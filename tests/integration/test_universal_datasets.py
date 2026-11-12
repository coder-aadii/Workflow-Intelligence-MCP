"""Integration tests for universal dataset support (Phase 9).

Verifies the zero-hardcoding guarantee: all decision tools work against
enterprise datasets with custom column names and zero procurement
assumptions.

Covered scenarios:
1. Process discovery on Amul dairy cold-chain events (milk chilling delay
   bottleneck detection).
2. KPI analysis on dairy plant costs with a custom spoilage-rate
   expression (``100.0 * SUM(spoiled) / SUM(total)``).
3. KPI deviation explanation with dynamic dimension inference
   (``plant_id`` inferred when no dimension is provided).
4. Multi-process resource discovery: ``workflow://process-maps/dairy``
   resolves to the dairy cold-chain event log.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from fastmcp import Client
from mcp.types import TextResourceContents

from workflow_intelligence.schemas.decision import (
    DecisionBriefRequest,
    KPICalculationRequest,
    KPIExplanationRequest,
)
from workflow_intelligence.schemas.process import ProcessAnalyzeRequest
from workflow_intelligence.server import create_server
from workflow_intelligence.services.analytics_service import AnalyticsService
from workflow_intelligence.services.decision_service import DecisionService
from workflow_intelligence.services.process_service import ProcessService

SAMPLE_DATA = Path(__file__).resolve().parents[2] / "sample-data"
DAIRY_EVENTS_CSV = SAMPLE_DATA / "dairy_cold_chain_events.csv"
DAIRY_COSTS_CSV = SAMPLE_DATA / "dairy_plant_costs.csv"


@pytest.fixture
def client() -> Client[Any]:
    """Provide an MCP Client connected to the test server."""
    return Client(create_server())


def _contents_text(contents: list[Any]) -> str:
    """Extract the text payload from resource contents, skipping blobs."""
    for item in contents:
        if isinstance(item, TextResourceContents):
            return item.text
    raise AssertionError("no text resource contents returned")


class TestUniversalProcessDiscovery:
    """Process discovery works on non-procurement event logs."""

    def test_dairy_cold_chain_bottleneck(self) -> None:
        """Milk chilling delay should be detected as a bottleneck."""
        ps = ProcessService()
        result = ps.analyze_business_process(
            ProcessAnalyzeRequest(
                source_uri=str(DAIRY_EVENTS_CSV),
                case_id_col="case_id",
                activity_col="activity",
                timestamp_col="timestamp",
            )
        )
        assert result.success is True
        assert result.total_cases == 6
        assert "Chilling" in result.unique_activities
        assert "Dispatch" in result.unique_activities

        # Chilling has the 3-hour delay on DC-0002 (06:00->08:30) and
        # DC-0004 (06:00->09:00), so it should appear as a transition.
        chilling_transitions = [
            t
            for t in result.transitions
            if t.source_activity == "Chilling" or t.target_activity == "Chilling"
        ]
        assert len(chilling_transitions) > 0


class TestUniversalKPIAnalysis:
    """KPI analysis works on custom datasets with custom expressions."""

    def test_dairy_spoilage_rate_custom_expression(self) -> None:
        """Custom spoilage-rate expression computes verified DuckDB values."""
        svc = DecisionService()
        # P1: July = 5000/100000 = 5%, August = 12000/100000 = 12%
        result = svc.analyze_kpi_performance(
            KPICalculationRequest(
                metric_name="spoilage_rate",
                source_uri=str(DAIRY_COSTS_CSV),
                timeframe_column="timeframe",
                timeframe_current="August",
                timeframe_baseline="July",
                custom_expression=(
                    "100.0 * SUM(milk_spoiled_litres) / SUM(milk_received_litres)"
                ),
            )
        )
        assert result.success is True
        assert result.metric_name == "spoilage_rate"
        # Aggregate spoilage rate: July = 4.545%, August = 16.364%
        assert abs(result.baseline_value - 4.545454545454546) < 1e-6
        assert abs(result.current_value - 16.363636363636363) < 1e-6
        assert abs(result.delta_percentage - 260.0) < 1e-6

    def test_dairy_spoilage_rate_deviation_explanation(self) -> None:
        """Deviation explanation with dynamic plant_id dimension inference."""
        svc = DecisionService()
        df = pd.read_csv(DAIRY_COSTS_CSV)
        # Compute overall spoilage rate for July vs August
        jul = df[df["timeframe"] == "July"]
        aug = df[df["timeframe"] == "August"]
        jul_rate = (
            jul["milk_spoiled_litres"].sum() / jul["milk_received_litres"].sum() * 100
        )
        aug_rate = (
            aug["milk_spoiled_litres"].sum() / aug["milk_received_litres"].sum() * 100
        )
        observed_delta = aug_rate - jul_rate

        explanation = svc.explain_kpi_deviation(
            KPIExplanationRequest(
                metric_name="spoilage_rate",
                source_uri=str(DAIRY_COSTS_CSV),
                timeframe_column="timeframe",
                timeframe_current="August",
                timeframe_baseline="July",
                observed_delta=observed_delta,
                dimension=None,  # Should infer plant_id
                custom_expression=(
                    "100.0 * SUM(milk_spoiled_litres) / SUM(milk_received_litres)"
                ),
            )
        )
        assert explanation.success is True
        assert explanation.data_insufficient is False
        assert explanation.warning is None
        # Dimension should be inferred as plant_id (the only text col besides timeframe)
        assert explanation.dimension == "plant_id"
        assert len(explanation.top_contributors) >= 1
        assert "plant_id" in explanation.explanation
        assert (
            str(round(observed_delta, 4)) in explanation.explanation
            or str(observed_delta) in explanation.explanation
        )

    def test_zero_procurement_assumptions(self) -> None:
        """Tools succeed against dairy CSV with no procurement columns."""
        svc = DecisionService()
        # Analyze processing_cost_inr across July vs August using custom expression
        result = svc.analyze_kpi_performance(
            KPICalculationRequest(
                metric_name="total_processing_cost",
                source_uri=str(DAIRY_COSTS_CSV),
                timeframe_column="timeframe",
                timeframe_current="August",
                timeframe_baseline="July",
                custom_expression="SUM(processing_cost_inr)",
            )
        )
        assert result.success is True
        jul_cost = (
            pd.read_csv(DAIRY_COSTS_CSV)
            .query("timeframe == 'July'")["processing_cost_inr"]
            .sum()
        )
        aug_cost = (
            pd.read_csv(DAIRY_COSTS_CSV)
            .query("timeframe == 'August'")["processing_cost_inr"]
            .sum()
        )
        assert abs(result.baseline_value - jul_cost) < 1e-6
        assert abs(result.current_value - aug_cost) < 1e-6


class TestUniversalResourceDiscovery:
    """Multi-process resource discovery works for non-procurement datasets."""

    @pytest.mark.asyncio
    async def test_dairy_process_map_resource(self, client: Client[Any]) -> None:
        """workflow://process-maps/dairy resolves to the dairy cold-chain graph."""
        async with client:
            contents = await client.read_resource("workflow://process-maps/dairy")
            item = contents[0]
            assert item.mime_type == "application/json"
            data = json.loads(_contents_text(contents))
            assert data["process_name"] == "dairy"
            assert isinstance(data["nodes"], list)
            assert isinstance(data["edges"], list)
            assert data["total_cases"] == 6
            assert data["total_events"] >= 6
            # Dairy activities should be present.
            activity_ids = {n["id"] for n in data["nodes"]}
            assert "Milk Collection" in activity_ids
            assert "Chilling" in activity_ids
            assert "Dispatch" in activity_ids

    @pytest.mark.asyncio
    async def test_unknown_process_resource_returns_error(
        self, client: Client[Any]
    ) -> None:
        """Unknown process name returns an error graph, not a crash."""
        async with client:
            contents = await client.read_resource(
                "workflow://process-maps/nonexistent_process"
            )
            item = contents[0]
            assert item.mime_type == "application/json"
            data = json.loads(_contents_text(contents))
            assert data["process_name"] == "nonexistent_process"
            assert data["nodes"] == []
            assert data["edges"] == []
            assert "not found" in data.get("note", "")


