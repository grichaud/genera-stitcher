"""
RunPod Serverless Handler: Video Stitcher
Combines scene video clips + audio into a final video using FFmpeg.

Input:
{
  "scenes": [
    {
      "video_url": "https://...",      # Scene video clip (5s webp/mp4)
      "audio_url": "https://...",      # Scene audio (mp3), null if visual-only
      "duration_audio": 12.5           # Audio duration in seconds (0 if no audio)
    },
    ...
  ],
  "transition": "none"                 # Future: "crossfade", "fade", etc.
}

Output:
{
  "video_base64": "...",               # Final video as base64
  "mime_type": "video/mp4",
  "duration": 92.5,                    # Total duration in seconds
  "file_size_bytes": 12345678
}
"""

import runpod
import subprocess
import os
import base64
import requests
import json
import time

WORK_DIR = "/tmp/stitch"


def download_file(url, filepath):
    """Download a file from URL to local path."""
    print(f"  Downloading: {url[:80]}...")
    response = requests.get(url, timeout=120)
    response.raise_for_status()
    with open(filepath, "wb") as f:
        f.write(response.content)
    size_mb = os.path.getsize(filepath) / (1024 * 1024)
    print(f"  Downloaded: {filepath} ({size_mb:.1f}MB)")


def get_duration(filepath):
    """Get media duration in seconds using ffprobe."""
    result = subprocess.run(
        [
            "ffprobe", "-v", "quiet",
            "-show_entries", "format=duration",
            "-of", "json",
            filepath
        ],
        capture_output=True, text=True
    )
    try:
        data = json.loads(result.stdout)
        return float(data["format"]["duration"])
    except (KeyError, ValueError, json.JSONDecodeError):
        return 5.0  # Default fallback


def process_scene(scene_index, video_path, audio_path, audio_duration):
    """Process a single scene: combine video + audio, matching durations."""
    output_path = os.path.join(WORK_DIR, f"scene_{scene_index:03d}_final.mp4")

    video_duration = get_duration(video_path)
    print(f"  Scene {scene_index}: video={video_duration:.1f}s, audio={audio_duration:.1f}s")

    if audio_path and audio_duration > 0:
        if audio_duration > video_duration + 0.5:
            # Audio is longer: extend video by holding last frame
            pad_duration = audio_duration - video_duration + 0.5  # Small buffer
            padded_path = os.path.join(WORK_DIR, f"scene_{scene_index:03d}_padded.mp4")

            # Step 1: Pad video with last frame
            subprocess.run([
                "ffmpeg", "-y",
                "-i", video_path,
                "-vf", f"tpad=stop_mode=clone:stop_duration={pad_duration}",
                "-c:v", "libx264", "-preset", "fast",
                "-pix_fmt", "yuv420p",
                "-r", "24",
                "-an",
                padded_path
            ], check=True, capture_output=True)

            # Step 2: Combine padded video + audio
            subprocess.run([
                "ffmpeg", "-y",
                "-i", padded_path,
                "-i", audio_path,
                "-c:v", "copy",
                "-c:a", "aac", "-b:a", "128k",
                "-shortest",
                output_path
            ], check=True, capture_output=True)

            # Cleanup intermediate file
            os.remove(padded_path)
        else:
            # Video is same length or longer: just combine
            subprocess.run([
                "ffmpeg", "-y",
                "-i", video_path,
                "-i", audio_path,
                "-c:v", "libx264", "-preset", "fast",
                "-pix_fmt", "yuv420p",
                "-r", "24",
                "-c:a", "aac", "-b:a", "128k",
                "-shortest",
                output_path
            ], check=True, capture_output=True)
    else:
        # No audio: just convert video to standard format
        subprocess.run([
            "ffmpeg", "-y",
            "-i", video_path,
            "-c:v", "libx264", "-preset", "fast",
            "-pix_fmt", "yuv420p",
            "-r", "24",
            "-an",
            output_path
        ], check=True, capture_output=True)

    final_duration = get_duration(output_path)
    print(f"  Scene {scene_index} done: {final_duration:.1f}s")
    return output_path


def concatenate_scenes(scene_files):
    """Concatenate all processed scene videos into one."""
    concat_list = os.path.join(WORK_DIR, "concat.txt")
    with open(concat_list, "w") as f:
        for scene_file in scene_files:
            f.write(f"file '{scene_file}'\n")

    output_path = os.path.join(WORK_DIR, "final_video.mp4")

    subprocess.run([
        "ffmpeg", "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", concat_list,
        "-c:v", "libx264", "-preset", "fast",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        output_path
    ], check=True, capture_output=True)

    return output_path


def handler(event):
    """Main RunPod handler."""
    start_time = time.time()
    input_data = event["input"]
    scenes = input_data.get("scenes", [])

    if not scenes:
        return {"error": "No scenes provided"}

    # Create work directory
    os.makedirs(WORK_DIR, exist_ok=True)

    print(f"=== Stitching {len(scenes)} scenes ===")

    # Step 1: Download all files
    print("\n--- Step 1: Downloading files ---")
    for i, scene in enumerate(scenes):
        video_url = scene.get("video_url")
        audio_url = scene.get("audio_url")

        if not video_url:
            return {"error": f"Scene {i} has no video_url"}

        download_file(video_url, os.path.join(WORK_DIR, f"scene_{i:03d}_video.webp"))

        if audio_url:
            download_file(audio_url, os.path.join(WORK_DIR, f"scene_{i:03d}_audio.mp3"))

    # Step 2: Process each scene (combine video + audio)
    print("\n--- Step 2: Processing scenes ---")
    scene_files = []
    for i, scene in enumerate(scenes):
        video_path = os.path.join(WORK_DIR, f"scene_{i:03d}_video.webp")
        audio_path = os.path.join(WORK_DIR, f"scene_{i:03d}_audio.mp3") if scene.get("audio_url") else None
        audio_duration = scene.get("duration_audio", 0)

        try:
            output = process_scene(i, video_path, audio_path, audio_duration)
            scene_files.append(output)
        except subprocess.CalledProcessError as e:
            print(f"  ERROR processing scene {i}: {e.stderr[:500] if e.stderr else str(e)}")
            return {"error": f"FFmpeg error on scene {i}: {str(e)[:200]}"}

    # Step 3: Concatenate all scenes
    print("\n--- Step 3: Concatenating ---")
    try:
        final_path = concatenate_scenes(scene_files)
    except subprocess.CalledProcessError as e:
        print(f"  ERROR concatenating: {e.stderr[:500] if e.stderr else str(e)}")
        return {"error": f"FFmpeg concat error: {str(e)[:200]}"}

    # Step 4: Read result and encode as base64
    print("\n--- Step 4: Encoding result ---")
    file_size = os.path.getsize(final_path)
    final_duration = get_duration(final_path)

    with open(final_path, "rb") as f:
        video_base64 = base64.b64encode(f.read()).decode("utf-8")

    # Cleanup
    import shutil
    shutil.rmtree(WORK_DIR, ignore_errors=True)

    elapsed = time.time() - start_time
    print(f"\n=== Done! Duration: {final_duration:.1f}s, Size: {file_size / (1024*1024):.1f}MB, Elapsed: {elapsed:.1f}s ===")

    return {
        "video_base64": video_base64,
        "mime_type": "video/mp4",
        "duration": round(final_duration, 1),
        "file_size_bytes": file_size,
        "processing_time": round(elapsed, 1),
    }


runpod.serverless.start({"handler": handler})
