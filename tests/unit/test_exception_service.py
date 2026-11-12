"""Unit tests for ExceptionService with synthetic failure payloads."""

from __future__ import annotations

import pytest

from workflow_intelligence.schemas.common import SeverityLevel
from workflow_intelligence.schemas.exception import (
    EscalationRequest,
    ExceptionCategory,
    ExceptionInvestigationResult,
    ResolutionPlan,
    ResolutionRequest,
    WorkflowInvestigationRequest,
)
from workflow_intelligence.services.exception_service import (
    ExceptionService,
    ExceptionServiceError,
)


@pytest.fixture
def service() -> ExceptionService:
    """Provide an ExceptionService instance."""
    return ExceptionService()


# ---------------------------------------------------------------------------
# Triage classification tests (15+ synthetic failure payloads)
# ---------------------------------------------------------------------------


class TestExceptionClassification:
    """Tests for classify_exception_type across all taxonomy branches."""

    def test_master_data_discrepancy_vendor_typo_tax_id_match(
        self, service: ExceptionService
    ) -> None:
        """Vendor name typo with tax ID match -> MASTER_DATA_DISCREPANCY."""
        payload = {
            "error": "vendor_name_typo",
            "vendor_name": "ABC Logistics Globla Inc",
            "tax_id": "US-123456789",
            "tax_id_match": True,
        }
        category = service.classify_exception_type(payload)
        assert category == ExceptionCategory.MASTER_DATA_DISCREPANCY

    def test_master_data_discrepancy_master_data(
        self, service: ExceptionService
    ) -> None:
        """Master data discrepancy keyword -> MASTER_DATA_DISCREPANCY."""
        payload = {
            "error": "master_data_discrepancy",
            "detail": "Vendor master record does not match submitted data.",
        }
        category = service.classify_exception_type(payload)
        assert category == ExceptionCategory.MASTER_DATA_DISCREPANCY

    def test_governance_incomplete_missing_approval(
        self, service: ExceptionService
    ) -> None:
        """Missing manager approval -> GOVERNANCE_INCOMPLETE."""
        payload = {
            "error": "missing_approval_signature",
            "detail": "Purchase order missing manager approval signature.",
            "approver": "jane.doe",
        }
        category = service.classify_exception_type(payload)
        assert category == ExceptionCategory.GOVERNANCE_INCOMPLETE

    def test_governance_incomplete_signature(self, service: ExceptionService) -> None:
        """Missing signature keyword -> GOVERNANCE_INCOMPLETE."""
        payload = {
            "error": "governance_incomplete",
            "detail": "Required approver signature not present on invoice.",
        }
        category = service.classify_exception_type(payload)
        assert category == ExceptionCategory.GOVERNANCE_INCOMPLETE

    def test_concurrency_conflict_duplicate_po(self, service: ExceptionService) -> None:
        """Duplicate PO number -> CONCURRENCY_CONFLICT."""
        payload = {
            "error": "duplicate_po_number",
            "detail": "Purchase order PO-9999 already exists in sequence.",
            "po_number": "PO-9999",
        }
        category = service.classify_exception_type(payload)
        assert category == ExceptionCategory.CONCURRENCY_CONFLICT

    def test_concurrency_conflict_sequence(self, service: ExceptionService) -> None:
        """Sequence conflict keyword -> CONCURRENCY_CONFLICT."""
        payload = {
            "error": "concurrency_conflict",
            "detail": "Concurrent sequence regeneration detected.",
        }
        category = service.classify_exception_type(payload)
        assert category == ExceptionCategory.CONCURRENCY_CONFLICT

    def test_transient_network_drop_api_timeout(
        self, service: ExceptionService
    ) -> None:
        """ERP API timeout -> TRANSIENT_NETWORK_DROP."""
        payload = {
            "error": "api_timeout",
            "detail": "Downstream ERP API call timed out after 30s.",
            "endpoint": "/erp/invoice",
        }
        category = service.classify_exception_type(payload)
        assert category == ExceptionCategory.TRANSIENT_NETWORK_DROP

    def test_transient_network_drop_network(self, service: ExceptionService) -> None:
        """Network drop keyword -> TRANSIENT_NETWORK_DROP."""
        payload = {
            "error": "network_drop",
            "detail": "Transient network failure connecting to ERP.",
        }
        category = service.classify_exception_type(payload)
        assert category == ExceptionCategory.TRANSIENT_NETWORK_DROP

    def test_financial_policy_breach_exceeds_budget(
        self, service: ExceptionService
    ) -> None:
        """Line item exceeds budget -> FINANCIAL_POLICY_BREACH."""
        payload = {
            "error": "budget_breach",
            "detail": "Line item total 15000 exceeds budget 10000.",
            "line_total": 15000,
            "budget": 10000,
        }
        category = service.classify_exception_type(payload)
        assert category == ExceptionCategory.FINANCIAL_POLICY_BREACH

    def test_financial_policy_breach_policy(self, service: ExceptionService) -> None:
        """Financial policy breach keyword -> FINANCIAL_POLICY_BREACH."""
        payload = {
            "error": "policy_breach",
            "detail": "Financial policy breach: amount over authorized limit.",
        }
        category = service.classify_exception_type(payload)
        assert category == ExceptionCategory.FINANCIAL_POLICY_BREACH

    def test_classify_unknown_payload_raises(self, service: ExceptionService) -> None:
        """Unclassifiable payload should raise ExceptionServiceError."""
        payload = {"error": "unknown_error", "detail": "Something went wrong."}
        with pytest.raises(ExceptionServiceError):
            service.classify_exception_type(payload)

    def test_classify_empty_payload_raises(self, service: ExceptionService) -> None:
        """Empty payload should raise ExceptionServiceError."""
        with pytest.raises(ExceptionServiceError):
            service.classify_exception_type({})

    def test_classify_non_dict_raises(self, service: ExceptionService) -> None:
        """Non-dict payload should raise ExceptionServiceError."""
        with pytest.raises(ExceptionServiceError):
            service.classify_exception_type("not a dict")  # type: ignore[arg-type]


