# NEXUS Autonomous AI Development System
# Multi-stage build for production deployment

FROM python:3.11-slim AS base

# System dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir fastapi uvicorn pydantic

# Copy application code
COPY src/ src/
COPY scripts/ scripts/
COPY monitor/ monitor/
COPY docs/ docs/
COPY run_system.py .

# Create necessary directories
RUN mkdir -p data/brain data/state data/memory data/experiments \
    logs screenshots/automation workspace

# Non-root user for security
RUN groupadd -r nexus && useradd -r -g nexus -d /app nexus \
    && chown -R nexus:nexus /app
USER nexus

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -sf http://localhost:8766/api/nexus/health || exit 1

# Default: run brain API
EXPOSE 8766 8767 5111
CMD ["python3", "-m", "uvicorn", "src.api.brain_api:app", "--host", "0.0.0.0", "--port", "8766"]

# ── Monitor service target ──────────────────────────────────────
FROM base AS monitor

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -sf http://localhost:5111/ || exit 1

EXPOSE 5111
CMD ["python3", "monitor/app.py"]

# ── Brain Daemon target ─────────────────────────────────────────
FROM base AS daemon

CMD ["python3", "scripts/brain_daemon.py", "start"]

# ── Full system target ──────────────────────────────────────────
FROM base AS full

EXPOSE 8766 8767 5111
CMD ["python3", "run_system.py"]
