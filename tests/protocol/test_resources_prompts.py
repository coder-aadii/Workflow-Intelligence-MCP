"""Protocol verification tests for MCP Resources and Prompts.

Verifies:

1. Resource reads return valid MIME types and payloads for all four
   registered resources.
2. Prompt generation produces expected message lists for all three
   registered prompts.
3. Observability (structlog + OpenTelemetry) initializes without errors
   and does not break resource/prompt reads.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from fastmcp import Client
from mcp.types import TextResourceContents

from workflow_intelligence.observability.logging import (
    configure_logging,
    get_logger,
    log_context,
)
from workflow_intelligence.observability.tracing import (
    get_tracer,
    init_tracing,
    trace_span,
)
from workflow_intelligence.schemas.process import (
    AutomationCandidate,
    AutomationScoreReport,
    BottleneckItem,
    BottleneckReport,
)
from workflow_intelligence.server import create_server

EXPECTED_RESOURCES = {
    "workflow://process-maps/procurement": "application/json",
    "workflow://exceptions/active": "application/json",
    "workflow://metrics/kpi-definitions": "text/markdown",
    "workflow://audit/recent": "application/json",
}

EXPECTED_PROMPTS = {
    "process_bottleneck_review",
    "exception_triage_session",
    "management_decision_brief",
}


def _contents_text(contents: list[Any]) -> str:
    """Extract the text payload from resource contents, skipping blobs."""
    for item in contents:
        if isinstance(item, TextResourceContents):
            return item.text
    raise AssertionError("no text resource contents returned")


@pytest.fixture
def client() -> Client[Any]:
    """Provide an MCP Client connected to the test server."""
    return Client(create_server())


@pytest.mark.asyncio
async def test_all_resources_registered(client: Client[Any]) -> None:
    """All four resources advertise valid MIME types."""
    async with client:
        resources = await client.list_resources()
        templates = await client.list_resource_templates()
        concrete = {
            getattr(r, "uri", None): getattr(r, "mime_type", None) for r in resources
        }
        template_map = {
            getattr(t, "uri_template", None): getattr(t, "mime_type", None)
            for t in templates
        }
        # Every expected URI is either directly served or produced by a template.
        for uri, mime in EXPECTED_RESOURCES.items():
            if uri in concrete:
                assert concrete[uri] == mime
            else:
                matched = any(
                    tmpl is not None
                    and tmpl in template_map
                    and uri.startswith(tmpl.split("{")[0])
                    and template_map[tmpl] == mime
                    for tmpl in template_map
                )
                assert matched, f"missing resource {uri}"


@pytest.mark.asyncio
async def test_process_map_resource_payload(client: Client[Any]) -> None:
    """workflow://process-maps/procurement returns valid graph JSON."""
    async with client:
        contents = await client.read_resource("workflow://process-maps/procurement")
        item = contents[0]
        assert item.mime_type == "application/json"
        data = json.loads(_contents_text(contents))
        assert data["process_name"] == "procurement"
        assert isinstance(data["nodes"], list)
        assert isinstance(data["edges"], list)
        assert data["total_cases"] >= 1
        assert data["total_events"] >= 1


@pytest.mark.asyncio
async def test_kpi_definitions_resource_payload(client: Client[Any]) -> None:
    """workflow://metrics/kpi-definitions returns markdown catalog."""
    async with client:
        contents = await client.read_resource("workflow://metrics/kpi-definitions")
        item = contents[0]
        assert item.mime_type == "text/markdown"
        assert _contents_text(contents).startswith("# KPI Definitions")
        assert "processing_cost" in _contents_text(contents)


@pytest.mark.asyncio
async def test_active_exceptions_resource_payload(client: Client[Any]) -> None:
    """workflow://exceptions/active returns JSON (empty when no DB)."""
    async with client:
        contents = await client.read_resource("workflow://exceptions/active")
        item = contents[0]
        assert item.mime_type == "application/json"
        data = json.loads(_contents_text(contents))
        assert data["kind"] == "active_exceptions"
        assert isinstance(data["exceptions"], list)


@pytest.mark.asyncio
async def test_recent_audit_resource_payload(client: Client[Any]) -> None:
    """workflow://audit/recent returns JSON (empty when no DB)."""
    async with client:
        contents = await client.read_resource("workflow://audit/recent")
        item = contents[0]
        assert item.mime_type == "application/json"
        data = json.loads(_contents_text(contents))
        assert data["kind"] == "recent_audit_log"
        assert isinstance(data["entries"], list)