class TestExceptionInvestigation:
    """Tests for investigate_failure end-to-end."""

    def test_investigate_master_data_discrepancy_auto_resolve(
        self, service: ExceptionService
    ) -> None:
        """Master data discrepancy with high confidence should be auto-resolvable."""
        request = WorkflowInvestigationRequest(
            case_id="PO-10042",
            workflow_type="purchase_order_creation",
            failure_payload={
                "error": "vendor_name_typo",
                "vendor_name": "ABC Logistics Globla Inc",
                "tax_id": "US-123456789",
                "tax_id_match": True,
                "master_records": [
                    {
                        "entity_id": "VEND-00412",
                        "registered_name": "ABC Logistics Global Inc",
                        "tax_id": "US-123456789",
                    }
                ],
            },
        )
        result = service.investigate_failure(request)

        assert isinstance(result, ExceptionInvestigationResult)
        assert result.case_id == "PO-10042"
        assert result.category == ExceptionCategory.MASTER_DATA_DISCREPANCY
        assert result.auto_resolvable is True
        assert result.severity == SeverityLevel.LOW
        assert len(result.matched_entities) == 1
        assert result.matched_entities[0].matching_score >= 95.0
        assert result.success is True
        assert result.exception_id.startswith("EXC-")

    def test_investigate_financial_policy_breach_not_auto_resolvable(
        self, service: ExceptionService
    ) -> None:
        """Financial policy breach should never be auto-resolvable."""
        request = WorkflowInvestigationRequest(
            case_id="PO-10043",
            workflow_type="invoice_processing",
            failure_payload={
                "error": "budget_breach",
                "detail": "Line item total exceeds budget.",
                "line_total": 15000,
                "budget": 10000,
            },
        )
        result = service.investigate_failure(request)

        assert result.category == ExceptionCategory.FINANCIAL_POLICY_BREACH
        assert result.auto_resolvable is False
        assert result.severity == SeverityLevel.HIGH
        assert "Finance Manager" in result.recommended_remediation

    def test_investigate_governance_incomplete(self, service: ExceptionService) -> None:
        """Governance incomplete should not be auto-resolvable."""
        request = WorkflowInvestigationRequest(
            case_id="PO-10044",
            workflow_type="purchase_order_approval",
            failure_payload={
                "error": "missing_approval_signature",
                "detail": "Manager approval signature missing.",
            },
        )
        result = service.investigate_failure(request)

        assert result.category == ExceptionCategory.GOVERNANCE_INCOMPLETE
        assert result.auto_resolvable is False
        assert result.severity == SeverityLevel.MEDIUM
        assert "approval reminder" in result.recommended_remediation.lower()

    def test_investigate_concurrency_conflict_auto_resolve(
        self, service: ExceptionService
    ) -> None:
        """Concurrency conflict should be auto-resolvable."""
        request = WorkflowInvestigationRequest(
            case_id="PO-10045",
            workflow_type="purchase_order_creation",
            failure_payload={
                "error": "duplicate_po_number",
                "detail": "PO-9999 already exists.",
            },
        )
        result = service.investigate_failure(request)

        assert result.category == ExceptionCategory.CONCURRENCY_CONFLICT
        assert result.auto_resolvable is True
        assert result.severity == SeverityLevel.LOW
        assert "regenerate" in result.recommended_remediation.lower()

    def test_investigate_transient_network_drop_auto_resolve(
        self, service: ExceptionService
    ) -> None:
        """Transient network drop should be auto-resolvable."""
        request = WorkflowInvestigationRequest(
            case_id="PO-10046",
            workflow_type="erp_invoice_sync",
            failure_payload={
                "error": "api_timeout",
                "detail": "ERP API timeout.",
            },
        )
        result = service.investigate_failure(request)

        assert result.category == ExceptionCategory.TRANSIENT_NETWORK_DROP
        assert result.auto_resolvable is True
        assert result.severity == SeverityLevel.LOW
        assert "backoff" in result.recommended_remediation.lower()

    def test_investigate_audit_narrative(self, service: ExceptionService) -> None:
        """Investigation result should include a structured audit narrative."""
        request = WorkflowInvestigationRequest(
            case_id="PO-10047",
            workflow_type="purchase_order_creation",
            failure_payload={
                "error": "vendor_name_typo",
                "vendor_name": "X",
            },
        )
        result = service.investigate_failure(request)

        assert result.case_id in result.audit_narrative
        assert result.category.value in result.audit_narrative
        assert result.severity.value in result.audit_narrative


