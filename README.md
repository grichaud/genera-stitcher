# genera-stitcher

FFmpeg video stitcher for RunPod Serverless. Part of the GeneraContenido video pipeline.

## What it does

Combines scene video clips + audio into a final video using FFmpeg:
1. Downloads scene videos and audio files from URLs
2. For each scene: syncs video duration to audio (extends with last frame if needed)
3. Concatenates all scenes into one continuous video
4. Returns the final video as base64

## Stack

- Python 3.11
- FFmpeg
- RunPod Serverless SDK
- CPU only (no GPU needed)

## Input format

```json
{
  "input": {
    "scenes": [
      {
        "video_url": "https://...",
        "audio_url": "https://...",
        "duration_audio": 12.5
      }
    ]
  }
}
```

## Deploy

The GitHub Actions workflow automatically builds and pushes the Docker image to Docker Hub on every push to `main`. Then create a **CPU** serverless endpoint in RunPod pointing to the image.
