"""Structured logging configuration for the Workflow Intelligence MCP.

Configures :mod:`structlog` with:
- JSON key-value renderer for production environments.
- Colored console renderer for local development.
- Context injection (``correlation_id``, ``tool_name``, ``case_id``,
  ``duration_ms``) carried on every log line via context vars.

Usage::

    from workflow_intelligence.observability.logging import (
        configure_logging, get_logger, log_context,
    )

    configure_logging(workflow_env="production", log_level="INFO")
    logger = get_logger("workflow_intelligence.tools.decision_intelligence")

    with log_context(correlation_id="corr-123", tool_name="decision_generate_brief"):
        logger.info("brief generated", metric="processing_cost")
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, cast

import structlog


def configure_logging(
    workflow_env: str = "development",
    log_level: str = "INFO",
) -> None:
    """Configure structlog for the application.

    Args:
        workflow_env: Application environment. ``"production"`` uses a JSON
            renderer; anything else uses a colored console renderer.
        log_level: Minimum log level (``DEBUG``, ``INFO``, ``WARNING``,
            ``ERROR``, ``CRITICAL``).
    """
    min_level = getattr(logging, log_level.upper(), logging.INFO)

    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
    ]

    if workflow_env == "production":
        renderer: structlog.types.Processor = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    structlog.configure(
        processors=[
            *shared_processors,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(min_level),
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Return a configured structlog bound logger for ``name``."""
    return cast(structlog.stdlib.BoundLogger, structlog.get_logger(name))


@contextmanager
def log_context(
    correlation_id: str | None = None,
    tool_name: str | None = None,
    case_id: str | None = None,
    duration_ms: float | None = None,
    **extra: Any,
) -> Iterator[None]:
    """Bind request-scoped context to structlog contextvars.

    All fields are optional; only provided values are bound. Bound context
    is cleared when the context manager exits.

    Args:
        correlation_id: Distributed trace / request identifier.
        tool_name: Name of the MCP tool being invoked.
        case_id: Associated workflow case identifier, if applicable.
        duration_ms: Duration of the operation in milliseconds.
        extra: Additional key-value context to bind.
    """
    bound: dict[str, Any] = {}
    if correlation_id is not None:
        bound["correlation_id"] = correlation_id
    if tool_name is not None:
        bound["tool_name"] = tool_name
    if case_id is not None:
        bound["case_id"] = case_id
    if duration_ms is not None:
        bound["duration_ms"] = duration_ms
    bound.update(extra)
    structlog.contextvars.bind_contextvars(**bound)
    try:
        yield
    finally:
        structlog.contextvars.clear_contextvars()
