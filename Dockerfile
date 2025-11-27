FROM python:3.11-slim

# Update system packages to patch security vulnerabilities
RUN apt-get update && apt-get upgrade -y && apt-get install -y \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Upgrade pip to latest version to patch CVE
RUN pip install --no-cache-dir --upgrade pip>=25.3

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

# Health check to verify Flask is running
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:5000/api/jobs').read()" || exit 1

# Set environment variables
ENV FLASK_APP=app.py
ENV MEDIA_FOLDER=/media
ENV PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True

# Run the web application with Gunicorn
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "1", "--threads", "4", "--timeout", "300", "--access-logfile", "-", "--error-logfile", "-", "app:app"]
