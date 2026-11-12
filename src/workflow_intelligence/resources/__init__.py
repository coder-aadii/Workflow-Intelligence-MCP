"""MCP Resources package."""

from pathlib import Path
from typing import Any

from workflow_intelligence.resources.decision_resources import (  # noqa: F401
    register_decision_resources,
)
from workflow_intelligence.resources.exception_resources import (  # noqa: F401
    register_exception_resources,
)
from workflow_intelligence.resources.process_resources import (  # noqa: F401
    register_process_resources,
)


def register_resources(
    mcp: Any,
    process_service: Any,
    default_source_uri: str,
    data_dir: str | Path | None = None,
) -> None:
    """Register all MCP Resources with the FastMCP server.

    Registers:

    - ``workflow://process-maps/{process_name}``
    - ``workflow://exceptions/active``
    - ``workflow://metrics/kpi-definitions``
    - ``workflow://audit/recent``

    Args:
        mcp: FastMCP server instance.
        process_service: :class:`ProcessService` used for process maps.
        default_source_uri: Default event log used for process maps.
        data_dir: Directory to search for event log files. Defaults to
            ``sample-data/`` relative to the project root.
    """
    register_process_resources(
        mcp, process_service, default_source_uri, data_dir=data_dir
    )
    register_exception_resources(mcp)
    register_decision_resources(mcp)