@pytest.mark.asyncio
async def test_all_prompts_registered(client: Client[Any]) -> None:
    """All three prompts are advertised."""
    async with client:
        prompts = await client.list_prompts()
        names = {p.name for p in prompts}
        assert EXPECTED_PROMPTS <= names


@pytest.mark.asyncio
async def test_process_bottleneck_review_prompt(client: Client[Any]) -> None:
    """process_bottleneck_review renders a formatted instruction prompt."""
    async with client:
        result = await client.get_prompt(
            "process_bottleneck_review",
            arguments={
                "bottleneck_report": BottleneckReport(
                    source_uri="sample-data/procurement_events.csv",
                    total_stages_analyzed=4,
                    bottlenecks=[
                        BottleneckItem(
                            stage_transition="Finance Approval -> Vendor Selection",
                            avg_duration_hours=74.0,
                            baseline_duration_hours=4.0,
                            z_score=5.2,
                            affected_case_count=50,
                            recommendation="Add auto-approval for low-value POs",
                        )
                    ],
                ),
                "automation_report": AutomationScoreReport(
                    candidates=[
                        AutomationCandidate(
                            activity_name="Manager Approval",
                            execution_frequency=120,
                            avg_duration_minutes=120.0,
                            standard_deviation_minutes=30.0,
                            manual_effort_score=8.0,
                            automation_potential_score=85.0,
                            rank=1,
                        )
                    ],
                    methodology="Score = f(frequency, duration, 1/variance)",
                ),
            },
        )
        messages = result.messages
        assert isinstance(messages, list) and len(messages) >= 1
        text = " ".join(str(m.content) for m in messages)
        assert "Process Bottleneck Review" in text
        assert "Bottlenecks" in text
        assert "Automation Candidates" in text


@pytest.mark.asyncio
async def test_exception_triage_session_prompt(client: Client[Any]) -> None:
    """exception_triage_session renders with case fields."""
    async with client:
        result = await client.get_prompt(
            "exception_triage_session",
            arguments={
                "case_id": "PO-001",
                "workflow_type": "purchase_order_creation",
                "category": "MASTER_DATA_DISCREPANCY",
                "root_cause": "vendor name typo",
                "severity": "MEDIUM",
                "matched_entities": [],
                "recommended_remediation": "remap vendor id",
            },
        )
        messages = result.messages
        text = " ".join(str(m.content) for m in messages)
        assert "Exception Triage Session" in text
        assert "PO-001" in text
        assert "MASTER_DATA_DISCREPANCY" in text


@pytest.mark.asyncio
async def test_management_decision_brief_prompt(client: Client[Any]) -> None:
    """management_decision_brief renders KPI findings into a brief."""
    async with client:
        result = await client.get_prompt(
            "management_decision_brief",
            arguments={
                "topic": "Q3 Cost Inflation",
                "metric_name": "processing_cost",
                "baseline_value": 100.0,
                "current_value": 150.0,
                "delta_percentage": 50.0,
            },
        )
        messages = result.messages
        text = " ".join(str(m.content) for m in messages)
        assert "Management Decision Brief: Q3 Cost Inflation" in text
        assert "50.00%" in text
        assert "Recommended Action" in text


def test_observability_initializes_without_errors() -> None:
    """structlog and OpenTelemetry configure cleanly in dev mode."""
    configure_logging(workflow_env="development", log_level="INFO")
    init_tracing(workflow_env="development")
    logger = get_logger("workflow_intelligence.test")
    with log_context(
        correlation_id="corr-1",
        tool_name="test_tool",
        case_id="case-1",
        duration_ms=12.5,
    ):
        logger.info("observability smoke test")
    tracer = get_tracer()
    with tracer.start_as_current_span("mcp.tool.invoke"):
        pass


def test_trace_span_decorator() -> None:
    """@trace_span wraps sync and async functions without error."""
    init_tracing(workflow_env="development")

    @trace_span("mcp.tool.invoke")
    def sync_fn(x: int) -> int:
        return x + 1

    @trace_span("duckdb.query")
    async def async_fn(y: int) -> int:
        return y * 2

    assert sync_fn(5) == 6
    assert asyncio.run(async_fn(3)) == 6
