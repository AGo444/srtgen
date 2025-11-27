#!/bin/bash
# SRTGEN Update Script
# Run this after making code changes

echo "🔨 Building new SRTGEN image..."
docker build -t srtgen:latest /mnt/user/appdata/SRTGEN

if [ $? -eq 0 ]; then
    echo "✅ Build successful!"
    echo "⏹️  Stopping container via Unraid..."
    echo ""
    echo "👉 Now go to Unraid Docker tab and restart the srtgen container"
    echo "   (or run: docker restart srtgen)"
else
    echo "❌ Build failed!"
    exit 1
fi
