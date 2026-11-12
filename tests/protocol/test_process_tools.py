"""Protocol tests for Process Discovery MCP tool handlers."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
from pydantic import ValidationError

from workflow_intelligence.schemas.process import (
    AutomationScoreRequest,
    BottleneckRequest,
    ProcessAnalyzeRequest,
)
from workflow_intelligence.services.process_service import ProcessService
from workflow_intelligence.tools.process_discovery import register_process_tools


@pytest.fixture
def csv_event_log(tmp_path: Path) -> str:
    """Create a CSV event log for protocol testing."""
    rows = [
        {"case_id": "C1", "activity": "A", "timestamp": "2026-01-01T09:00:00"},
        {"case_id": "C1", "activity": "B", "timestamp": "2026-01-01T10:00:00"},
        {"case_id": "C2", "activity": "A", "timestamp": "2026-01-01T09:00:00"},
        {"case_id": "C2", "activity": "B", "timestamp": "2026-01-01T11:00:00"},
    ]
    df = pd.DataFrame(rows)
    path = tmp_path / "events.csv"
    df.to_csv(path, index=False)
    return str(path)


def _build_server(csv_event_log: str) -> ProcessService:
    """Build a ProcessService with process tools registered on a FastMCP server.

    Returns the service so handlers can be invoked directly through
    their underlying sync functions for coverage.
    """
    from fastmcp import FastMCP

    mcp = FastMCP("test-process-server")
    service = ProcessService()
    register_process_tools(mcp, service)
    return service


class TestProcessToolHandlers:
    """Tests for FastMCP tool handler registration and invocation."""

    def test_process_analyze_business_process_handler(self, csv_event_log: str) -> None:
        """process_analyze_business_process handler returns a valid result dict."""
        service = _build_server(csv_event_log)

        result = service.analyze_business_process(
            ProcessAnalyzeRequest(
                source_uri=csv_event_log,
                case_id_col="case_id",
                activity_col="activity",
                timestamp_col="timestamp",
            )
        )

        assert result.success is True
        assert result.total_cases == 2
        assert result.total_events == 4
        assert "A" in result.unique_activities
        assert "B" in result.unique_activities
        assert len(result.transitions) > 0

    def test_process_identify_bottlenecks_handler(self, csv_event_log: str) -> None:
        """process_identify_bottlenecks handler returns a valid report dict."""
        service = _build_server(csv_event_log)

        result = service.identify_bottlenecks(
            BottleneckRequest(
                source_uri=csv_event_log,
                threshold_std_dev=2.0,
            )
        )

        assert result.success is True
        assert result.total_stages_analyzed > 0
        assert isinstance(result.bottlenecks, list)

    def test_process_score_automation_opportunities_handler(
        self, csv_event_log: str
    ) -> None:
        """process_score_automation_opportunities handler returns valid candidates."""
        service = _build_server(csv_event_log)

        result = service.score_automation_opportunities(
            AutomationScoreRequest(
                source_uri=csv_event_log,
                min_frequency=1,
            )
        )

        assert result.success is True
        assert result.methodology
        assert isinstance(result.candidates, list)
        assert len(result.candidates) > 0

    def test_process_analyze_request_validation(self) -> None:
        """ProcessAnalyzeRequest rejects empty fields."""
        with pytest.raises(ValidationError):
            ProcessAnalyzeRequest(
                source_uri="",
                case_id_col="cid",
                activity_col="act",
                timestamp_col="ts",
            )

    def test_bottleneck_request_validation(self) -> None:
        """BottleneckRequest rejects non-positive threshold."""
        with pytest.raises(ValidationError):
            BottleneckRequest(source_uri="x.csv", threshold_std_dev=0.0)

    def test_automation_score_request_validation(self) -> None:
        """AutomationScoreRequest rejects zero min_frequency."""
        with pytest.raises(ValidationError):
            AutomationScoreRequest(source_uri="x.csv", min_frequency=0)
