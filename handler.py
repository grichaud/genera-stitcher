"""
RunPod Serverless Handler: Video Stitcher
Combines scene video clips + audio into a final video using FFmpeg.
Optionally mixes background music underneath dialogue.
Uploads the result directly to Supabase Storage via signed URL.

Input:
{
  "scenes": [
    {
      "video_url": "https://...",
      "audio_url": "https://...",
      "duration_audio": 12.5
    }
  ],
  "background_music_url": "https://...",     // optional
  "background_music_volume": -18,            // dB, optional (default: -18)
  "upload_url": "https://...supabase.co/storage/v1/object/upload/sign/...",
  "upload_token": "...",
  "public_url": "https://...supabase.co/storage/v1/object/public/..."
}

Output:
{
  "video_url": "https://...public url...",
  "duration": 92.5,
  "file_size_bytes": 12345678
}
"""

import runpod
import subprocess
import os
import json
import time
import requests

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
        return 5.0


def process_scene(scene_index, video_path, audio_path, audio_duration, keep_embedded_audio=False):
    """Process a single scene: combine video + audio, matching durations."""
    output_path = os.path.join(WORK_DIR, f"scene_{scene_index:03d}_final.mp4")

    video_duration = get_duration(video_path)
    print(f"  Scene {scene_index}: video={video_duration:.1f}s, audio={audio_duration:.1f}s, embedded={keep_embedded_audio}")

    if keep_embedded_audio:
        # Video already has audio baked in (e.g. VEED Fabric lip-sync) — re-encode but keep audio
        subprocess.run([
            "ffmpeg", "-y",
            "-i", video_path,
            "-c:v", "libx264", "-preset", "fast",
            "-pix_fmt", "yuv420p",
            "-r", "24",
            "-c:a", "aac", "-b:a", "128k",
            output_path
        ], check=True, capture_output=True)
    elif audio_path and audio_duration > 0:
        if audio_duration > video_duration + 0.5:
            pad_duration = audio_duration - video_duration + 0.5
            padded_path = os.path.join(WORK_DIR, f"scene_{scene_index:03d}_padded.mp4")

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

            subprocess.run([
                "ffmpeg", "-y",
                "-i", padded_path,
                "-i", audio_path,
                "-c:v", "copy",
                "-c:a", "aac", "-b:a", "128k",
                "-shortest",
                output_path
            ], check=True, capture_output=True)

            os.remove(padded_path)
        else:
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


def mix_background_music(video_path, music_path, volume_db=-18):
    """Mix background music under the video's existing audio track.

    Uses FFmpeg amerge to layer music underneath dialogue at reduced volume.
    Music loops if shorter than video, and fades out in the last 3 seconds.
    """
    output_path = os.path.join(WORK_DIR, "final_with_music.mp4")
    video_duration = get_duration(video_path)
    fade_start = max(0, video_duration - 3)

    # Filter chain:
    # 1. Loop music to cover full video duration
    # 2. Apply volume reduction (e.g. -18dB) so it's subtle behind dialogue
    # 3. Fade out music in last 3 seconds
    # 4. Mix with original video audio (amerge → pan to stereo)
    subprocess.run([
        "ffmpeg", "-y",
        "-i", video_path,
        "-stream_loop", "-1", "-i", music_path,
        "-filter_complex",
        f"[1:a]volume={volume_db}dB,afade=t=out:st={fade_start}:d=3[music];"
        f"[0:a][music]amix=inputs=2:duration=first:dropout_transition=2[aout]",
        "-map", "0:v",
        "-map", "[aout]",
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "128k",
        "-shortest",
        output_path
    ], check=True, capture_output=True)

    print(f"  Mixed background music at {volume_db}dB, fade out at {fade_start:.1f}s")
    return output_path


def upload_to_storage(filepath, upload_url, upload_token):
    """Upload the final video to Supabase Storage via signed URL."""
    file_size = os.path.getsize(filepath)
    print(f"  Uploading {file_size / (1024*1024):.1f}MB to Supabase Storage...")

    with open(filepath, "rb") as f:
        response = requests.put(
            upload_url,
            headers={
                "Authorization": f"Bearer {upload_token}",
                "Content-Type": "video/mp4",
            },
            data=f,
            timeout=300,
        )

    if response.status_code >= 400:
        print(f"  Upload error: {response.status_code} - {response.text[:300]}")
        raise Exception(f"Upload failed: {response.status_code}")

    print(f"  Upload complete!")
    return True


def reencode_clip(video_url, upload_url, upload_token, public_url):
    """Re-encode a single video clip to H264 High yuv420p for browser compatibility.
    Wan 2.2 outputs yuv444p which many browsers cannot play.
    """
    os.makedirs(WORK_DIR, exist_ok=True)
    input_path = os.path.join(WORK_DIR, "reencode_input.mp4")
    output_path = os.path.join(WORK_DIR, "reencode_output.mp4")

    download_file(video_url, input_path)

    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=pix_fmt",
         "-of", "csv=p=0", input_path],
        capture_output=True, text=True
    )
    pix_fmt = result.stdout.strip().split("\n")[0]
    print(f"  Pixel format: {pix_fmt}")

    if pix_fmt == "yuv420p":
        print("  Already compatible, uploading as-is...")
        upload_to_storage(input_path, upload_url, upload_token)
        duration = get_duration(input_path)
        file_size = os.path.getsize(input_path)
    else:
        print(f"  Re-encoding {pix_fmt} -> yuv420p...")
        subprocess.run([
            "ffmpeg", "-y", "-i", input_path,
            "-c:v", "libx264", "-profile:v", "high",
            "-pix_fmt", "yuv420p", "-preset", "fast",
            "-movflags", "+faststart",
            output_path
        ], check=True, capture_output=True)
        upload_to_storage(output_path, upload_url, upload_token)
        duration = get_duration(output_path)
        file_size = os.path.getsize(output_path)

    import shutil
    shutil.rmtree(WORK_DIR, ignore_errors=True)

    return {
        "video_url": public_url,
        "duration": round(duration, 1),
        "file_size_bytes": file_size,
    }


