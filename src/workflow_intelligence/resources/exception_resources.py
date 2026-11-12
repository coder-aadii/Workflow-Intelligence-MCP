"""MCP Resources for workflow exception data.

Exposes:

- ``workflow://exceptions/active`` (``application/json``): real-time queue of
  open workflow failures awaiting resolution.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.exc import SQLAlchemyError

from workflow_intelligence.persistence.database import get_db_session
from workflow_intelligence.persistence.repositories import ExceptionRepository


def register_exception_resources(mcp: Any) -> None:
    """Register exception resources with the FastMCP server.

    Args:
        mcp: FastMCP server instance.
    """

    async def _read_active_exceptions() -> str:
        exceptions: list[dict[str, Any]] = []
        try:
            async for session in get_db_session():
                repo = ExceptionRepository(session)
                unresolved = await repo.get_unresolved(offset=0, limit=100)
                exceptions = [
                    {
                        "id": exc.id,
                        "workflow_id": exc.workflow_id,
                        "category": exc.category,
                        "root_cause": exc.root_cause,
                        "is_resolved": exc.is_resolved,
                        "resolved_at": (
                            exc.resolved_at.isoformat() if exc.resolved_at else None
                        ),
                        "payload": exc.payload,
                    }
                    for exc in unresolved
                ]
        except SQLAlchemyError:
            # Database unavailable (e.g. no live PostgreSQL in local/dev).
            # Return an empty active-exceptions queue rather than failing.
            exceptions = []
        return json.dumps(
            {
                "kind": "active_exceptions",
                "count": len(exceptions),
                "exceptions": exceptions,
            }
        )

    mcp.resource(
        "workflow://exceptions/active",
        name="Active Exceptions",
        mime_type="application/json",
        description="Real-time queue of open workflow failures awaiting resolution.",
    )(_read_active_exceptions)
