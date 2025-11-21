FROM python:3.11-slim

# Install FFmpeg and system dependencies
RUN apt-get update && apt-get install -y \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY mkv_transcribe.py .
COPY app.py .
COPY templates/ templates/
COPY static/ static/

# Create directories for input/output
RUN mkdir -p /media /output

# Expose web UI port
EXPOSE 5000

# Set environment variables
ENV FLASK_APP=app.py
ENV MEDIA_FOLDER=/media

# Run the web application
CMD ["python", "app.py"]
