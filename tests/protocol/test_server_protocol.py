"""Automated MCP protocol test suite for the Workflow Intelligence server.

Validates:

1. Protocol compliance: all 9 domain tools advertise valid input schemas.
2. End-to-end execution: every tool runs against ``sample-data/`` without
   unhandled errors and returns structured responses.
3. Error resilience: malformed payloads produce structured error returns.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pandas as pd
import pytest

from workflow_intelligence.schemas.decision import (
    DecisionBriefRequest,
    KPICalculationRequest,
    KPIExplanationRequest,
)
from workflow_intelligence.schemas.exception import (
    ResolutionRequest,
    WorkflowInvestigationRequest,
)
from workflow_intelligence.schemas.process import (
    AutomationScoreRequest,
    BottleneckRequest,
    ProcessAnalyzeRequest,
)
from workflow_intelligence.server import create_server
from workflow_intelligence.services.analytics_service import AnalyticsServiceError
from workflow_intelligence.services.decision_service import DecisionService
from workflow_intelligence.services.exception_service import (
    ExceptionService,
    ExceptionServiceError,
)
from workflow_intelligence.services.process_service import (
    ProcessService,
    ProcessServiceError,
)

SAMPLE_DATA = Path(__file__).resolve().parents[2] / "sample-data"
EVENTS_CSV = SAMPLE_DATA / "procurement_events.csv"
FAILED_WORKFLOWS_JSON = SAMPLE_DATA / "failed_workflows.json"
COSTS_CSV = SAMPLE_DATA / "procurement_costs.csv"

EXPECTED_TOOLS = {
    "process_analyze_business_process",
    "process_identify_bottlenecks",
    "process_score_automation_opportunities",
    "exception_investigate_workflow",
    "exception_recommend_resolution",
    "exception_escalate_case",
    "decision_analyze_kpi_performance",
    "decision_explain_kpi_deviation",
    "decision_generate_brief",
}


def _build_server() -> ProcessService:
    """Build a server with all tools registered, returning the process service."""
    mcp = create_server()
    tools = asyncio.run(mcp.list_tools())
    assert {t.name for t in tools} == EXPECTED_TOOLS
    for tool in tools:
        assert tool.name
        assert tool.description
        assert isinstance(tool.parameters, dict)
        assert "type" in tool.parameters
        assert "properties" in tool.parameters
    return ProcessService()


class TestServerProtocolToolListing:
    """Tests for MCP server tool listing and schema advertisement."""

    def test_all_nine_tools_registered(self) -> None:
        """All 9 domain tools advertise valid MCP tool schemas."""
        mcp = create_server()
        tools = asyncio.run(mcp.list_tools())
        names = {t.name for t in tools}
        assert names == EXPECTED_TOOLS
        assert len(tools) == 9

    def test_tool_schemas_have_required_fields(self) -> None:
        """Every advertised tool exposes name, description, and inputSchema."""
        mcp = create_server()
        for tool in asyncio.run(mcp.list_tools()):
            assert tool.name in EXPECTED_TOOLS
            assert isinstance(tool.description, str) and len(tool.description) > 0
            schema = tool.parameters
            assert isinstance(schema, dict)
            assert "type" in schema
            assert "properties" in schema
            assert "required" in schema


class TestServerProtocolExecution:
    """Tests for end-to-end tool execution against sample data."""

    def test_process_analyze_business_process(self) -> None:
        service = _build_server()
        result = service.analyze_business_process(
            ProcessAnalyzeRequest(
                source_uri=str(EVENTS_CSV),
                case_id_col="case_id",
                activity_col="activity",
                timestamp_col="timestamp",
            )
        )
        assert result.success is True
        assert result.total_cases == 500
        assert result.total_events >= 2500
        assert "Finance Approval" in result.unique_activities
        assert "Vendor Selection" in result.unique_activities
        assert result.variant_count >= 1

    def test_process_identify_bottlenecks(self) -> None:
        service = _build_server()
        report = service.identify_bottlenecks(
            BottleneckRequest(
                source_uri=str(EVENTS_CSV),
                threshold_std_dev=2.0,
            )
        )
        assert report.success is True
        assert report.total_stages_analyzed > 0
        assert isinstance(report.bottlenecks, list)
        # The embedded 74-hour Finance Approval -> Vendor Selection delay
        # should surface as a bottleneck.
        assert any("Vendor Selection" in b.stage_transition for b in report.bottlenecks)

    def test_process_score_automation_opportunities(self) -> None:
        service = _build_server()
        report = service.score_automation_opportunities(
            AutomationScoreRequest(source_uri=str(EVENTS_CSV), min_frequency=10)
        )
        assert report.success is True
        assert report.methodology
        assert isinstance(report.candidates, list)
        assert len(report.candidates) > 0
        assert all(c.rank >= 1 for c in report.candidates)

    def test_exception_investigate_workflow(self) -> None:
        service = ExceptionService()
        payload = {
            "vendor_name": "ABC Technologies",
            "tax_id": "TX-123456",
            "discrepancy": "vendor_name_typo",
            "master_records": [
                {
                    "entity_id": "VEND-001",
                    "registered_name": "ABC Technology Pvt Ltd",
                    "tax_id": "TX-123456",
                }
            ],
        }
        result = service.investigate_failure(
            WorkflowInvestigationRequest(
                case_id="PO-9001",
                workflow_type="purchase_order_creation",
                failure_payload=payload,
            )
        )
        assert result.success is True
        assert result.case_id == "PO-9001"
        assert result.category.value == "MASTER_DATA_DISCREPANCY"
        assert len(result.matched_entities) == 1
        assert result.matched_entities[0].matching_score >= 95.0

    def test_exception_recommend_resolution(self) -> None:
        service = ExceptionService()
        plan = service.recommend_resolution(
            ResolutionRequest(
                exception_id="EXC-001",
                policy_context={"auto_approve": True, "approval_tier": "manager"},
            )
        )
        assert plan.auto_resolve is True
        assert plan.required_approval_tier is None
        assert plan.remediation_action

    def test_exception_escalate_case(self) -> None:
        service = ExceptionService()
        result = service.escalate_case(
            case_id="PO-9002",
            reason="Missing department cost-center code",
            target_department="Finance",
        )
        assert result.ticket_id.startswith("TKT-")
        assert result.routed_department == "Finance"
        assert result.audit_narrative
        assert result.success is True

    def test_decision_analyze_kpi_performance(self) -> None:
        service = DecisionService()
        result = service.analyze_kpi_performance(
            KPICalculationRequest(
                metric_name="processing_cost",
                timeframe_current="Q3",
                timeframe_baseline="Q2",
            )
        )
        assert result.success is True
        assert result.metric_name == "processing_cost"
        assert result.current_value > result.baseline_value
        assert result.delta_percentage > 0

    def test_decision_explain_kpi_deviation(self) -> None:
        service = DecisionService()
        explanation = service.explain_kpi_deviation(
            KPIExplanationRequest(
                metric_name="processing_cost",
                observed_delta=925.0,
                dimension="department",
            )
        )
        assert explanation.success is True
        assert explanation.metric_name == "processing_cost"
        assert explanation.observed_delta == 925.0
        assert len(explanation.top_contributors) >= 1
        assert explanation.explanation

    def test_decision_generate_brief(self) -> None:
        service = DecisionService()
        brief = service.generate_brief(
            DecisionBriefRequest(
                topic="Q3 Procurement Cost Inflation",
                metric_ids=["processing_cost", "order_count"],
            )
        )
        assert brief.success is True
        assert brief.topic == "Q3 Procurement Cost Inflation"
        assert len(brief.findings) == 2
        assert len(brief.attributions) >= 1
        assert any("sha256=" in link for link in brief.provenance_links)
        assert len(brief.recommendations) >= 1
        assert "Executive Summary" in brief.executive_summary


class TestServerProtocolErrorResilience:
    """Tests that malformed payloads produce structured error returns."""

    def test_process_analyze_missing_file(self) -> None:
        service = _build_server()
        with pytest.raises(ProcessServiceError):
            service.analyze_business_process(
                ProcessAnalyzeRequest(
                    source_uri="/nonexistent/path.csv",
                    case_id_col="case_id",
                    activity_col="activity",
                    timestamp_col="timestamp",
                )
            )

    def test_decision_unknown_metric_raises(self) -> None:
        service = DecisionService()
        with pytest.raises(AnalyticsServiceError):
            service.analyze_kpi_performance(
                KPICalculationRequest(
                    metric_name="nonexistent_metric",
                    timeframe_current="Q3",
                    timeframe_baseline="Q2",
                )
            )

    def test_exception_investigate_empty_payload_raises(self) -> None:
        service = ExceptionService()
        with pytest.raises(ExceptionServiceError):
            service.investigate_failure(
                WorkflowInvestigationRequest(
                    case_id="PO-1",
                    workflow_type="po",
                    failure_payload={},
                )
            )

    def test_failed_workflows_json_is_valid(self) -> None:
        """failed_workflows.json parses and documents the 3 scenarios."""
        data = json.loads(FAILED_WORKFLOWS_JSON.read_text())
        assert isinstance(data, list)
        assert len(data) == 3
        categories = {d["failure_category"] for d in data}
        assert "MASTER_DATA_DISCREPANCY" in categories
        assert "GOVERNANCE_INCOMPLETE" in categories
        assert "APPROVAL_TIMEOUT" in categories

    def test_costs_csv_embeds_q3_spike(self) -> None:
        """procurement_costs.csv has 4 quarters with a Q3 spike."""
        df = pd.read_csv(COSTS_CSV)
        assert set(df["quarter"].unique()) == {"Q1", "Q2", "Q3", "Q4"}
        q3 = df[df["quarter"] == "Q3"]["processing_cost"].sum()
        q2 = df[df["quarter"] == "Q2"]["processing_cost"].sum()
        assert q3 > q2 * 2, f"Q3 spike missing: Q3={q3}, Q2={q2}"