class TestExceptionResolution:
    """Tests for recommend_resolution."""

    def test_recommend_resolution_auto_approve(self, service: ExceptionService) -> None:
        """Auto-approve policy context should enable auto-resolve."""
        request = ResolutionRequest(
            exception_id="EXC-0001",
            policy_context={"auto_approve": True, "approval_tier": "manager"},
        )
        plan = service.recommend_resolution(request)

        assert isinstance(plan, ResolutionPlan)
        assert plan.exception_id == "EXC-0001"
        assert plan.auto_resolve is True
        assert plan.required_approval_tier is None

    def test_recommend_resolution_manual_review(
        self, service: ExceptionService
    ) -> None:
        """Non-auto-approve should require manual approval tier."""
        request = ResolutionRequest(
            exception_id="EXC-0002",
            policy_context={"auto_approve": False, "approval_tier": "director"},
        )
        plan = service.recommend_resolution(request)

        assert plan.auto_resolve is False
        assert plan.required_approval_tier == "director"

    def test_recommend_resolution_default_policy(
        self, service: ExceptionService
    ) -> None:
        """Default policy context should not auto-resolve."""
        request = ResolutionRequest(exception_id="EXC-0003")
        plan = service.recommend_resolution(request)

        assert plan.auto_resolve is False
        assert plan.required_approval_tier == "manager"


class TestExceptionEscalation:
    """Tests for escalate_case."""

    def test_escalate_to_finance_high_priority(self, service: ExceptionService) -> None:
        """Escalation to Finance should be HIGH priority."""
        result = service.escalate_case(
            case_id="PO-10048",
            reason="Line item total exceeds budget.",
            target_department="Finance",
        )

        assert result.ticket_id.startswith("TKT-")
        assert result.routed_department == "Finance"
        assert result.priority == SeverityLevel.HIGH
        assert result.success is True
        assert "Finance" in result.audit_narrative
        assert "PO-10048" in result.audit_narrative

    def test_escalate_to_management_high_priority(
        self, service: ExceptionService
    ) -> None:
        """Escalation to Management should be HIGH priority."""
        result = service.escalate_case(
            case_id="PO-10049",
            reason="Repeated governance violations.",
            target_department="Management",
        )

        assert result.priority == SeverityLevel.HIGH
        assert result.routed_department == "Management"

    def test_escalate_to_procurement_medium_priority(
        self, service: ExceptionService
    ) -> None:
        """Escalation to Procurement should be MEDIUM priority."""
        result = service.escalate_case(
            case_id="PO-10050",
            reason="Vendor master data review needed.",
            target_department="Procurement",
        )

        assert result.priority == SeverityLevel.MEDIUM
        assert result.routed_department == "Procurement"

    def test_escalate_to_it_ops_medium_priority(
        self, service: ExceptionService
    ) -> None:
        """Escalation to IT_Ops should be MEDIUM priority."""
        result = service.escalate_case(
            case_id="PO-10051",
            reason="ERP API connectivity issues.",
            target_department="IT_Ops",
        )

        assert result.priority == SeverityLevel.MEDIUM
        assert result.routed_department == "IT_Ops"

    def test_escalate_missing_fields_raises(self, service: ExceptionService) -> None:
        """Missing case_id, reason, or department should raise."""
        with pytest.raises(ExceptionServiceError):
            service.escalate_case(
                case_id="", reason="test", target_department="Finance"
            )
        with pytest.raises(ExceptionServiceError):
            service.escalate_case(
                case_id="PO-1", reason="", target_department="Finance"
            )
        with pytest.raises(ExceptionServiceError):
            service.escalate_case(case_id="PO-1", reason="test", target_department="")


class TestExceptionRequestValidation:
    """Tests for Pydantic request/response validation."""

    def test_workflow_investigation_request(self) -> None:
        """WorkflowInvestigationRequest should require all fields."""
        req = WorkflowInvestigationRequest(
            case_id="PO-1",
            workflow_type="po_creation",
            failure_payload={"error": "test"},
        )
        assert req.case_id == "PO-1"
        assert req.workflow_type == "po_creation"
        assert req.failure_payload == {"error": "test"}

    def test_escalation_request(self) -> None:
        """EscalationRequest should require all fields."""
        req = EscalationRequest(
            case_id="PO-1",
            reason="Budget breach",
            target_department="Finance",
        )
        assert req.case_id == "PO-1"
        assert req.reason == "Budget breach"
        assert req.target_department == "Finance"
