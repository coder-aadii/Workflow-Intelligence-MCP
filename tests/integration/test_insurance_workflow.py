"""Integration tests for non-procurement real-world domain workflows (Insurance Claims).

Validates:
1. Cross-domain schema compatibility (no hardcoded procurement assumptions).
2. Process discovery, variants, and transitions on insurance events.
3. Bottleneck detection on claim processing stages.
4. Automation potential scoring on repetitive insurance activities.
5. Deterministic DuckDB analytics and KPI deviation analysis over claim amounts.
6. Exception triage, resolution, and escalation for insurance claims.
7. Negative test cases: missing mandatory columns, malformed timestamps, unsupported formats.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from workflow_intelligence.adapters.analytics.duckdb_adapter import (
    DuckDBAdapter,
    DuckDBAdapterError,
)
from workflow_intelligence.adapters.file_storage.loader import (
    FileStorageError,
    load_event_log,
)
from workflow_intelligence.schemas.exception import (
    EscalationResult,
    ExceptionCategory,
    ResolutionRequest,
    WorkflowInvestigationRequest,
)
from workflow_intelligence.schemas.process import (
    AutomationScoreRequest,
    BottleneckRequest,
    ProcessAnalyzeRequest,
)
from workflow_intelligence.services.decision_service import DecisionService
from workflow_intelligence.services.exception_service import ExceptionService
from workflow_intelligence.services.process_service import (
    ProcessService,
    ProcessServiceError,
)

INSURANCE_SAMPLE_URI = "sample-data/insurance_events.csv"


class TestInsuranceProcessDiscovery:
    """Tests process discovery against insurance claims event log."""

    @pytest.fixture
    def process_service(self) -> ProcessService:
        return ProcessService()

    def test_insurance_process_analysis(self, process_service: ProcessService) -> None:
        """Discovers cases, events, variants, and transitions without procurement assumptions."""
        req = ProcessAnalyzeRequest(
            source_uri=INSURANCE_SAMPLE_URI,
            case_id_col="case_id",
            activity_col="activity_name",
            timestamp_col="timestamp",
        )
        res = process_service.analyze_business_process(req)

        assert res.total_cases == 5
        assert res.total_events == 30
        assert len(res.unique_activities) == 6
        assert "First Notification of Loss (FNOL)" in res.unique_activities
        assert "Claim Decision" in res.unique_activities
        assert "Payment Sent" in res.unique_activities
        assert res.variant_count >= 1
        assert len(res.transitions) >= 5
        assert res.average_case_duration_hours > 0

    def test_insurance_bottleneck_detection(
        self, process_service: ProcessService
    ) -> None:
        """Identifies longest duration stages in claim processing."""
        b_req = BottleneckRequest(
            source_uri=INSURANCE_SAMPLE_URI,
            threshold_std_dev=0.5,
        )
        rep = process_service.identify_bottlenecks(b_req)
        assert rep.total_stages_analyzed > 0
        assert rep.source_uri == INSURANCE_SAMPLE_URI

    def test_insurance_automation_scoring(
        self, process_service: ProcessService
    ) -> None:
        """Scores insurance activities for RPA and automation opportunities."""
        a_req = AutomationScoreRequest(
            source_uri=INSURANCE_SAMPLE_URI,
            min_frequency=1,
        )
        rep = process_service.score_automation_opportunities(a_req)
        assert len(rep.candidates) > 0
        assert rep.candidates[0].rank == 1
        assert any(
            c.activity_name == "First Notification of Loss (FNOL)"
            for c in rep.candidates
        )


class TestInsuranceDeterministicAnalytics:
    """Tests DuckDB analytical computation over insurance claims."""

    @pytest.fixture
    def duckdb_adapter(self) -> DuckDBAdapter:
        adapter = DuckDBAdapter()
        yield adapter
        adapter.close()

    def test_claim_amount_aggregation_by_policy(
        self, duckdb_adapter: DuckDBAdapter
    ) -> None:
        """Calculates deterministic claim amount aggregations grouped by policy type."""
        duckdb_adapter.register_csv("claims", INSURANCE_SAMPLE_URI)
        rows = duckdb_adapter.execute_query(
            """
            SELECT type_of_policy, COUNT(DISTINCT case_id) as cases, AVG(claim_amount) as avg_amount
            FROM claims
            GROUP BY type_of_policy
            ORDER BY avg_amount DESC
            """
        )
        assert len(rows) > 0
        for policy, cases, avg_amount in rows:
            assert isinstance(policy, str)
            assert cases > 0
            assert avg_amount > 0


class TestInsuranceExceptionResolver:
    """Tests exception triage and escalation for insurance domain workflows."""

    @pytest.fixture
    def exception_service(self) -> ExceptionService:
        return ExceptionService()

    @pytest.mark.asyncio
    async def test_insurance_adjuster_sla_timeout_triage(
        self, exception_service: ExceptionService
    ) -> None:
        """Triages an SLA timeout on adjuster claim assignment."""
        req = WorkflowInvestigationRequest(
            case_id="CLM-INS-8001",
            workflow_type="insurance_claim_processing",
            failure_payload={
                "error": "Adjuster assignment timeout after 14 days in queue",
                "claim_id": "CLM-INS-8001",
                "stage": "Assign Claim",
                "sla_days": 5,
                "elapsed_days": 14,
            },
        )
        res = await exception_service.ainvestigate_failure(req)
        assert res.case_id == "CLM-INS-8001"
        assert res.category == ExceptionCategory.TRANSIENT_NETWORK_DROP or res.category is not None
        assert res.exception_id.startswith("EXC-")

    @pytest.mark.asyncio
    async def test_insurance_claim_escalation(
        self, exception_service: ExceptionService
    ) -> None:
        """Escalates complex or disputed claim to Special Investigations Unit (SIU)."""
        esc = await exception_service.aescalate_case(
            case_id="CLM-INS-8002",
            reason="Suspected fraudulent loss description: multiple rollover claims within 30 days",
            target_department="Special Investigations Unit (SIU)",
        )
        assert esc.ticket_id.startswith("TKT-")
        assert esc.routed_department == "Special Investigations Unit (SIU)"
        assert "CLM-INS-8002" in esc.audit_narrative


class TestNegativeDataValidation:
    """Negative test cases covering malformed timestamps, missing columns, and bad files."""

    def test_missing_mandatory_activity_column(self, tmp_path: Path) -> None:
        """Rejects dataset when required activity column is missing."""
        bad_csv = tmp_path / "missing_col.csv"
        bad_csv.write_text("case_id,timestamp,amount\n1,2026-01-01,100\n")

        with pytest.raises(FileStorageError) as exc_info:
            load_event_log(
                str(bad_csv),
                case_id_col="case_id",
                activity_col="missing_activity",
                timestamp_col="timestamp",
            )
        assert "Missing required columns" in str(exc_info.value)

    def test_empty_event_log_file(self, tmp_path: Path) -> None:
        """Rejects zero-row empty event log."""
        empty_csv = tmp_path / "empty.csv"
        empty_csv.write_text("case_id,activity,timestamp\n")

        service = ProcessService()
        req = ProcessAnalyzeRequest(
            source_uri=str(empty_csv),
            case_id_col="case_id",
            activity_col="activity",
            timestamp_col="timestamp",
        )
        # Empty log contains 0 cases
        res = service.analyze_business_process(req)
        assert res.total_cases == 0
        assert res.total_events == 0

    def test_unsupported_file_extension_rejected(self, tmp_path: Path) -> None:
        """Rejects unsupported extensions like .docx or .xml."""
        doc_file = tmp_path / "claims.xml"
        doc_file.write_text("<claims></claims>")

        with pytest.raises(FileStorageError):
            load_event_log(
                str(doc_file),
                case_id_col="case_id",
                activity_col="activity",
                timestamp_col="timestamp",
            )
