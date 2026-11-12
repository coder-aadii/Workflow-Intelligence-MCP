"""MCP Resources for business decision intelligence data.

Exposes:

- ``workflow://metrics/kpi-definitions`` (``text/markdown``): catalog of
  operational KPI calculation rules and SQL queries.
- ``workflow://audit/recent`` (``application/json``): tail of the last 100
  system escalations, recoveries, and scans.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.exc import SQLAlchemyError

from workflow_intelligence.persistence.database import get_db_session
from workflow_intelligence.persistence.repositories import AuditRepository

# Static catalog of operational KPI calculation rules. Every rule is
# computed deterministically by DuckDB (see
# ``workflow_intelligence.services.analytics_service.METRIC_REGISTRY``);
# no LLM is used to derive numbers.
KPI_DEFINITIONS_MARKDOWN = """# KPI Definitions

The following operational KPIs are computed deterministically by DuckDB over
the configured event log. Each rule lists its calculation expression and
interpretation.

| KPI | Expression | Description |
|-----|------------|-------------|
| `processing_cost` | `SUM(processing_cost)` | Total processing cost in the period. |
| `avg_processing_cost` | `AVG(processing_cost)` | Average processing cost per order. |
| `total_expedited_fee` | `SUM(expedited_fee)` | Total expedited shipping fees. |
| `avg_expedited_fee` | `AVG(expedited_fee)` | Average expedited fee per order. |
| `order_count` | `COUNT(order_id)` | Number of orders. |
| `expedited_share_percentage` | `share formula` | Share of expedited orders. |

Full formulas are computed deterministically by DuckDB (see
``workflow_intelligence.services.analytics_service.METRIC_REGISTRY``); no LLM
is used to derive numbers. The expedited share is calculated as
``100.0 * SUM(CASE WHEN shipping_method = 'Expedited' THEN 1 ELSE 0 END)
/ COUNT(*)``.

Period-over-period comparisons compute absolute and relative variance between
a current and a baseline period. Dimensional attribution ranks dimension
values (e.g. department, supplier tier) by their contribution to the observed
delta.

All figures are computed solely by DuckDB; LLMs draft narrative only from
verified numbers.
"""


def register_decision_resources(mcp: Any) -> None:
    """Register decision-intelligence resources with the FastMCP server.

    Args:
        mcp: FastMCP server instance.
    """

    @mcp.resource(  # type: ignore[untyped-decorator]
        "workflow://metrics/kpi-definitions",
        name="KPI Definitions",
        mime_type="text/markdown",
        description="Catalog of operational KPI calculation rules and SQL queries.",
    )
    async def read_kpi_definitions() -> str:
        """Return the KPI definitions catalog as markdown."""
        return KPI_DEFINITIONS_MARKDOWN

    async def _read_recent_audit() -> str:
        entries: list[dict[str, Any]] = []
        try:
            async for session in get_db_session():
                repo = AuditRepository(session)
                recent = await repo.list(offset=0, limit=100)
                entries = [
                    {
                        "id": entry.id,
                        "action_name": entry.action_name,
                        "actor": entry.actor,
                        "timestamp": entry.timestamp.isoformat(),
                        "details": entry.details_json,
                        "correlation_id": entry.correlation_id,
                    }
                    for entry in recent
                ]
        except SQLAlchemyError:
            entries = []
        return json.dumps(
            {
                "kind": "recent_audit_log",
                "count": len(entries),
                "entries": entries,
            }
        )

    mcp.resource(
        "workflow://audit/recent",
        name="Recent Audit Log",
        mime_type="application/json",
        description="Tail of the last 100 system escalations, recoveries, and scans.",
    )(_read_recent_audit)
