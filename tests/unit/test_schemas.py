"""Unit tests for Pydantic V2 schemas."""

from __future__ import annotations

from datetime import datetime

import pytest
from pydantic import ValidationError

from workflow_intelligence.schemas import (
    AuditRecord,
    AutomationCandidate,
    AutomationScoreReport,
    AutomationScoreRequest,
    BaseMCPResponse,
    BottleneckReport,
    BottleneckRequest,
    DecisionBrief,
    DecisionBriefRequest,
    DimensionAttribution,
    EscalationRequest,
    EscalationResult,
    ExceptionCategory,
    ExecutionStatus,
    KPICalculationRequest,
    KPIExplanationRequest,
    MatchedEntity,
    MetricComparison,
    PaginationParams,
    PerformanceAnalysisResult,
    ProcessAnalysisResult,
    ProcessAnalyzeRequest,
    ResolutionPlan,
    ResolutionRequest,
    SeverityLevel,
    TransitionMetric,
    WorkflowInvestigationRequest,
)


class TestCommonSchemas:
    """Tests for common envelope, enums, pagination, and audit records."""

    def test_base_mcp_response_defaults(self) -> None:
        """BaseMCPResponse should generate defaults for timestamp and execution_id."""
        response = BaseMCPResponse(success=True)
        assert response.success is True
        assert isinstance(response.timestamp, datetime)
        assert isinstance(response.execution_id, str)
        assert response.warnings is None

    def test_base_mcp_response_with_warnings(self) -> None:
        """BaseMCPResponse should accept an optional warnings list."""
        response = BaseMCPResponse(success=False, warnings=["minor issue"])
        assert response.success is False
        assert response.warnings == ["minor issue"]

    def test_severity_level_enum(self) -> None:
        """SeverityLevel should expose all required levels."""
        assert SeverityLevel.LOW == "LOW"
        assert SeverityLevel.MEDIUM == "MEDIUM"
        assert SeverityLevel.HIGH == "HIGH"
        assert SeverityLevel.CRITICAL == "CRITICAL"

    def test_execution_status_enum(self) -> None:
        """ExecutionStatus should expose all required statuses."""
        assert ExecutionStatus.SUCCESS == "SUCCESS"
        assert ExecutionStatus.FAILURE == "FAILURE"
        assert ExecutionStatus.PARTIAL == "PARTIAL"
        assert ExecutionStatus.ESCALATED == "ESCALATED"

    def test_pagination_params_defaults(self) -> None:
        """PaginationParams should have sensible defaults and boundary validation."""
        params = PaginationParams()
        assert params.offset == 0
        assert params.limit == 100

    def test_pagination_params_boundaries(self) -> None:
        """PaginationParams should reject negative offset and out-of-range limit."""
        with pytest.raises(ValidationError):
            PaginationParams(offset=-1)
        with pytest.raises(ValidationError):
            PaginationParams(limit=0)
        with pytest.raises(ValidationError):
            PaginationParams(limit=1001)

    def test_audit_record(self) -> None:
        """AuditRecord should capture operation, tool, status, and optional details."""
        record = AuditRecord(
            operation="process_analyze",
            tool_name="process_*",
            case_id="PO-1001",
            status=ExecutionStatus.SUCCESS,
        )
        assert record.operation == "process_analyze"
        assert record.tool_name == "process_*"
        assert record.case_id == "PO-1001"
        assert record.status == ExecutionStatus.SUCCESS


