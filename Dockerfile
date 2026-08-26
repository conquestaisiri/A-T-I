# ATI Trading Intelligence - Multi-stage Dockerfile

# ─── Base Stage ───
FROM python:3.13-slim-bookworm AS base

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    libpq-dev \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Set Python path
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# ─── Builder Stage ───
FROM base AS builder

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies (single source - requirements-all bundles runtime; dev excluded from prod)
COPY requirements*.txt ./
RUN pip install --no-cache-dir --upgrade pip setuptools wheel && \
    pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir -r requirements-venue.txt && \
    pip install --no-cache-dir -r requirements-data.txt && \
    pip install --no-cache-dir -r requirements-ai.txt && \
    pip install --no-cache-dir -r requirements-ml.txt && \
    pip install --no-cache-dir -r requirements-research.txt

# ─── Production Stage ───
FROM base AS production

# Copy installed packages from builder
COPY --from=builder /usr/local/lib/python3.13/site-packages /usr/local/lib/python3.13/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Create non-root user
RUN groupadd -r ati && useradd -r -g ati -d /app -s /bin/bash ati

# Copy application code
COPY --chown=ati:ati . /app

# Create directories
RUN mkdir -p /app/data /app/logs /app/monitoring && \
    chown -R ati:ati /app

# Switch to non-root user
USER ati

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request, sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health', timeout=5).status == 200 else 1)"

# Default command
CMD ["python", "-m", "backend.main", "--mode", "paper"]