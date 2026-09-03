FROM registry.access.redhat.com/ubi9/python-311:latest

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Switch to root for system package installation
USER 0
RUN dnf install -y postgresql-devel gcc python3-devel xz \
    && dnf clean all \
    && rm -rf /var/cache/dnf

# Install static ffmpeg binary (works on both amd64 and aarch64)
RUN ARCH=$(uname -m) && \
    if [ "$ARCH" = "aarch64" ]; then \
        curl -L https://github.com/yt-dlp/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-linuxarm64-gpl.tar.xz -o /tmp/ffmpeg.tar.xz; \
    else \
        curl -L https://github.com/yt-dlp/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-linux64-gpl.tar.xz -o /tmp/ffmpeg.tar.xz; \
    fi && \
    tar -xf /tmp/ffmpeg.tar.xz -C /tmp && \
    cp /tmp/ffmpeg-*/bin/ffmpeg /usr/local/bin/ffmpeg && \
    chmod +x /usr/local/bin/ffmpeg && \
    rm -rf /tmp/ffmpeg*

# Create app directory and set permissions
WORKDIR /app
RUN chown -R 1001:1001 /app

# Switch to non-root user
USER 1001

# Install Python dependencies
COPY --chown=1001:1001 requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY --chown=1001:1001 . .

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import http.client; c = http.client.HTTPConnection('localhost', 8081); c.request('GET', '/'); r = c.getresponse(); exit(0 if r.status in (200, 401) else 1)" || exit 1

CMD ["python", "main.py"]