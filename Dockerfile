FROM registry.access.redhat.com/ubi9/python-311:latest

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Switch to root for system package installation
USER 0
RUN dnf install -y postgresql-devel gcc python3-devel \
    && dnf clean all \
    && rm -rf /var/cache/dnf

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
    CMD python -c "import requests; requests.get('http://localhost:8000/health')" || exit 1

CMD ["python", "main.py"]