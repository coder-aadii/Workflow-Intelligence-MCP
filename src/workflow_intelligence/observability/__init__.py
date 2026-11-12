"""Observability package: structured logging and OpenTelemetry tracing."""

from workflow_intelligence.observability.logging import (  # noqa: F401
    configure_logging,
    get_logger,
    log_context,
)
from workflow_intelligence.observability.tracing import (  # noqa: F401
    SPAN_DUCKDB_QUERY,
    SPAN_MCP_TOOL_INVOKE,
    SPAN_PM4PY_DISCOVER_DFG,
    get_span_names,
    get_tracer,
    init_tracing,
    trace_span,
)

__all__ = [
    "configure_logging",
    "get_logger",
    "log_context",
    "init_tracing",
    "get_tracer",
    "trace_span",
    "get_span_names",
    "SPAN_MCP_TOOL_INVOKE",
    "SPAN_PM4PY_DISCOVER_DFG",
    "SPAN_DUCKDB_QUERY",
]
