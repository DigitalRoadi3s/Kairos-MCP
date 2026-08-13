FROM python:3.11-alpine

# Install runtime dependencies (TLS, libffi for CalDAV)
RUN apk add --no-cache \
    ca-certificates \
    openssl \
    libffi-dev \
    gcc \
    musl-dev

# Create non-root user
RUN addgroup -g 1000 appuser && adduser -D -u 1000 -G appuser appuser

WORKDIR /app

# Copy requirements
COPY requirements.txt .

# Install Python dependencies (no cache to reduce image size)
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY src/ .

# Change ownership to appuser
RUN chown -R appuser:appuser /app

USER appuser

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import requests; requests.get('http://localhost:8080/health', timeout=5)" || exit 1

EXPOSE 8080

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
