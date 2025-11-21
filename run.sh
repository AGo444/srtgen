#!/bin/bash

# SRTGEN - Docker Run Script for Unraid
# Usage: ./run.sh /path/to/video.mkv

if [ -z "$1" ]; then
    echo "Usage: $0 <path-to-mkv-file> [options]"
    echo ""
    echo "Example:"
    echo "  $0 /mnt/user/media/movies/video.mkv"
    echo "  $0 /mnt/user/media/movies/video.mkv -l nl-NL"
    echo ""
    exit 1
fi

VIDEO_FILE="$1"
shift  # Remove first argument, keep the rest as options

# Extract directory and filename
VIDEO_DIR=$(dirname "$VIDEO_FILE")
VIDEO_NAME=$(basename "$VIDEO_FILE")

# Build the Docker image if it doesn't exist
if [[ "$(docker images -q srtgen:latest 2> /dev/null)" == "" ]]; then
    echo "Building Docker image..."
    docker build -t srtgen:latest /mnt/user/appdata/SRTGEN
fi

# Run the transcription
echo "Transcribing: $VIDEO_NAME"
docker run --rm \
    -v "$VIDEO_DIR:/media" \
    srtgen:latest \
    "/media/$VIDEO_NAME" \
    -o "/media/${VIDEO_NAME%.*}.srt" \
    "$@"

echo "Done! SRT subtitles saved to: ${VIDEO_FILE%.*}.srt"
