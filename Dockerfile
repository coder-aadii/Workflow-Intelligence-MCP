# ---------------------------------------------------------------
# Multi-stage Dockerfile for Workflow Intelligence MCP (v1.0.0)
#
# Stage 1 (builder): compile wheels and install production + dev
#   dependencies into an isolated target directory.
# Stage 2 (runtime): minimal distroless/slim image containing only
#   production dependencies, running as a non-root user.
#
# Build:
#   docker build -t workflow-intelligence-mcp:latest .
#
# Run (STDIO, default):
#   docker run --rm workflow-intelligence-mcp:latest
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

# Install build tooling + hatchling for wheel compilation.
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip setuptools wheel hatchling

WORKDIR /src

# Copy dependency manifest first to leverage Docker layer caching.
COPY pyproject.toml ./

# Install production + dev dependencies into the venv.
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -e ".[dev]"

# Copy the rest of the source.
COPY . /src

# Run a lightweight import smoke test to catch missing modules early.
RUN python -c "import workflow_intelligence; print('import ok', workflow_intelligence.__version__)"


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

# Non-root user for least-privilege execution.
RUN groupadd -r appuser && useradd -r -g appuser -m -s /bin/sh appuser

# Copy the virtualenv (with installed packages) from the builder stage.
COPY --from=builder /opt/venv /opt/venv

# Copy only the application source (not tests, docs, local, etc.).
WORKDIR /app
COPY --from=builder /src/src /app/src
COPY --from=builder /src/pyproject.toml /app/pyproject.toml
COPY --from=builder /src/README.md /app/README.md
COPY --from=builder /src/sample-data /app/sample-data
COPY --from=builder /src/demo /app/demo

# Healthcheck: verify the server module imports and responds over STDIO.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import workflow_intelligence.server; print('healthy')" \
        || exit 1

USER appuser

# Entrypoint: run the MCP server over SSE by default in containers.
ENTRYPOINT ["python", "-m", "workflow_intelligence.server"]
CMD ["--transport", "sse", "--host", "0.0.0.0", "--port", "8000"]
