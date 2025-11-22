#!/bin/bash
#
# Bazarr Post-Processing Script for SRTGEN
# 
# Place this script in your Bazarr's custom post-processing directory
# Configure in Bazarr: Settings > General > Post-Processing > Custom Post-Processing
#
# Bazarr Environment Variables:
# - sonarr_episodefile_path: Full path to the video file
# - sonarr_series_path: Series directory
# - sonarr_episodefile_scenename: Episode filename
# - radarr_moviefile_path: Full path to movie file (for movies)
#

# Configuration
SRTGEN_CONTAINER="srtgen"
TARGET_LANGUAGE="${SRTGEN_TARGET_LANG:-nl}"  # Default to Dutch, override with env var
WHISPER_MODEL="${SRTGEN_MODEL:-base}"        # Default to base model
OVERWRITE="${SRTGEN_OVERWRITE:-false}"       # Default to not overwrite

# Logging
LOG_DIR="/tmp/srtgen-bazarr"
mkdir -p "$LOG_DIR"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
LOG_FILE="$LOG_DIR/bazarr_${TIMESTAMP}.log"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

log "========================================="
log "SRTGEN Bazarr Post-Processing Started"
log "========================================="

# Determine video file path (supports both Sonarr and Radarr)
if [ -n "$sonarr_episodefile_path" ]; then
    VIDEO_FILE="$sonarr_episodefile_path"
    log "Type: TV Episode"
elif [ -n "$radarr_moviefile_path" ]; then
    VIDEO_FILE="$radarr_moviefile_path"
    log "Type: Movie"
else
    log "ERROR: No video file path provided by Bazarr"
    log "Available environment variables:"
    env | grep -E "(sonarr_|radarr_)" | tee -a "$LOG_FILE"
    exit 1
fi

log "Video file: $VIDEO_FILE"
log "Target language: $TARGET_LANGUAGE"
log "Whisper model: $WHISPER_MODEL"
log "Overwrite: $OVERWRITE"

# Check if video file exists
if [ ! -f "$VIDEO_FILE" ]; then
    log "ERROR: Video file not found: $VIDEO_FILE"
    exit 1
fi

# Check if file is MKV (SRTGEN only supports MKV)
if [[ ! "$VIDEO_FILE" =~ \.mkv$ ]]; then
    log "WARNING: File is not MKV format. SRTGEN only processes MKV files."
    log "Skipping transcription."
    exit 0
fi

# Build docker exec command
DOCKER_CMD="docker exec $SRTGEN_CONTAINER python3 /app/mkv_transcribe.py"
DOCKER_CMD="$DOCKER_CMD \"$VIDEO_FILE\""
DOCKER_CMD="$DOCKER_CMD -l $TARGET_LANGUAGE"
DOCKER_CMD="$DOCKER_CMD --model $WHISPER_MODEL"

if [ "$OVERWRITE" = "true" ]; then
    DOCKER_CMD="$DOCKER_CMD --overwrite"
fi

log "Executing: $DOCKER_CMD"
log "========================================="

# Execute transcription
if eval "$DOCKER_CMD" 2>&1 | tee -a "$LOG_FILE"; then
    log "========================================="
    log "✓ SRTGEN transcription completed successfully"
    
    # List generated SRT files
    VIDEO_DIR=$(dirname "$VIDEO_FILE")
    VIDEO_BASE=$(basename "$VIDEO_FILE" .mkv)
    log "Generated SRT files:"
    ls -lh "${VIDEO_DIR}/${VIDEO_BASE}"*.srt 2>/dev/null | tee -a "$LOG_FILE"
    
    exit 0
else
    EXIT_CODE=$?
    log "========================================="
    log "✗ SRTGEN transcription failed with exit code: $EXIT_CODE"
    exit $EXIT_CODE
fi
