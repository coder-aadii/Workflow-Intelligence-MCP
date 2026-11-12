"""MCP Resources for process discovery data.

Exposes:

- ``workflow://process-maps/{process_name}`` (``application/json``): a
  dynamic Directly-Follows Graph (nodes, edges, average duration) computed
  from the configured event log.
"""

from __future__ import annotations

import json
from typing import Any

from workflow_intelligence.schemas.process import ProcessAnalyzeRequest


def register_process_resources(
    mcp: Any,
    process_service: Any,
    default_source_uri: str,
) -> None:
    """Register process-map resources with the FastMCP server.

    Args:
        mcp: FastMCP server instance.
        process_service: :class:`ProcessService` used to mine the event log.
        default_source_uri: Default event log used when computing maps.
    """

    @mcp.resource(  # type: ignore[untyped-decorator]
        "workflow://process-maps/{process_name}",
        name="Process Map",
        mime_type="application/json",
        description=(
            "Dynamic Directly-Follows Graph (nodes, edges, average "
            "duration) for a business process."
        ),
    )
    async def read_process_map(process_name: str) -> str:
        """Return the process map JSON for ``process_name``.

        Currently the ``procurement`` process is mined from the default
        event log; any other name returns an empty graph for that process.
        """
        if process_name != "procurement":
            graph: dict[str, Any] = {
                "process_name": process_name,
                "nodes": [],
                "edges": [],
                "total_cases": 0,
                "total_events": 0,
                "note": f"No event log configured for process '{process_name}'.",
            }
            return json.dumps(graph)

        request = ProcessAnalyzeRequest(
            source_uri=default_source_uri,
            case_id_col="case_id",
            activity_col="activity",
            timestamp_col="timestamp",
        )
        result = process_service.analyze_business_process(request)

        nodes = [
            {"id": activity, "label": activity} for activity in result.unique_activities
        ]
        edges = [
            {
                "source": transition.source_activity,
                "target": transition.target_activity,
                "count": transition.transition_count,
                "avg_duration_hours": transition.mean_duration_hours,
            }
            for transition in result.transitions
        ]

        graph = {
            "process_name": process_name,
            "nodes": nodes,
            "edges": edges,
            "total_cases": result.total_cases,
            "total_events": result.total_events,
            "variant_count": result.variant_count,
        }
        return json.dumps(graph)
