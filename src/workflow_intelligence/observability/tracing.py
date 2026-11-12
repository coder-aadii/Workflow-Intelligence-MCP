"""OpenTelemetry tracing for the Workflow Intelligence MCP.

Initializes a :class:`opentelemetry.sdk.trace.TracerProvider` and provides:
- :func:`init_tracing`: one-time tracer provider setup.
- :func:`get_tracer`: retrieve the application tracer.
- :func:`trace_span`: decorator/context manager creating named spans.

Standard span names used across the codebase:

- ``mcp.tool.invoke``: root span for every MCP tool call.
- ``pm4py.discover_dfg``: process graph extraction.
- ``duckdb.query``: SQL analytical execution.

In development, spans are exported to the console via
:class:`SimpleSpanProcessor` + :class:`ConsoleSpanExporter`. In production,
wire an OTLP exporter through :func:`init_tracing`.
"""

from __future__ import annotations

import functools
import inspect
from collections.abc import Callable
from typing import Any, TypeVar, cast

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    ConsoleSpanExporter,
    SimpleSpanProcessor,
)

_TRACER_PROVIDER: TracerProvider | None = None

# Semantic convention span names.
SPAN_MCP_TOOL_INVOKE = "mcp.tool.invoke"
SPAN_PM4PY_DISCOVER_DFG = "pm4py.discover_dfg"
SPAN_DUCKDB_QUERY = "duckdb.query"

F = TypeVar("F", bound=Callable[..., Any])


def init_tracing(
    workflow_env: str = "development",
) -> TracerProvider:
    """Initialize the OpenTelemetry tracer provider (idempotent).

    Args:
        workflow_env: Application environment. Used to decide exporter
            behavior; development exports spans to stdout.

    Returns:
        The configured :class:`TracerProvider`.
    """
    global _TRACER_PROVIDER
    if _TRACER_PROVIDER is not None:
        return _TRACER_PROVIDER

    provider = TracerProvider()
    if workflow_env == "development":
        provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
    trace.set_tracer_provider(provider)
    _TRACER_PROVIDER = provider
    return provider


def get_tracer(name: str = "workflow-intelligence") -> trace.Tracer:
    """Return the OpenTelemetry tracer for ``name``."""
    return trace.get_tracer(name)


def trace_span(name: str) -> Callable[[F], F]:
    """Decorator creating an OpenTelemetry span around a function.

    Supports both ``async`` and ``sync`` functions. The span name is the
    given ``name``; execution context (correlation id, tool name) is not
    automatically attached -- call :func:`trace_span` inside a
    :func:`workflow_intelligence.observability.logging.log_context`
    block to correlate logs and traces.

    Args:
        name: Span name (e.g. ``"mcp.tool.invoke"``).

    Returns:
        A decorator that wraps the target function.
    """

    def decorator(func: F) -> F:
        if inspect.iscoroutinefunction(func):

            @functools.wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                tracer = get_tracer()
                with tracer.start_as_current_span(name):
                    return await func(*args, **kwargs)

            return cast(F, async_wrapper)

        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            tracer = get_tracer()
            with tracer.start_as_current_span(name):
                return func(*args, **kwargs)

        return cast(F, sync_wrapper)

    return decorator


def get_span_names() -> list[str]:
    """Return the standard span names used by this service."""
    return [SPAN_MCP_TOOL_INVOKE, SPAN_PM4PY_DISCOVER_DFG, SPAN_DUCKDB_QUERY]