class TestProcessSchemas:
    """Tests for process mining request and response models."""

    def test_process_analyze_request(self) -> None:
        """ProcessAnalyzeRequest should require all four column parameters."""
        req = ProcessAnalyzeRequest(
            source_uri="sample-data/procurement_events.csv",
            case_id_col="case_id",
            activity_col="activity",
            timestamp_col="timestamp",
        )
        assert req.source_uri == "sample-data/procurement_events.csv"
        assert req.case_id_col == "case_id"
        assert req.activity_col == "activity"
        assert req.timestamp_col == "timestamp"

    def test_process_analyze_request_rejects_empty_fields(self) -> None:
        """ProcessAnalyzeRequest should reject empty string fields."""
        with pytest.raises(ValidationError):
            ProcessAnalyzeRequest(
                source_uri="", case_id_col="cid", activity_col="act", timestamp_col="ts"
            )

    def test_bottleneck_request_defaults(self) -> None:
        """BottleneckRequest should default threshold_std_dev to 2.0."""
        req = BottleneckRequest(source_uri="events.csv")
        assert req.threshold_std_dev == 2.0

    def test_bottleneck_request_rejects_non_positive_threshold(self) -> None:
        """BottleneckRequest should reject threshold_std_dev <= 0."""
        with pytest.raises(ValidationError):
            BottleneckRequest(source_uri="events.csv", threshold_std_dev=0.0)
        with pytest.raises(ValidationError):
            BottleneckRequest(source_uri="events.csv", threshold_std_dev=-1.0)

    def test_automation_score_request_defaults(self) -> None:
        """AutomationScoreRequest should default min_frequency to 10."""
        req = AutomationScoreRequest(source_uri="events.csv")
        assert req.min_frequency == 10

    def test_automation_score_request_rejects_zero_frequency(self) -> None:
        """AutomationScoreRequest should reject min_frequency < 1."""
        with pytest.raises(ValidationError):
            AutomationScoreRequest(source_uri="events.csv", min_frequency=0)

    def test_transition_metric(self) -> None:
        """TransitionMetric should capture source, target, counts, and durations."""
        tm = TransitionMetric(
            source_activity="Purchase Request",
            target_activity="Manager Approval",
            transition_count=5,
            mean_duration_hours=24.0,
            median_duration_hours=20.0,
            std_dev_duration_hours=5.0,
        )
        assert tm.source_activity == "Purchase Request"
        assert tm.target_activity == "Manager Approval"
        assert tm.transition_count == 5
        assert tm.mean_duration_hours == 24.0
        assert tm.median_duration_hours == 20.0
        assert tm.std_dev_duration_hours == 5.0

    def test_process_analysis_result_inherits_base(self) -> None:
        """ProcessAnalysisResult should inherit BaseMCPResponse fields."""
        result = ProcessAnalysisResult(
            total_cases=100,
            total_events=500,
            unique_activities=["A", "B"],
            transitions=[
                TransitionMetric(
                    source_activity="A",
                    target_activity="B",
                    transition_count=10,
                )
            ],
        )
        assert result.total_cases == 100
        assert result.total_events == 500
        assert result.unique_activities == ["A", "B"]
        assert len(result.transitions) == 1
        assert result.success is True
        assert isinstance(result.execution_id, str)

    def test_bottleneck_report(self) -> None:
        """BottleneckReport should contain filtered stages and threshold."""
        report = BottleneckReport(
            stages=[
                TransitionMetric(
                    source_activity="Finance Approval",
                    target_activity="Vendor Selection",
                    transition_count=3,
                    mean_duration_hours=74.2,
                )
            ],
            threshold_std_dev=2.0,
        )
        assert len(report.stages) == 1
        assert report.threshold_std_dev == 2.0

    def test_automation_score_report(self) -> None:
        """AutomationScoreReport should rank candidates with automation scores."""
        report = AutomationScoreReport(
            candidates=[
                AutomationCandidate(
                    activity="Requisition Approval",
                    frequency=25,
                    avg_duration_hours=48.0,
                    variance_hours=10.0,
                    automation_score=85.5,
                )
            ],
            min_frequency=10,
        )
        assert len(report.candidates) == 1
        assert report.candidates[0].automation_score == 85.5
        assert report.min_frequency == 10

    def test_automation_candidate_rejects_score_out_of_range(self) -> None:
        """AutomationCandidate should reject automation_score outside 0-100."""
        with pytest.raises(ValidationError):
            AutomationCandidate(
                activity="X",
                frequency=1,
                avg_duration_hours=1.0,
                variance_hours=0.0,
                automation_score=101.0,
            )
        with pytest.raises(ValidationError):
            AutomationCandidate(
                activity="X",
                frequency=1,
                avg_duration_hours=1.0,
                variance_hours=0.0,
                automation_score=-1.0,
            )