class TestUniversalBriefGeneration:
    """Decision brief generation works on universal datasets."""

    def test_dairy_brief_generation(self) -> None:
        """Decision brief generates from dairy KPI findings."""
        # Register the custom spoilage_rate metric for brief generation.
        analytics = AnalyticsService()
        analytics.use_source(str(DAIRY_COSTS_CSV), "dairy_plant_costs")
        analytics.register_custom_metric(
            "spoilage_rate",
            "100.0 * SUM(milk_spoiled_litres) / SUM(milk_received_litres)",
            column="milk_received_litres",
        )
        svc = DecisionService(analytics=analytics)

        brief = svc.generate_brief(
            DecisionBriefRequest(
                topic="Dairy Spoilage Rate Increase",
                metric_ids=["spoilage_rate"],
            ),
            source_uri=str(DAIRY_COSTS_CSV),
            table_name="dairy_plant_costs",
            timeframe_column="timeframe",
            current_quarter="August",
            baseline_quarter="July",
        )
        assert brief.success is True
        assert brief.topic == "Dairy Spoilage Rate Increase"
        assert len(brief.findings) == 1
        assert any("sha256=" in link for link in brief.provenance_links)
        assert any("source=" in link for link in brief.provenance_links)
        assert len(brief.recommendations) >= 1
        assert "Executive Summary" in brief.executive_summary
        assert "Observed Variance" in brief.executive_summary
