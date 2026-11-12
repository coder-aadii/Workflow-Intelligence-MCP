"""MCP Prompts package."""

from typing import Any

from workflow_intelligence.prompts.decision_analysis import (  # noqa: F401
    management_decision_brief,
)
from workflow_intelligence.prompts.exception_analysis import (  # noqa: F401
    exception_triage_session,
)
from workflow_intelligence.prompts.process_analysis import (  # noqa: F401
    process_bottleneck_review,
)


def register_prompts(mcp: Any) -> None:
    """Register all MCP Prompts with the FastMCP server.

    Registers:

    - ``process_bottleneck_review``
    - ``exception_triage_session``
    - ``management_decision_brief``

    Args:
        mcp: FastMCP server instance.
    """

    mcp.prompt(name="process_bottleneck_review")(process_bottleneck_review)
    mcp.prompt(name="exception_triage_session")(exception_triage_session)
    mcp.prompt(name="management_decision_brief")(management_decision_brief)
