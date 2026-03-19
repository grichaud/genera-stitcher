# =============================================================================
# GeneraContenido - Video Stitcher
# RunPod Serverless Worker (CPU only - no GPU needed)
#
# Combines scene video clips + audio into a final video using FFmpeg.
# Lightweight: ~200MB image vs ~15GB for the video generation image.
#
# Build:  docker build -t <tu-usuario>/genera-stitcher:latest .
# Push:   docker push <tu-usuario>/genera-stitcher:latest
# =============================================================================

FROM python:3.11-slim

# Install FFmpeg
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Copy handler
COPY handler.py /app/handler.py

WORKDIR /app

CMD ["python", "-u", "handler.py"]
