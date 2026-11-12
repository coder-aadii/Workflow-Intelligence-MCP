"""Workflow Exception Resolver service for triage, investigation, and escalation."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from workflow_intelligence.schemas.common import SeverityLevel
from workflow_intelligence.schemas.exception import (
    EscalationResult,
    ExceptionCategory,
    ExceptionInvestigationResult,
    MatchedEntity,
    ResolutionPlan,
    ResolutionRequest,
    WorkflowInvestigationRequest,
)
from workflow_intelligence.services.evidence_service import EvidenceService


class ExceptionServiceError(Exception):
    """Raised when exception resolver service operations fail."""


class ExceptionService:
    """Service for triaging, investigating, and resolving workflow exceptions.

    Provides deterministic failure classification, fuzzy entity reconciliation,
    policy-driven resolution recommendations, and escalation routing.
    """

    def __init__(self) -> None:
        self._evidence = EvidenceService()
        self._exception_counter = 0

    def classify_exception_type(
        self, failure_payload: dict[str, Any]
    ) -> ExceptionCategory:
        """Deterministically classify a failure payload into an exception category.

        Classification rules follow the Phase 3 triage classification matrix:

        - Vendor name typo with tax ID match → MASTER_DATA_DISCREPANCY
        - Missing manager approval / signature → GOVERNANCE_INCOMPLETE
        - Duplicate PO number / sequence conflict → CONCURRENCY_CONFLICT
        - ERP API timeout / network drop → TRANSIENT_NETWORK_DROP
        - Line item total exceeds budget → FINANCIAL_POLICY_BREACH

        Args:
            failure_payload: Free-form failure details dictionary.

        Returns:
            The classified ExceptionCategory.

        Raises:
            ExceptionServiceError: If the payload cannot be classified.
        """
        if not isinstance(failure_payload, dict):
            raise ExceptionServiceError(
                "failure_payload must be a dictionary for classification."
            )

        # Normalize all string values for case-insensitive matching
        text_parts: list[str] = []
        for key, value in failure_payload.items():
            text_parts.append(str(key).lower())
            if isinstance(value, str):
                text_parts.append(value.lower())
            elif isinstance(value, (int, float, bool)):
                text_parts.append(str(value).lower())
        combined_text = " ".join(text_parts)

        # Financial policy breach takes highest priority (always escalates)
        if any(
            token in combined_text
            for token in (
                "budget",
                "financial policy breach",
                "exceeds budget",
                "over budget",
                "policy breach",
            )
        ):
            return ExceptionCategory.FINANCIAL_POLICY_BREACH

        # Master data discrepancy: vendor name typo with tax ID match
        if any(
            token in combined_text
            for token in (
                "vendor",
                "tax_id",
                "tax id",
                "registration code",
                "master data",
            )
        ) and any(
            token in combined_text
            for token in (
                "typo",
                "mismatch",
                "discrepancy",
                "name mismatch",
                "tax_id_match",
                "tax id match",
            )
        ):
            return ExceptionCategory.MASTER_DATA_DISCREPANCY

        # Governance incomplete: missing approval / signature
        if any(
            token in combined_text
            for token in (
                "approval",
                "signature",
                "governance",
                "manager approval",
                "approver",
            )
        ):
            return ExceptionCategory.GOVERNANCE_INCOMPLETE

        # Concurrency conflict: duplicate PO / sequence
        if any(
            token in combined_text
            for token in (
                "duplicate",
                "concurrency",
                "sequence",
                "po number",
                "purchase order number",
                "regenerate",
            )
        ):
            return ExceptionCategory.CONCURRENCY_CONFLICT

        # Transient network drop: API timeout / network
        if any(
            token in combined_text
            for token in (
                "timeout",
                "network",
                "api",
                "erp",
                "transient",
                "backoff",
                "retry",
            )
        ):
            return ExceptionCategory.TRANSIENT_NETWORK_DROP

        raise ExceptionServiceError(
            "Unable to classify failure payload: no matching triage signature found."
        )

    def investigate_failure(
        self, request: WorkflowInvestigationRequest
    ) -> ExceptionInvestigationResult:
        """Investigate a failed workflow case.

        Classifies the failure, matches entities against master data, and
        builds a structured investigation result with audit narrative.

        Args:
            request: Investigation request with case_id, workflow_type,
                and failure_payload.

        Returns:
            ExceptionInvestigationResult with classification and matches.

        Raises:
            ExceptionServiceError: If investigation fails.
        """
        self._exception_counter += 1
        exception_id = f"EXC-{self._exception_counter:04d}"

        # Classify the exception
        category = self.classify_exception_type(request.failure_payload)

        # Match entities against any master records provided in payload
        master_records = request.failure_payload.get("master_records", [])
        if not isinstance(master_records, list):
            master_records = []

        raw_matches = self._evidence.match_entities(
            request.failure_payload, master_records
        )
        matched_entities = [
            MatchedEntity(
                entity_id=m["entity_id"],
                registered_name=m["registered_name"],
                matching_score=m["matching_score"],
                discrepancies=m["discrepancies"],
            )
            for m in raw_matches
        ]

        # Determine auto-resolvability and severity based on category
        auto_resolvable = False
        severity = SeverityLevel.MEDIUM
        recommended_remediation = ""
        classification_reason = ""

        if category == ExceptionCategory.MASTER_DATA_DISCREPANCY:
            best_score = (
                max((m.matching_score for m in matched_entities), default=0.0)
                if matched_entities
                else 0.0
            )
            auto_resolvable = best_score >= 95.0
            severity = SeverityLevel.LOW if auto_resolvable else SeverityLevel.MEDIUM
            classification_reason = (
                "Vendor name discrepancy detected with tax ID verification; "
                "classified as master data discrepancy."
            )
            recommended_remediation = (
                "Remap vendor ID and retry dispatch."
                if auto_resolvable
                else "Review vendor master data manually before dispatch."
            )

        elif category == ExceptionCategory.GOVERNANCE_INCOMPLETE:
            auto_resolvable = False
            severity = SeverityLevel.MEDIUM
            classification_reason = (
                "Missing manager approval or signature; classified as "
                "governance incomplete."
            )
            recommended_remediation = (
                "Trigger approval reminder webhook and await manager sign-off."
            )

        elif category == ExceptionCategory.CONCURRENCY_CONFLICT:
            auto_resolvable = True
            severity = SeverityLevel.LOW
            classification_reason = (
                "Duplicate purchase order number or sequence conflict detected; "
                "classified as concurrency conflict."
            )
            recommended_remediation = (
                "Query latest sequence and regenerate purchase order ID."
            )

        elif category == ExceptionCategory.TRANSIENT_NETWORK_DROP:
            auto_resolvable = True
            severity = SeverityLevel.LOW
            classification_reason = (
                "Downstream ERP API timeout or transient network drop detected; "
                "classified as transient network drop."
            )
            recommended_remediation = (
                "Apply exponential backoff retry (up to 3 attempts)."
            )

        elif category == ExceptionCategory.FINANCIAL_POLICY_BREACH:
            auto_resolvable = False
            severity = SeverityLevel.HIGH
            classification_reason = (
                "Line item total exceeds budget; classified as financial policy breach."
            )
            recommended_remediation = (
                "No automated fix available; escalate to Finance Manager."
            )

        audit_narrative = (
            f"Investigated case {request.case_id} "
            f"(workflow: {request.workflow_type}). "
            f"Classification: {category.value}. "
            f"{classification_reason} "
            f"Entities matched: {len(matched_entities)}. "
            f"Auto-resolvable: {auto_resolvable}. "
            f"Severity: {severity.value}."
        )

        return ExceptionInvestigationResult(
            case_id=request.case_id,
            exception_id=exception_id,
            category=category,
            matched_entities=matched_entities,
            classification_reason=classification_reason,
            severity=severity,
            auto_resolvable=auto_resolvable,
            recommended_remediation=recommended_remediation,
            audit_narrative=audit_narrative,
        )

    def recommend_resolution(self, request: ResolutionRequest) -> ResolutionPlan:
        """Recommend a resolution plan for a classified exception.

        Args:
            request: Resolution request with exception_id and optional
                policy_context.

        Returns:
            ResolutionPlan with remediation action and auto-resolve flag.

        Raises:
            ExceptionServiceError: If resolution cannot be determined.
        """
        # The resolution is derived from the exception context; in a full
        # implementation this would look up the exception record. Here we
        # use the policy_context to constrain the recommendation.
        policy_context = request.policy_context or {}

        auto_approve = policy_context.get("auto_approve", False)
        approval_tier = policy_context.get("approval_tier", "manager")

        remediation_action = (
            "Apply automated recovery per standard operating procedure."
            if auto_approve
            else "Route for manual review and approval."
        )

        return ResolutionPlan(
            exception_id=request.exception_id,
            remediation_action=remediation_action,
            auto_resolve=auto_approve,
            required_approval_tier=approval_tier if not auto_approve else None,
        )

    def escalate_case(
        self,
        case_id: str,
        reason: str,
        target_department: str,
    ) -> EscalationResult:
        """Escalate an unresolved exception case to a functional department.

        Generates a unique escalation tracking ticket with full audit trail.

        Args:
            case_id: Unique case identifier.
            reason: Reason for escalation.
            target_department: Department to route the escalation to
                (e.g. Finance, Procurement, IT_Ops, Management).

        Returns:
            EscalationResult with ticket ID, routed department, priority,
            and audit narrative.

        Raises:
            ExceptionServiceError: If escalation fails.
        """
        if not case_id or not reason or not target_department:
            raise ExceptionServiceError(
                "case_id, reason, and target_department are required for escalation."
            )

        self._exception_counter += 1
        ticket_id = f"TKT-{self._exception_counter:04d}"

        # Determine priority based on target department
        priority_map = {
            "Finance": SeverityLevel.HIGH,
            "Procurement": SeverityLevel.MEDIUM,
            "IT_Ops": SeverityLevel.MEDIUM,
            "Management": SeverityLevel.HIGH,
        }
        priority = priority_map.get(target_department, SeverityLevel.MEDIUM)

        timestamp = datetime.now(UTC).isoformat()
        audit_narrative = (
            f"Escalation ticket {ticket_id} created at {timestamp} "
            f"for case {case_id}. "
            f"Reason: {reason}. "
            f"Routed to: {target_department}. "
            f"Priority: {priority.value}."
        )

        return EscalationResult(
            ticket_id=ticket_id,
            routed_department=target_department,
            priority=priority,
            audit_narrative=audit_narrative,
        )