def handler(event):
    """Main RunPod handler.
    Supports actions: stitch (default), reencode (single clip codec fix).
    """
    start_time = time.time()
    input_data = event["input"]
    action = input_data.get("action", "stitch")

    if action == "reencode":
        video_url = input_data.get("video_url")
        upload_url = input_data.get("upload_url")
        upload_token = input_data.get("upload_token")
        public_url = input_data.get("public_url")
        if not video_url or not upload_url or not upload_token:
            return {"error": "video_url, upload_url, upload_token required"}
        try:
            result = reencode_clip(video_url, upload_url, upload_token, public_url)
            result["processing_time"] = round(time.time() - start_time, 1)
            return result
        except Exception as e:
            return {"error": f"Reencode failed: {str(e)[:200]}"}

    scenes = input_data.get("scenes", [])
    background_music_url = input_data.get("background_music_url")
    background_music_volume = input_data.get("background_music_volume", -18)
    upload_url = input_data.get("upload_url")
    upload_token = input_data.get("upload_token")
    public_url = input_data.get("public_url")

    if not scenes:
        return {"error": "No scenes provided"}

    # upload_url/upload_token are optional — if not provided or upload fails,
    # video is returned as base64 in the output for server-side upload.

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

    # Download background music if provided
    music_path = None
    if background_music_url:
        music_path = os.path.join(WORK_DIR, "background_music.mp3")
        try:
            download_file(background_music_url, music_path)
            print(f"  Background music downloaded ({background_music_volume}dB)")
        except Exception as e:
            print(f"  WARNING: Failed to download background music: {e}")
            music_path = None

    # Step 2: Process each scene
    print("\n--- Step 2: Processing scenes ---")
    scene_files = []
    for i, scene in enumerate(scenes):
        video_path = os.path.join(WORK_DIR, f"scene_{i:03d}_video.webp")
        audio_path = os.path.join(WORK_DIR, f"scene_{i:03d}_audio.mp3") if scene.get("audio_url") else None
        audio_duration = scene.get("duration_audio", 0)
        keep_embedded = scene.get("keep_embedded_audio", False)

        try:
            output = process_scene(i, video_path, audio_path, audio_duration, keep_embedded)
            scene_files.append(output)
        except subprocess.CalledProcessError as e:
            stderr = e.stderr.decode() if e.stderr else str(e)
            print(f"  ERROR processing scene {i}: {stderr[:500]}")
            return {"error": f"FFmpeg error on scene {i}: {stderr[:200]}"}

    # Step 3: Concatenate
    print("\n--- Step 3: Concatenating ---")
    try:
        final_path = concatenate_scenes(scene_files)
    except subprocess.CalledProcessError as e:
        stderr = e.stderr.decode() if e.stderr else str(e)
        print(f"  ERROR concatenating: {stderr[:500]}")
        return {"error": f"FFmpeg concat error: {stderr[:200]}"}

    # Step 3.5: Mix background music (if available)
    if music_path and os.path.exists(music_path):
        print("\n--- Step 3.5: Mixing background music ---")
        try:
            final_path = mix_background_music(final_path, music_path, background_music_volume)
        except subprocess.CalledProcessError as e:
            stderr = e.stderr.decode() if e.stderr else str(e)
            print(f"  WARNING: Music mixing failed, using video without music: {stderr[:300]}")
            # Non-fatal: continue with the concatenated video without music

    file_size = os.path.getsize(final_path)
    final_duration = get_duration(final_path)

    # Step 4: Try uploading to Supabase Storage, fallback to base64 output
    uploaded = False
    if upload_url and upload_token:
        print("\n--- Step 4: Uploading to storage ---")
        try:
            upload_to_storage(final_path, upload_url, upload_token)
            uploaded = True
        except Exception as e:
            print(f"  Upload failed ({e}), falling back to base64 output")

    elapsed = time.time() - start_time

    result = {
        "duration": round(final_duration, 1),
        "file_size_bytes": file_size,
        "processing_time": round(elapsed, 1),
    }

    if uploaded:
        result["video_url"] = public_url
    else:
        # Return video as base64 so the caller can upload it server-side
        import base64
        with open(final_path, "rb") as f:
            result["video_base64"] = base64.b64encode(f.read()).decode("ascii")
        print(f"  Returning video as base64 ({file_size / (1024*1024):.1f}MB)")

    # Cleanup
    import shutil
    shutil.rmtree(WORK_DIR, ignore_errors=True)

    print(f"\n=== Done! Duration: {final_duration:.1f}s, Size: {file_size / (1024*1024):.1f}MB, Elapsed: {elapsed:.1f}s ===")

    return result


runpod.serverless.start({"handler": handler})
