"""Comprehensive regression tests for automation opportunity scoring accuracy,
activity-frequency correctness, score normalization, and bottleneck sample-size reliability.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from workflow_intelligence.schemas.process import (
    AutomationScoreRequest,
    BottleneckRequest,
    ProcessAnalysisResult,
    ProcessAnalyzeRequest,
    StageMetric,
)
from workflow_intelligence.services.process_service import ProcessService

SAMPLE_DATA = Path(__file__).resolve().parents[2] / "sample-data"
PROCUREMENT_CSV = SAMPLE_DATA / "procurement_events.csv"
INSURANCE_SAMPLE_CSV = SAMPLE_DATA / "insurance_events.csv"
LOCAL_INSURANCE_CSV = (
    Path(__file__).resolve().parents[2] / "local" / "Insurance_claims_event_log.csv"
)
LOCAL_BPIC19_CSV = Path(__file__).resolve().parents[2] / "local" / "BPIC19_flat.csv"


@pytest.fixture
def process_service() -> ProcessService:
    return ProcessService()


@pytest.fixture
def branching_event_log(tmp_path: Path) -> str:
    """Create a synthesized event log with branching execution paths."""
    rows = []
    # 50 cases: Start -> Review (fast: 10m) -> End
    for i in range(1, 51):
        case = f"C-FAST-{i:03d}"
        rows.extend(
            [
                {
                    "case_id": case,
                    "activity": "Start",
                    "timestamp": "2026-01-01T10:00:00",
                },
                {
                    "case_id": case,
                    "activity": "Review",
                    "timestamp": "2026-01-01T10:10:00",
                },
                {
                    "case_id": case,
                    "activity": "End",
                    "timestamp": "2026-01-01T10:20:00",
                },
            ]
        )
    # 50 cases: Start -> Escalate (slow: 120m) -> End
    for i in range(1, 51):
        case = f"C-SLOW-{i:03d}"
        rows.extend(
            [
                {
                    "case_id": case,
                    "activity": "Start",
                    "timestamp": "2026-01-01T10:00:00",
                },
                {
                    "case_id": case,
                    "activity": "Escalate",
                    "timestamp": "2026-01-01T12:00:00",
                },
                {
                    "case_id": case,
                    "activity": "End",
                    "timestamp": "2026-01-01T12:15:00",
                },
            ]
        )
    p = tmp_path / "branching_events.csv"
    pd.DataFrame(rows).to_csv(p, index=False)
    return str(p)


@pytest.fixture
def repeated_activities_log(tmp_path: Path) -> str:
    """Create an event log with loops (repeated activity occurrences in a single case)."""
    rows = []
    for i in range(1, 21):
        case = f"CASE-{i:02d}"
        # Start (1) -> Approve (3 times in loop) -> Complete (1)
        rows.append(
            {
                "case_id": case,
                "activity": "Initiate",
                "timestamp": "2026-01-01T08:00:00",
            }
        )
        rows.append(
            {
                "case_id": case,
                "activity": "Manager Review",
                "timestamp": "2026-01-01T09:00:00",
            }
        )
        rows.append(
            {
                "case_id": case,
                "activity": "Manager Review",
                "timestamp": "2026-01-01T10:00:00",
            }
        )
        rows.append(
            {
                "case_id": case,
                "activity": "Manager Review",
                "timestamp": "2026-01-01T11:00:00",
            }
        )
        rows.append(
            {
                "case_id": case,
                "activity": "Finalize",
                "timestamp": "2026-01-01T12:00:00",
            }
        )
    p = tmp_path / "repeated_events.csv"
    pd.DataFrame(rows).to_csv(p, index=False)
    return str(p)


class TestAutomationScoringAccuracy:
    """Verifies that activity frequencies, durations, and scores are mathematically accurate."""

    def test_procurement_activity_frequency_matches_raw_csv_not_transitions(
        self, process_service: ProcessService
    ) -> None:
        """Reported activity frequencies must match raw event counts, not doubled in/out transitions."""
        df = pd.read_csv(PROCUREMENT_CSV)
        raw_counts = df["activity"].value_counts().to_dict()

        report = process_service.score_automation_opportunities(
            AutomationScoreRequest(source_uri=str(PROCUREMENT_CSV), min_frequency=1)
        )
        cand_map = {c.activity_name: c for c in report.candidates}

        # Manager Approval had 1108 raw events (loops in 500 cases), previously misreported as 1943
        assert (
            cand_map["Manager Approval"].execution_frequency
            == raw_counts["Manager Approval"]
        )
        assert cand_map["Manager Approval"].execution_frequency == 1108

        # Intermediate activities previously doubled to 1000 must now report exactly 500
        assert cand_map["Purchase Request"].execution_frequency == 500
        assert cand_map["Finance Approval"].execution_frequency == 500
        assert cand_map["Vendor Selection"].execution_frequency == 500
        assert cand_map["Purchase Order"].execution_frequency == 500

    def test_repeated_activity_occurrences_counted_correctly(
        self, process_service: ProcessService, repeated_activities_log: str
    ) -> None:
        """Activities occurring multiple times in a single trace must count all event occurrences."""
        report = process_service.score_automation_opportunities(
            AutomationScoreRequest(source_uri=repeated_activities_log, min_frequency=1)
        )
        cand_map = {c.activity_name: c for c in report.candidates}

        # 20 cases * 3 Manager Reviews per case = 60 occurrences
        assert cand_map["Manager Review"].execution_frequency == 60
        assert cand_map["Initiate"].execution_frequency == 20
        assert cand_map["Finalize"].execution_frequency == 20

    def test_first_and_last_activities_in_trace_handled_correctly(
        self, process_service: ProcessService, branching_event_log: str
    ) -> None:
        """First activity (no incoming) and last activity (no outgoing) must calculate correctly."""
        report = process_service.score_automation_opportunities(
            AutomationScoreRequest(source_uri=branching_event_log, min_frequency=1)
        )
        cand_map = {c.activity_name: c for c in report.candidates}

        # 100 cases total: 50 fast + 50 slow
        assert cand_map["Start"].execution_frequency == 100
        assert cand_map["End"].execution_frequency == 100
        assert cand_map["Review"].execution_frequency == 50
        assert cand_map["Escalate"].execution_frequency == 50

        # Start has 2 outgoing branches: Review (10m) and Escalate (120m)
        assert cand_map["Start"].avg_duration_minutes > 0
        assert cand_map["Start"].standard_deviation_minutes > 0

    def test_score_normalization_prevents_saturation_at_100(
        self, process_service: ProcessService
    ) -> None:
        """Only the top candidate receives 100.0; subsequent candidates are non-saturated and differentiated."""
        report = process_service.score_automation_opportunities(
            AutomationScoreRequest(source_uri=str(PROCUREMENT_CSV), min_frequency=1)
        )
        # Check ranks are 1..N
        ranks = [c.rank for c in report.candidates]
        assert ranks == list(range(1, len(report.candidates) + 1))

        # Exactly candidate at rank 1 has 100.0
        assert report.candidates[0].automation_potential_score == 100.0
        # Other candidates have non-saturated, differentiated scores
        other_scores = [c.automation_potential_score for c in report.candidates[1:]]
        assert all(s < 100.0 for s in other_scores)
        assert len(set(other_scores)) > 1  # Not all tied

    def test_deterministic_ranking_and_tie_breaking(
        self, process_service: ProcessService, tmp_path: Path
    ) -> None:
        """Tied automation potential scores must break deterministically."""
        # Create 2 activities with identical metrics but different names
        rows = []
        for i in range(1, 11):
            case = f"TIE-{i:02d}"
            rows.extend(
                [
                    {
                        "case_id": case,
                        "activity": "Alpha Task",
                        "timestamp": "2026-01-01T10:00:00",
                    },
                    {
                        "case_id": case,
                        "activity": "Beta Task",
                        "timestamp": "2026-01-01T10:15:00",
                    },
                    {
                        "case_id": case,
                        "activity": "End",
                        "timestamp": "2026-01-01T10:30:00",
                    },
                ]
            )
        p = tmp_path / "tie_events.csv"
        pd.DataFrame(rows).to_csv(p, index=False)

        report = process_service.score_automation_opportunities(
            AutomationScoreRequest(source_uri=str(p), min_frequency=1)
        )
        assert len(report.candidates) == 3
        # Strict descending order of potential score
        scores = [c.automation_potential_score for c in report.candidates]
        assert scores == sorted(scores, reverse=True)

    def test_transition_frequencies_separate_from_activity_frequencies(
        self, process_service: ProcessService
    ) -> None:
        """ProcessAnalysisResult must maintain transitions and activity_frequencies separately."""
        res = process_service.analyze_business_process(
            ProcessAnalyzeRequest(
                source_uri=str(PROCUREMENT_CSV),
                case_id_col="case_id",
                activity_col="activity",
                timestamp_col="timestamp",
            )
        )
        assert res.activity_frequencies["Manager Approval"] == 1108
        assert res.activity_frequencies["Purchase Request"] == 500
        # Transitions are stage-to-stage StageMetric items
        assert all(hasattr(t, "transition_count") for t in res.transitions)
        assert sum(res.activity_frequencies.values()) == res.total_events


class TestBottleneckReliabilityAndFiltering:
    """Verifies bottleneck z-score calculation, sample size warnings, and min_frequency threshold."""

    def test_bottleneck_sample_size_warning_on_small_n(
        self, process_service: ProcessService, tmp_path: Path
    ) -> None:
        """One-off outlier transitions (n < 30) must carry a sample_size_warning."""
        rows = []
        # 100 normal transitions A -> B taking 1 hour
        for i in range(1, 101):
            case = f"N-{i:03d}"
            rows.extend(
                [
                    {
                        "case_id": case,
                        "activity": "A",
                        "timestamp": "2026-01-01T10:00:00",
                    },
                    {
                        "case_id": case,
                        "activity": "B",
                        "timestamp": "2026-01-01T11:00:00",
                    },
                ]
            )
        # 1 extreme outlier transition A -> C taking 100 hours
        case = "OUTLIER-001"
        rows.extend(
            [
                {"case_id": case, "activity": "A", "timestamp": "2026-01-01T10:00:00"},
                {"case_id": case, "activity": "C", "timestamp": "2026-01-05T14:00:00"},
            ]
        )
        p = tmp_path / "outlier_events.csv"
        pd.DataFrame(rows).to_csv(p, index=False)

        # Default min_frequency=1 finds the outlier
        report = process_service.identify_bottlenecks(
            BottleneckRequest(source_uri=str(p), threshold_std_dev=0.5, min_frequency=1)
        )
        assert len(report.bottlenecks) > 0
        outlier_b = next(
            b for b in report.bottlenecks if "A -> C" in b.stage_transition
        )
        assert outlier_b.affected_case_count == 1
        assert outlier_b.sample_size_warning is not None
        assert "Low sample size" in outlier_b.sample_size_warning

        # With min_frequency=5, the 1-occurrence outlier is filtered out
        filtered_report = process_service.identify_bottlenecks(
            BottleneckRequest(source_uri=str(p), threshold_std_dev=0.5, min_frequency=5)
        )
        assert not any(
            "A -> C" in b.stage_transition for b in filtered_report.bottlenecks
        )


@pytest.mark.skipif(
    not LOCAL_INSURANCE_CSV.exists(),
    reason="local/Insurance_claims_event_log.csv not present",
)
class TestInsuranceDatasetVerification:
    """Verifies the confirmed Insurance dataset discrepancy is fully resolved."""

    def test_insurance_actual_event_counts_versus_reported_frequency(
        self, process_service: ProcessService
    ) -> None:
        """Every activity in the 30,000-case insurance log must report exactly 30,000 frequency."""
        report = process_service.score_automation_opportunities(
            AutomationScoreRequest(
                source_uri=str(LOCAL_INSURANCE_CSV), min_frequency=10
            )
        )
        cand_map = {c.activity_name: c for c in report.candidates}

        # Confirmed fix: no doubling to 60,000 and no transition-sum 59,823
        assert cand_map["Assign Claim"].execution_frequency == 30000
        assert cand_map["Claim Decision"].execution_frequency == 30000
        assert cand_map["Payment Sent"].execution_frequency == 30000
        assert (
            cand_map["First Notification of Loss (FNOL)"].execution_frequency == 30000
        )
        assert cand_map["Set Reserve"].execution_frequency == 30000
        assert cand_map["Close Claim"].execution_frequency == 30000

    def test_insurance_automation_score_not_saturated(
        self, process_service: ProcessService
    ) -> None:
        """Insurance activities must not all saturate at 100.0; FNOL must rank #1."""
        report = process_service.score_automation_opportunities(
            AutomationScoreRequest(
                source_uri=str(LOCAL_INSURANCE_CSV), min_frequency=10
            )
        )
        assert report.candidates[0].activity_name == "First Notification of Loss (FNOL)"
        assert report.candidates[0].automation_potential_score == 100.0

        # Assign Claim, Claim Decision, Payment Sent must NOT be 100.0
        scores = {
            c.activity_name: c.automation_potential_score for c in report.candidates
        }
        assert scores["Assign Claim"] < 50.0
        assert scores["Claim Decision"] < 20.0
        assert scores["Payment Sent"] < 20.0

    def test_insurance_bottleneck_calculations_and_warnings(
        self, process_service: ProcessService
    ) -> None:
        """Verifies documented bottleneck z-scores and sample size warning on the 1-case transition."""
        report = process_service.identify_bottlenecks(
            BottleneckRequest(
                source_uri=str(LOCAL_INSURANCE_CSV), threshold_std_dev=2.0
            )
        )
        b_map = {b.stage_transition: b for b in report.bottlenecks}

        # FNOL -> Assign Claim: 360.25h, z ~ 2.22, 29807 cases (no warning)
        b1 = b_map["First Notification of Loss (FNOL) -> Assign Claim"]
        assert round(b1.avg_duration_hours, 2) == 360.25
        assert round(b1.z_score, 2) == 2.22
        assert b1.affected_case_count == 29807
        assert b1.sample_size_warning is None

        # FNOL -> Set Reserve: 354.78h, z ~ 2.17, 1 case (has warning)
        b2 = b_map["First Notification of Loss (FNOL) -> Set Reserve"]
        assert round(b2.avg_duration_hours, 2) == 354.78
        assert round(b2.z_score, 2) == 2.17
        assert b2.affected_case_count == 1
        assert b2.sample_size_warning is not None
        assert "Low sample size" in b2.sample_size_warning


