# ===========================================
# STAGE 1: Build dependencies
# ===========================================
FROM python:3.11.17-slim-trixie@sha256:e88e9763f943ec1834f992a4b51e0f24500486803e8bc534e5767af9ea65f6ce AS builder

# Install system dependencies for building
RUN apt-get update && apt-get upgrade -y && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements files
COPY requirements.txt ./

# Create virtual environment and install dependencies
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Install Python dependencies
RUN pip install --no-cache-dir --upgrade pip setuptools wheel && \
    pip install --no-cache-dir -r requirements.txt

# ===========================================
# STAGE 2: Runtime image
# ===========================================
FROM python:3.11.17-slim-trixie@sha256:e88e9763f943ec1834f992a4b51e0f24500486803e8bc534e5767af9ea65f6ce AS runtime

# Add metadata labels for traceability
LABEL maintainer="PAL MCP Server Team"
LABEL version="1.3.0"
LABEL description="PAL MCP Server - AI-powered Model Context Protocol server"
LABEL org.opencontainers.image.title="pal-mcp-server"
LABEL org.opencontainers.image.description="AI-powered Model Context Protocol server with multi-provider support"
LABEL org.opencontainers.image.version="1.3.0"
LABEL org.opencontainers.image.source="https://github.com/serpro69/pal-mcp-server"
LABEL org.opencontainers.image.documentation="https://github.com/serpro69/pal-mcp-server/blob/main/README.md"
LABEL org.opencontainers.image.licenses="Apache-2.0"

# Create non-root user for security
RUN groupadd -r paluser && useradd -r -g paluser paluser

# Install minimal runtime dependencies
RUN apt-get update && apt-get upgrade -y && apt-get install -y --no-install-recommends \
    ca-certificates \
    procps \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Refresh the base interpreter's packaging tools too; updating /opt/venv alone
# leaves older, vulnerable copies under /usr/local in the runtime image.
RUN python -m pip install --no-cache-dir --upgrade pip setuptools wheel

# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Set working directory
WORKDIR /app

# Copy application code
COPY --chown=paluser:paluser . .

# External state works with a read-only application directory.
ENV PAL_STATE_DIR=/var/lib/pal
RUN mkdir -p /var/lib/pal/logs && chown -R paluser:paluser /var/lib/pal

# Create tmp directory for container operations
RUN mkdir -p tmp && chown -R paluser:paluser tmp

# Copy health check script
COPY --chown=paluser:paluser docker/scripts/healthcheck.py /usr/local/bin/healthcheck.py
RUN chmod +x /usr/local/bin/healthcheck.py

# Switch to non-root user
USER paluser

# Health check configuration
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python /usr/local/bin/healthcheck.py

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

# Default command
CMD ["python", "server.py"]
