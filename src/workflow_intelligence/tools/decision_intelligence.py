"""MCP tool handlers for Business Decision Intelligence (decision_* tools).

Handlers are thin transport layers: they validate input via Pydantic schemas,
delegate to DecisionService, and return validated output schemas.
"""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP

from workflow_intelligence.schemas.decision import (
    DecisionBriefRequest,
    KPICalculationRequest,
    KPIExplanationRequest,
)
from workflow_intelligence.services.decision_service import DecisionService


def register_decision_tools(mcp: FastMCP, service: DecisionService) -> None:
    """Register decision intelligence tools with the FastMCP server.

    Args:
        mcp: FastMCP server instance.
        service: DecisionService instance for business logic.
    """

    @mcp.tool()
    async def decision_analyze_kpi_performance(
        metric_name: str,
        timeframe_current: str,
        timeframe_baseline: str,
    ) -> dict[str, Any]:
        """Analyze KPI performance across two timeframes.

        Computes baseline and current values for a registered metric using
        deterministic DuckDB aggregations, then derives the absolute and
        relative variance.

        Args:
            metric_name: Name of the KPI metric (e.g. ``"processing_cost"``).
            timeframe_current: Current period identifier (e.g. ``"Q3"``).
            timeframe_baseline: Baseline period identifier (e.g. ``"Q2"``).

        Returns:
            KPI performance analysis result as a dictionary.
        """
        request = KPICalculationRequest(
            metric_name=metric_name,
            timeframe_current=timeframe_current,
            timeframe_baseline=timeframe_baseline,
        )
        result = service.analyze_kpi_performance(
            request,
            current_quarter=timeframe_current,
            baseline_quarter=timeframe_baseline,
        )
        return result.model_dump()

    @mcp.tool()
    async def decision_explain_kpi_deviation(
        metric_name: str,
        observed_delta: float,
        dimension: str | None = None,
    ) -> dict[str, Any]:
        """Explain a KPI deviation using verified dimensional drivers.

        Attributes the observed delta to the top contributing dimension values
        and attaches deterministic statistical significance indicators.

        Args:
            metric_name: Name of the KPI metric.
            observed_delta: Observed delta between current and baseline.
            dimension: Optional dimension to segment the deviation by.

        Returns:
            Deviation explanation as a dictionary.
        """
        request = KPIExplanationRequest(
            metric_name=metric_name,
            observed_delta=observed_delta,
            dimension=dimension,
        )
        result = service.explain_kpi_deviation(request)
        return result.model_dump()

    @mcp.tool()
    async def decision_generate_brief(
        topic: str,
        metric_ids: list[str],
    ) -> dict[str, Any]:
        """Generate a structured, evidence-backed decision brief.

        Combines verified KPI metrics into a markdown brief with executive
        summary, observed variance, contributing drivers, provenance footnotes,
        and recommended interventions.

        Args:
            topic: Topic of the decision brief.
            metric_ids: List of metric identifiers to include.

        Returns:
            Decision brief as a dictionary.
        """
        request = DecisionBriefRequest(topic=topic, metric_ids=metric_ids)
        result = service.generate_brief(request)
        return result.model_dump()