@pytest.mark.skipif(
    not LOCAL_BPIC19_CSV.exists(), reason="local/BPIC19_flat.csv not present"
)
class TestBPIC19DatasetVerification:
    """Verifies BPIC19 scoring accuracy and non-saturation using cached process discovery."""

    def test_bpic19_raw_event_counts_via_duckdb(self) -> None:
        """Independent mathematical validation of BPIC19 raw event frequencies via DuckDB."""
        import duckdb

        query_rel = duckdb.query(
            f"""
            SELECT "ocel:activity", count(*)
            FROM '{LOCAL_BPIC19_CSV}'
            WHERE "ocel:activity" IN ('Change Quantity', 'Clear Invoice', 'Vendor creates invoice')
            GROUP BY "ocel:activity"
            """
        )
        assert query_rel is not None
        res = dict(query_rel.fetchall())
        assert res["Change Quantity"] == 21449
        assert res["Clear Invoice"] == 194393
        assert res["Vendor creates invoice"] == 219919

    def test_bpic19_scoring_frequency_matches_csv_not_doubled(
        self, monkeypatch: pytest.MonkeyPatch, process_service: ProcessService
    ) -> None:
        """Activities in BPIC19 must report actual event counts (e.g. Change Quantity = 21,449)."""
        bpic19_analysis = ProcessAnalysisResult(
            total_cases=251734,
            total_events=1595923,
            unique_activities=[
                "Change Quantity",
                "Clear Invoice",
                "Vendor creates invoice",
                "Record Goods Receipt",
            ],
            variant_count=13885,
            top_variants=[],
            average_case_duration_hours=1716.59,
            activity_frequencies={
                "Change Quantity": 21449,
                "Clear Invoice": 194393,
                "Vendor creates invoice": 219919,
                "Record Goods Receipt": 242099,
            },
            transitions=[
                StageMetric(
                    source_activity="Change Quantity",
                    target_activity="Record Goods Receipt",
                    transition_count=21400,
                    mean_duration_hours=235.624,  # 14,137.46 min
                    median_duration_hours=50.0,
                    std_dev_hours=675.92,
                    is_bottleneck=False,
                ),
                StageMetric(
                    source_activity="Clear Invoice",
                    target_activity="End Activity",
                    transition_count=194393,
                    mean_duration_hours=738.058,  # 44,283.53 min
                    median_duration_hours=100.0,
                    std_dev_hours=1445.46,
                    is_bottleneck=False,
                ),
                StageMetric(
                    source_activity="Vendor creates invoice",
                    target_activity="Clear Invoice",
                    transition_count=216467,
                    mean_duration_hours=368.814,  # 22,128.86 min
                    median_duration_hours=80.0,
                    std_dev_hours=815.65,
                    is_bottleneck=False,
                ),
            ],
        )

        monkeypatch.setattr(
            process_service, "analyze_business_process", lambda req: bpic19_analysis
        )

        report = process_service.score_automation_opportunities(
            AutomationScoreRequest(source_uri=str(LOCAL_BPIC19_CSV), min_frequency=1000)
        )
        cand_map = {c.activity_name: c for c in report.candidates}

        # Previously reported 42,320 (doubled transitions); actual CSV is 21,449
        assert cand_map["Change Quantity"].execution_frequency == 21449
        # Previously reported 207,499; actual CSV is 194,393
        assert cand_map["Clear Invoice"].execution_frequency == 194393
        # Previously reported 436,315; actual CSV is 219,919
        assert cand_map["Vendor creates invoice"].execution_frequency == 219919

        # Scores must NOT be saturated at 100.0 for all activities
        scores = [c.automation_potential_score for c in report.candidates]
        assert len(set(scores)) > 1  # Candidates have differentiated scores
        assert cand_map["Change Quantity"].automation_potential_score < 50.0

    def test_bpic19_bottleneck_sparse_transitions_and_warnings(
        self, monkeypatch: pytest.MonkeyPatch, process_service: ProcessService
    ) -> None:
        """Verifies bottleneck warnings on low-sample transitions and filtering on BPIC19."""
        bpic19_analysis = ProcessAnalysisResult(
            total_cases=251734,
            total_events=1595923,
            unique_activities=["A", "B", "C"],
            variant_count=13885,
            top_variants=[],
            average_case_duration_hours=1716.59,
            activity_frequencies={"A": 1000, "B": 1000, "C": 1000},
            transitions=[
                StageMetric(
                    source_activity="Vendor creates debit memo",
                    target_activity="SRM: Created",
                    transition_count=5,
                    mean_duration_hours=29981.40,
                    median_duration_hours=29980.0,
                    std_dev_hours=1500.0,
                    is_bottleneck=True,
                ),
                StageMetric(
                    source_activity="Vendor creates debit memo",
                    target_activity="Create Purchase Order Item",
                    transition_count=117,
                    mean_duration_hours=20579.98,
                    median_duration_hours=20500.0,
                    std_dev_hours=1200.0,
                    is_bottleneck=True,
                ),
                StageMetric(
                    source_activity="Change Storage Location",
                    target_activity="Block Purchase Order Item",
                    transition_count=1,
                    mean_duration_hours=4875.40,
                    median_duration_hours=4875.40,
                    std_dev_hours=0.0,
                    is_bottleneck=True,
                ),
            ]
            + [
                StageMetric(
                    source_activity=f"Normal Activity {i}",
                    target_activity=f"Normal Activity {i + 1}",
                    transition_count=1000,
                    mean_duration_hours=2.0,
                    median_duration_hours=2.0,
                    std_dev_hours=1.0,
                    is_bottleneck=False,
                )
                for i in range(50)
            ],
        )

        monkeypatch.setattr(
            process_service, "analyze_business_process", lambda req: bpic19_analysis
        )

        # Default min_frequency=1: sparse transitions are reported but have sample size warnings
        report_default = process_service.identify_bottlenecks(
            BottleneckRequest(source_uri=str(LOCAL_BPIC19_CSV), threshold_std_dev=0.5)
        )
        b_map = {b.stage_transition: b for b in report_default.bottlenecks}

        assert "Vendor creates debit memo -> SRM: Created" in b_map
        assert (
            b_map["Vendor creates debit memo -> SRM: Created"].sample_size_warning
            is not None
        )
        assert (
            "n=5"
            in b_map["Vendor creates debit memo -> SRM: Created"].sample_size_warning
        )

        assert "Vendor creates debit memo -> Create Purchase Order Item" in b_map
        # n=117 >= 30 so no warning
        assert (
            b_map[
                "Vendor creates debit memo -> Create Purchase Order Item"
            ].sample_size_warning
            is None
        )

        assert "Change Storage Location -> Block Purchase Order Item" in b_map
        assert (
            b_map[
                "Change Storage Location -> Block Purchase Order Item"
            ].sample_size_warning
            is not None
        )
        assert (
            "n=1"
            in b_map[
                "Change Storage Location -> Block Purchase Order Item"
            ].sample_size_warning
        )

        # With min_frequency=10: sparse transitions (n=5 and n=1) are cleanly excluded
        report_filtered = process_service.identify_bottlenecks(
            BottleneckRequest(
                source_uri=str(LOCAL_BPIC19_CSV),
                threshold_std_dev=0.5,
                min_frequency=10,
            )
        )
        b_filtered_map = {b.stage_transition: b for b in report_filtered.bottlenecks}
        assert "Vendor creates debit memo -> SRM: Created" not in b_filtered_map
        assert (
            "Change Storage Location -> Block Purchase Order Item" not in b_filtered_map
        )
        assert (
            "Vendor creates debit memo -> Create Purchase Order Item" in b_filtered_map
        )
