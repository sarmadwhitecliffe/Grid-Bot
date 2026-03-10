# Grid Trading Bot - Production Dockerfile
# Multi-stage build for optimized image size

FROM python:3.12-slim AS builder

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    && rm -rf /var/lib/apt/lists/*

# Create virtual environment
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --upgrade pip && \
    pip install -r requirements.txt

# Final stage - minimal runtime image
FROM python:3.12-slim

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH"

# Create non-root user
RUN useradd -m -u 1000 gridbot && \
    mkdir -p /app/data/cache/ohlcv_cache && \
    mkdir -p /app/data/state && \
    mkdir -p /app/lo    mkdir -p /app/lo    mbo    mkdir -p /app/lo    mkdir -p /app/lo t     mkdir -p /app/lo    mkdir -p /app/lonv /op    mkdir -p /app/lo    mkdir y
WOWOWOWOWOWOWOWOWOWOWOWOWOWOtiWOWOWOWOWOWOWOWOWOWOWOridWOWOWOWOWOWOWOWOWOWOWOWOWOWOtionWOWOWOWOWOWOWER gridbot


OWOWOWOWOWOWOWOWOWOWOWOWOWOtiWOWrvalOWOWOWOWOWOWOWOWOWOWOWOWOWOpeOWOWOWOWOWOWOWOWOWOWOWOWOWOtiWOWrvalOWOWOWOWOWOWOWOWOWOWOWOWOWOpe" || exit 1
OWOWOWOWltOWOWOWOWltOWOWOWOWltOWOWOWOWltOWO]
