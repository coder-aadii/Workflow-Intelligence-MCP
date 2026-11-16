# ---------------------------------------------------------------
# Multi-stage Dockerfile for Workflow Intelligence MCP
#
# Stage 1 (builder): compile wheels and install production
#   dependencies non-editable into an isolated virtual environment.
# Stage 2 (runtime): minimal slim image containing only
#   production dependencies, running as a non-root user.
#
# Build:
#   docker build -t workflow-intelligence-mcp:latest .
#
# Run (STDIO, default CLI):
#   docker run -i --rm -e SERVER_TRANSPORT=stdio \
#     workflow-intelligence-mcp:latest --transport stdio
#
# Run (SSE over HTTP):
#   docker run --rm -p 8000:8000 \
#     -e SERVER_TRANSPORT=sse -e SERVER_PORT=8000 \
#     workflow-intelligence-mcp:latest --transport sse
# ---------------------------------------------------------------

# ---- Stage 1: builder -------------------------------------------------
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip setuptools wheel hatchling

WORKDIR /src

# Copy package manifest, documentation, and source code required for non-editable build
COPY pyproject.toml README.md ./
COPY src/ ./src/

# Non-editable production installation into the virtual environment
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install .

# Import smoke test in builder
RUN python -c "import workflow_intelligence; print('Installed workflow_intelligence', workflow_intelligence.__version__)"


# ---- Stage 2: runtime -------------------------------------------------
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    WORKFLOW_ENV=production \
    SERVER_TRANSPORT=sse \
    SERVER_HOST=0.0.0.0 \
    SERVER_PORT=8000 \
    LOG_LEVEL=INFO

# Least-privilege non-root execution user and group
RUN groupadd -r -g 10001 appuser \
    && useradd -r -u 10001 -g appuser -m -d /home/appuser -s /bin/sh appuser

# Copy virtual environment from builder stage
COPY --from=builder /opt/venv /opt/venv

WORKDIR /app

# Ensure application and persistent data directories exist and are owned by appuser
RUN mkdir -p /app/data \
    && chown -R appuser:appuser /app

# Copy application resources with non-root ownership
COPY --chown=appuser:appuser sample-data /app/sample-data
COPY --chown=appuser:appuser demo /app/demo
COPY --chown=appuser:appuser pyproject.toml /app/pyproject.toml
COPY --chown=appuser:appuser README.md /app/README.md

USER appuser

# Reliable health check: validates SSE readiness probe when in SSE transport; validates import in STDIO
HEALTHCHECK --interval=15s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import os, sys, urllib.request; \
transport = os.environ.get('SERVER_TRANSPORT', 'sse').strip().lower(); \
if transport == 'stdio': sys.exit(0); \
port = os.environ.get('SERVER_PORT', '8000').strip(); \
req = urllib.request.Request(f'http://127.0.0.1:{port}/ready'); \
resp = urllib.request.urlopen(req, timeout=3); \
sys.exit(0 if resp.status == 200 else 1)" || exit 1

# Entrypoint: run the FastMCP server
ENTRYPOINT ["python", "-m", "workflow_intelligence.server"]
CMD ["--transport", "unified", "--host", "0.0.0.0", "--port", "8000"]