class TestExceptionSchemas:
    """Tests for workflow exception and triage data models."""

    def test_exception_category_enum(self) -> None:
        """ExceptionCategory should expose all required categories."""
        assert ExceptionCategory.SCHEMA_DRIFT == "SCHEMA_DRIFT"
        assert ExceptionCategory.DATA_MISMATCH == "DATA_MISMATCH"
        assert ExceptionCategory.APPROVAL_TIMEOUT == "APPROVAL_TIMEOUT"
        assert ExceptionCategory.API_TIMEOUT == "API_TIMEOUT"
        assert ExceptionCategory.POLICY_VIOLATION == "POLICY_VIOLATION"

    def test_workflow_investigation_request(self) -> None:
        """WorkflowInvestigationRequest requires case_id, type, failure payload."""
        req = WorkflowInvestigationRequest(
            case_id="PO-10042",
            workflow_type="purchase_order_creation",
            failure_payload={"error_code": "VENDOR_RECORD_MISMATCH"},
        )
        assert req.case_id == "PO-10042"
        assert req.workflow_type == "purchase_order_creation"
        assert req.failure_payload["error_code"] == "VENDOR_RECORD_MISMATCH"

    def test_workflow_investigation_request_rejects_empty_case_id(self) -> None:
        """WorkflowInvestigationRequest should reject empty case_id."""
        with pytest.raises(ValidationError):
            WorkflowInvestigationRequest(
                case_id="",
                workflow_type="po",
                failure_payload={},
            )

    def test_resolution_request(self) -> None:
        """ResolutionRequest requires exception_id with optional policy context."""
        req = ResolutionRequest(
            exception_id="EXC-001",
            policy_context={"tier": "manager"},
        )
        assert req.exception_id == "EXC-001"
        assert req.policy_context == {"tier": "manager"}

    def test_resolution_request_defaults(self) -> None:
        """ResolutionRequest should default policy_context to None."""
        req = ResolutionRequest(exception_id="EXC-002")
        assert req.policy_context is None

    def test_escalation_request(self) -> None:
        """EscalationRequest should require case_id, reason, and target_department."""
        req = EscalationRequest(
            case_id="PO-10042",
            reason="Unauthorized vendor change",
            target_department="Procurement",
        )
        assert req.case_id == "PO-10042"
        assert req.reason == "Unauthorized vendor change"
        assert req.target_department == "Procurement"

    def test_matched_entity(self) -> None:
        """MatchedEntity should capture entity id, name, score, and discrepancies."""
        entity = MatchedEntity(
            entity_id="VEND-00412",
            registered_name="ABC Logistics Global Inc",
            matching_score=98.0,
            discrepancies=["tax_id_suffix"],
        )
        assert entity.entity_id == "VEND-00412"
        assert entity.registered_name == "ABC Logistics Global Inc"
        assert entity.matching_score == 98.0
        assert entity.discrepancies == ["tax_id_suffix"]

    def test_matched_entity_rejects_score_out_of_range(self) -> None:
        """MatchedEntity should reject matching_score outside 0-100."""
        with pytest.raises(ValidationError):
            MatchedEntity(
                entity_id="E1",
                registered_name="Name",
                matching_score=150.0,
            )
        with pytest.raises(ValidationError):
            MatchedEntity(
                entity_id="E1",
                registered_name="Name",
                matching_score=-10.0,
            )

    def test_resolution_plan(self) -> None:
        """ResolutionPlan should capture remediation action and auto-resolve flag."""
        plan = ResolutionPlan(
            exception_id="EXC-001",
            remediation_action="Remap vendor_id and resume dispatch_po",
            auto_resolve=True,
            required_approval_tier=None,
        )
        assert plan.exception_id == "EXC-001"
        assert plan.auto_resolve is True
        assert plan.required_approval_tier is None

    def test_escalation_result_inherits_base(self) -> None:
        """EscalationResult should inherit BaseMCPResponse fields."""
        result = EscalationResult(
            ticket_id="TKT-999",
            routed_department="Finance",
            priority=SeverityLevel.HIGH,
            audit_narrative="Escalated due to policy violation.",
        )
        assert result.ticket_id == "TKT-999"
        assert result.routed_department == "Finance"
        assert result.priority == SeverityLevel.HIGH
        assert result.success is True


