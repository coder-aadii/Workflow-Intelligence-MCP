"""MCP tool handlers for the Process Discovery Engine (process_* tools).

Handlers are thin transport layers: they validate input via Pydantic schemas,
delegate to ProcessService, and return validated output schemas.
"""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP

from workflow_intelligence.schemas.process import (
    AutomationScoreRequest,
    BottleneckRequest,
    ProcessAnalyzeRequest,
)
from workflow_intelligence.services.process_service import ProcessService


def register_process_tools(mcp: FastMCP, service: ProcessService) -> None:
    """Register process discovery tools with the FastMCP server.

    Args:
        mcp: FastMCP server instance.
        service: ProcessService instance for business logic.
    """

    @mcp.tool()
    async def process_analyze_business_process(
        source_uri: str,
        case_id_col: str,
        activity_col: str,
        timestamp_col: str,
    ) -> dict[str, Any]:
        """Analyze a business process event log and reconstruct execution paths.

        Args:
            source_uri: URI or path to the event log file.
            case_id_col: Column name for case identifiers.
            activity_col: Column name for activity names.
            timestamp_col: Column name for event timestamps.

        Returns:
            Process analysis result as a dictionary.
        """
        request = ProcessAnalyzeRequest(
            source_uri=source_uri,
            case_id_col=case_id_col,
            activity_col=activity_col,
            timestamp_col=timestamp_col,
        )
        result = service.analyze_business_process(request)
        return result.model_dump()

    @mcp.tool()
    async def process_identify_bottlenecks(
        source_uri: str,
        threshold_std_dev: float = 2.0,
    ) -> dict[str, Any]:
        """Identify operational bottlenecks using z-score deviation analysis.

        Args:
            source_uri: URI or path to the event log file.
            threshold_std_dev: Standard deviation threshold (default 2.0).

        Returns:
            Bottleneck report as a dictionary.
        """
        request = BottleneckRequest(
            source_uri=source_uri,
            threshold_std_dev=threshold_std_dev,
        )
        result = service.identify_bottlenecks(request)
        return result.model_dump()

    @mcp.tool()
    async def process_score_automation_opportunities(
        source_uri: str,
        min_frequency: int = 10,
    ) -> dict[str, Any]:
        """Score repetitive activities for automation suitability.

        Args:
            source_uri: URI or path to the event log file.
            min_frequency: Minimum frequency threshold (default 10).

        Returns:
            Automation score report as a dictionary.
        """
        request = AutomationScoreRequest(
            source_uri=source_uri,
            min_frequency=min_frequency,
        )
        result = service.score_automation_opportunities(request)
        return result.model_dump()
