"""Unit tests for ProcessService with artificial bottleneck fixtures."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from workflow_intelligence.schemas.process import (
    AutomationScoreRequest,
    BottleneckRequest,
    ProcessAnalyzeRequest,
)
from workflow_intelligence.services.process_service import (
    ProcessService,
    ProcessServiceError,
)


@pytest.fixture
def process_service() -> ProcessService:
    """Provide a ProcessService instance."""
    return ProcessService()


@pytest.fixture
def bottleneck_event_log(tmp_path: Path) -> str:
    """Create a CSV event log with a 72-hour bottleneck at Manager Approval.

    Cases:
    - PO-001..PO-100: normal flow with 2-hour Manager Approval
    - PO-101..PO-110: delayed flow with 72-hour Manager Approval (bottleneck)
    """
    rows: list[dict[str, str]] = []

    # Normal cases (100 cases, 2-hour Manager Approval)
    for i in range(1, 101):
        case = "PO-" + str(i).zfill(4)
        rows.extend(
            [
                {
                    "case_id": case,
                    "activity": "Purchase Request",
                    "timestamp": "2026-01-01T09:00:00",
                },
                {
                    "case_id": case,
                    "activity": "Manager Approval",
                    "timestamp": "2026-01-01T11:00:00",
                },
                {
                    "case_id": case,
                    "activity": "Finance Approval",
                    "timestamp": "2026-01-01T13:00:00",
                },
                {
                    "case_id": case,
                    "activity": "Vendor Selection",
                    "timestamp": "2026-01-01T15:00:00",
                },
                {
                    "case_id": case,
                    "activity": "Purchase Order",
                    "timestamp": "2026-01-01T17:00:00",
                },
            ]
        )

    # Delayed cases (10 cases, 72-hour Manager Approval = artificial bottleneck)
    for i in range(101, 111):
        case = "PO-" + str(i).zfill(4)
        rows.extend(
            [
                {
                    "case_id": case,
                    "activity": "Purchase Request",
                    "timestamp": "2026-01-02T09:00:00",
                },
                {
                    "case_id": case,
                    "activity": "Manager Approval",
                    "timestamp": "2026-01-05T09:00:00",
                },
                {
                    "case_id": case,
                    "activity": "Finance Approval",
                    "timestamp": "2026-01-05T11:00:00",
                },
                {
                    "case_id": case,
                    "activity": "Vendor Selection",
                    "timestamp": "2026-01-05T13:00:00",
                },
                {
                    "case_id": case,
                    "activity": "Purchase Order",
                    "timestamp": "2026-01-05T15:00:00",
                },
            ]
        )

    df = pd.DataFrame(rows)
    path = tmp_path / "bottleneck_events.csv"
    df.to_csv(path, index=False)
    return str(path)


@pytest.fixture
def nonstandard_event_log(tmp_path: Path) -> str:
    """Create a CSV with non-standard column names: Case_ID, Act, Event_Time."""
    rows = [
        {"Case_ID": "C1", "Act": "A", "Event_Time": "2026-01-01T09:00:00"},
        {"Case_ID": "C1", "Act": "B", "Event_Time": "2026-01-01T10:00:00"},
        {"Case_ID": "C2", "Act": "A", "Event_Time": "2026-01-01T09:00:00"},
        {"Case_ID": "C2", "Act": "B", "Event_Time": "2026-01-01T11:00:00"},
    ]
    df = pd.DataFrame(rows)
    path = tmp_path / "nonstandard_events.csv"
    df.to_csv(path, index=False)
    return str(path)


@pytest.fixture
def variant_event_log(tmp_path: Path) -> str:
    """Create a CSV event log with multiple execution path variants.

    Variant A: Purchase Request -> Manager Approval -> Finance Approval ->
        Vendor Selection -> Purchase Order (80 cases)
    Variant B: Purchase Request -> Manager Approval -> Vendor Selection ->
        Purchase Order (20 cases, skips Finance)
    """
    rows: list[dict[str, str]] = []

    for i in range(1, 81):
        case = "PO-" + str(i).zfill(4)
        rows.extend(
            [
                {
                    "case_id": case,
                    "activity": "Purchase Request",
                    "timestamp": "2026-01-01T09:" + str(i % 60).zfill(2) + ":00",
                },
                {
                    "case_id": case,
                    "activity": "Manager Approval",
                    "timestamp": "2026-01-01T10:" + str(i % 60).zfill(2) + ":00",
                },
                {
                    "case_id": case,
                    "activity": "Finance Approval",
                    "timestamp": "2026-01-01T11:" + str(i % 60).zfill(2) + ":00",
                },
                {
                    "case_id": case,
                    "activity": "Vendor Selection",
                    "timestamp": "2026-01-01T12:" + str(i % 60).zfill(2) + ":00",
                },
                {
                    "case_id": case,
                    "activity": "Purchase Order",
                    "timestamp": "2026-01-01T13:" + str(i % 60).zfill(2) + ":00",
                },
            ]
        )

    for i in range(81, 101):
        case = "PO-" + str(i).zfill(4)
        rows.extend(
            [
                {
                    "case_id": case,
                    "activity": "Purchase Request",
                    "timestamp": "2026-01-02T09:" + str(i % 60).zfill(2) + ":00",
                },
                {
                    "case_id": case,
                    "activity": "Manager Approval",
                    "timestamp": "2026-01-02T10:" + str(i % 60).zfill(2) + ":00",
                },
                {
                    "case_id": case,
                    "activity": "Vendor Selection",
                    "timestamp": "2026-01-02T11:" + str(i % 60).zfill(2) + ":00",
                },
                {
                    "case_id": case,
                    "activity": "Purchase Order",
                    "timestamp": "2026-01-02T12:" + str(i % 60).zfill(2) + ":00",
                },
            ]
        )

    df = pd.DataFrame(rows)
    path = tmp_path / "variant_events.csv"
    df.to_csv(path, index=False)
    return str(path)


class TestProcessServiceAnalyze:
    """Tests for analyze_business_process."""

    def test_analyze_basic_statistics(
        self, process_service: ProcessService, bottleneck_event_log: str
    ) -> None:
        """Should return correct case and event counts."""
        request = ProcessAnalyzeRequest(
            source_uri=bottleneck_event_log,
            case_id_col="case_id",
            activity_col="activity",
            timestamp_col="timestamp",
        )
        result = process_service.analyze_business_process(request)

        assert result.total_cases == 110
        assert result.total_events == 550
        assert "Manager Approval" in result.unique_activities
        assert "Finance Approval" in result.unique_activities
        assert result.variant_count >= 1
        assert result.average_case_duration_hours > 0

    def test_analyze_transitions(
        self, process_service: ProcessService, bottleneck_event_log: str
    ) -> None:
        """Should return transition metrics between activities."""
        request = ProcessAnalyzeRequest(
            source_uri=bottleneck_event_log,
            case_id_col="case_id",
            activity_col="activity",
            timestamp_col="timestamp",
        )
        result = process_service.analyze_business_process(request)

        assert len(result.transitions) > 0
        manager_to_finance = next(
            (
                t
                for t in result.transitions
                if t.source_activity == "Manager Approval"
                and t.target_activity == "Finance Approval"
            ),
            None,
        )
        assert manager_to_finance is not None
        assert manager_to_finance.transition_count > 0

    def test_analyze_top_variants(
        self, process_service: ProcessService, variant_event_log: str
    ) -> None:
        """Should identify top execution variants."""
        request = ProcessAnalyzeRequest(
            source_uri=variant_event_log,
            case_id_col="case_id",
            activity_col="activity",
            timestamp_col="timestamp",
        )
        result = process_service.analyze_business_process(request)

        assert result.variant_count == 2
        assert len(result.top_variants) == 2

    def test_analyze_missing_file(self, process_service: ProcessService) -> None:
        """Should raise ProcessServiceError for missing file."""
        request = ProcessAnalyzeRequest(
            source_uri="/nonexistent/path.csv",
            case_id_col="case_id",
            activity_col="activity",
            timestamp_col="timestamp",
        )
        with pytest.raises(ProcessServiceError):
            process_service.analyze_business_process(request)


class TestProcessServiceBottlenecks:
    """Tests for identify_bottlenecks."""

    def test_detects_artificial_bottleneck(
        self, process_service: ProcessService, bottleneck_event_log: str
    ) -> None:
        """Should detect the 72-hour Manager Approval delay as a bottleneck."""
        request = BottleneckRequest(
            source_uri=bottleneck_event_log,
            threshold_std_dev=1.0,
        )
        report = process_service.identify_bottlenecks(request)

        assert report.source_uri == bottleneck_event_log
        assert report.total_stages_analyzed > 0
        assert len(report.bottlenecks) > 0

        # The Manager Approval -> Finance Approval transition for delayed
        # cases should be flagged
        bottleneck_transitions = {b.stage_transition for b in report.bottlenecks}
        assert any("Manager Approval" in t for t in bottleneck_transitions)

    def test_bottleneck_has_z_score(
        self, process_service: ProcessService, bottleneck_event_log: str
    ) -> None:
        """Bottleneck items should have z-scores exceeding the threshold."""
        request = BottleneckRequest(
            source_uri=bottleneck_event_log,
            threshold_std_dev=2.0,
        )
        report = process_service.identify_bottlenecks(request)

        for b in report.bottlenecks:
            assert b.z_score >= 1.0
            assert b.avg_duration_hours > 0
            assert b.baseline_duration_hours > 0
            assert b.affected_case_count > 0
            assert b.recommendation

    def test_no_bottlenecks_with_high_threshold(
        self, process_service: ProcessService, bottleneck_event_log: str
    ) -> None:
        """Very high threshold should yield no bottlenecks."""
        request = BottleneckRequest(
            source_uri=bottleneck_event_log,
            threshold_std_dev=100.0,
        )
        report = process_service.identify_bottlenecks(request)
        assert len(report.bottlenecks) == 0

    def test_detects_bottleneck_with_nonstandard_columns(
        self, process_service: ProcessService, nonstandard_event_log: str
    ) -> None:
        """identify_bottlenecks should auto-detect Case_ID/Act/Event_Time columns."""
        request = BottleneckRequest(
            source_uri=nonstandard_event_log,
            threshold_std_dev=1.0,
        )
        report = process_service.identify_bottlenecks(request)
        assert report.source_uri == nonstandard_event_log
        assert report.total_stages_analyzed > 0


class TestProcessServiceAutomation:
    """Tests for score_automation_opportunities."""

    def test_scores_activities(
        self, process_service: ProcessService, bottleneck_event_log: str
    ) -> None:
        """Should return ranked automation candidates."""
        request = AutomationScoreRequest(
            source_uri=bottleneck_event_log,
            min_frequency=10,
        )
        report = process_service.score_automation_opportunities(request)

        assert len(report.candidates) > 0
        assert report.methodology
        assert all(c.rank >= 1 for c in report.candidates)
        assert all(c.execution_frequency >= 10 for c in report.candidates)

    def test_ranks_by_automation_potential(
        self, process_service: ProcessService, bottleneck_event_log: str
    ) -> None:
        """Candidates should be ranked by automation potential score descending."""
        request = AutomationScoreRequest(
            source_uri=bottleneck_event_log,
            min_frequency=10,
        )
        report = process_service.score_automation_opportunities(request)

        scores = [c.automation_potential_score for c in report.candidates]
        assert scores == sorted(scores, reverse=True)

    def test_filters_by_min_frequency(
        self, process_service: ProcessService, bottleneck_event_log: str
    ) -> None:
        """Should filter out activities below min_frequency."""
        request = AutomationScoreRequest(
            source_uri=bottleneck_event_log,
            min_frequency=1000,
        )
        report = process_service.score_automation_opportunities(request)
        assert len(report.candidates) == 0

    def test_scores_with_nonstandard_columns(
        self, process_service: ProcessService, nonstandard_event_log: str
    ) -> None:
        """score_automation_opportunities should auto-detect non-standard columns."""
        request = AutomationScoreRequest(
            source_uri=nonstandard_event_log,
            min_frequency=1,
        )
        report = process_service.score_automation_opportunities(request)
        assert len(report.candidates) > 0
        assert report.methodology