class TestDecisionSchemas:
    """Tests for KPI variance and decision brief data models."""

    def test_kpi_calculation_request(self) -> None:
        """KPICalculationRequest should require metric_name and both timeframes."""
        req = KPICalculationRequest(
            metric_name="procurement_cost",
            timeframe_current="Q3-2026",
            timeframe_baseline="Q2-2026",
        )
        assert req.metric_name == "procurement_cost"
        assert req.timeframe_current == "Q3-2026"
        assert req.timeframe_baseline == "Q2-2026"

    def test_kpi_explanation_request(self) -> None:
        """KPIExplanationRequest should require metric_name and observed_delta."""
        req = KPIExplanationRequest(
            metric_name="procurement_cost",
            observed_delta=28.4,
            dimension="department",
        )
        assert req.metric_name == "procurement_cost"
        assert req.observed_delta == 28.4
        assert req.dimension == "department"

    def test_kpi_explanation_request_defaults(self) -> None:
        """KPIExplanationRequest should default dimension to None."""
        req = KPIExplanationRequest(metric_name="cost", observed_delta=5.0)
        assert req.dimension is None

    def test_decision_brief_request(self) -> None:
        """DecisionBriefRequest should require topic with optional metric_ids."""
        req = DecisionBriefRequest(topic="Q3 Cost Inflation", metric_ids=["m1", "m2"])
        assert req.topic == "Q3 Cost Inflation"
        assert req.metric_ids == ["m1", "m2"]

    def test_metric_comparison(self) -> None:
        """MetricComparison should capture baseline, current, and variance."""
        mc = MetricComparison(
            metric_name="procurement_cost",
            baseline_value=52000.0,
            current_value=68000.0,
            absolute_variance=16000.0,
            relative_variance_percentage=30.77,
        )
        assert mc.baseline_value == 52000.0
        assert mc.current_value == 68000.0
        assert mc.absolute_variance == 16000.0
        assert mc.relative_variance_percentage == 30.77

    def test_dimension_attribution(self) -> None:
        """DimensionAttribution captures segment, contribution, record count."""
        da = DimensionAttribution(
            segment_name="Engineering Hardware",
            contribution_percentage=41.0,
            record_count=142,
        )
        assert da.segment_name == "Engineering Hardware"
        assert da.contribution_percentage == 41.0
        assert da.record_count == 142

    def test_dimension_attribution_rejects_percentage_out_of_range(self) -> None:
        """DimensionAttribution should reject contribution_percentage outside 0-100."""
        with pytest.raises(ValidationError):
            DimensionAttribution(
                segment_name="X",
                contribution_percentage=101.0,
                record_count=1,
            )
        with pytest.raises(ValidationError):
            DimensionAttribution(
                segment_name="X",
                contribution_percentage=-1.0,
                record_count=1,
            )

    def test_decision_brief_inherits_base(self) -> None:
        """DecisionBrief inherits base fields and contains all sections."""
        brief = DecisionBrief(
            topic="Q3 Procurement Cost Inflation",
            executive_summary="Costs increased +28.4%.",
            findings=[
                MetricComparison(
                    metric_name="procurement_cost",
                    baseline_value=52000.0,
                    current_value=68000.0,
                    absolute_variance=16000.0,
                    relative_variance_percentage=30.77,
                )
            ],
            attributions=[
                DimensionAttribution(
                    segment_name="Engineering Hardware",
                    contribution_percentage=41.0,
                    record_count=142,
                )
            ],
            provenance_links=["sample-data/procurement_costs.csv"],
            recommendations=["Implement vendor catalog constraints."],
        )
        assert brief.topic == "Q3 Procurement Cost Inflation"
        assert len(brief.findings) == 1
        assert len(brief.attributions) == 1
        assert len(brief.provenance_links) == 1
        assert len(brief.recommendations) == 1
        assert brief.success is True

    def test_performance_analysis_result(self) -> None:
        """PerformanceAnalysisResult captures metric values and delta percentage."""
        result = PerformanceAnalysisResult(
            metric_name="procurement_cost",
            baseline_value=52000.0,
            current_value=68000.0,
            delta_percentage=30.77,
        )
        assert result.metric_name == "procurement_cost"
        assert result.delta_percentage == 30.77


class TestSchemaSerialization:
    """Tests for JSON schema generation and round-trip serialization."""

    def test_base_mcp_response_json_schema(self) -> None:
        """BaseMCPResponse should generate a valid JSON schema."""
        schema = BaseMCPResponse.model_json_schema()
        assert "properties" in schema
        assert "success" in schema["properties"]
        assert "timestamp" in schema["properties"]
        assert "execution_id" in schema["properties"]

    def test_process_analysis_result_json_schema(self) -> None:
        """ProcessAnalysisResult should generate a valid JSON schema."""
        schema = ProcessAnalysisResult.model_json_schema()
        assert "properties" in schema
        assert "total_cases" in schema["properties"]
        assert "transitions" in schema["properties"]

    def test_decision_brief_json_schema(self) -> None:
        """DecisionBrief should generate a valid JSON schema."""
        schema = DecisionBrief.model_json_schema()
        assert "properties" in schema
        assert "topic" in schema["properties"]
        assert "provenance_links" in schema["properties"]

    def test_round_trip_serialization(self) -> None:
        """Models should serialize to dict and back without data loss."""
        req = ProcessAnalyzeRequest(
            source_uri="events.csv",
            case_id_col="case_id",
            activity_col="activity",
            timestamp_col="timestamp",
        )
        data = req.model_dump()
        restored = ProcessAnalyzeRequest(**data)
        assert restored == req

    def test_invalid_input_rejection(self) -> None:
        """All schemas should reject invalid input types."""
        with pytest.raises(ValidationError):
            ProcessAnalyzeRequest(
                source_uri=123,  # type: ignore[arg-type]
                case_id_col="cid",
                activity_col="act",
                timestamp_col="ts",
            )
