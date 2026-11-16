"""Workflow Exception Resolver service for triage, investigation, and escalation."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from workflow_intelligence.config import get_settings
from workflow_intelligence.observability.logging import get_logger
from workflow_intelligence.persistence.database import get_db_session
from workflow_intelligence.persistence.repositories import (
    AuditRepository,
    ExceptionRepository,
    WorkflowRepository,
)
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

logger = get_logger("workflow_intelligence.services.exception_service")

# Sensitive fields to redact from logs and database payload snapshots
SENSITIVE_FIELD_NAMES = {
    "password",
    "secret",
    "token",
    "api_key",
    "apikey",
    "authorization",
    "access_token",
    "credit_card",
    "ssn",
    "cvv",
}


def sanitize_payload(data: Any) -> Any:
    """Mask sensitive fields recursively in payloads and audit details."""
    if isinstance(data, dict):
        sanitized: dict[str, Any] = {}
        for k, v in data.items():
            if any(s in str(k).lower() for s in SENSITIVE_FIELD_NAMES):
                sanitized[k] = "********"
            else:
                sanitized[k] = sanitize_payload(v)
        return sanitized
    if isinstance(data, list):
        return [sanitize_payload(item) for item in data]
    return data


class ExceptionServiceError(Exception):
    """Raised when exception resolver service operations fail."""


class ExceptionService:
    """Service for triaging, investigating, and resolving workflow exceptions.

    Provides deterministic failure classification, fuzzy entity reconciliation,
    policy-driven resolution recommendations, escalation routing, and
    end-to-end transactional persistence with audit logging.
    """

    def __init__(self) -> None:
        self._evidence = EvidenceService()
        self._exception_counter = 0
        # In-memory store for standalone demo mode and offline testing
        self._in_memory_exceptions: dict[str, dict[str, Any]] = {}
        self._in_memory_workflows: dict[str, dict[str, Any]] = {}
        self._in_memory_audit: list[dict[str, Any]] = []
        self._in_memory_escalations: dict[str, dict[str, Any]] = {}

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

    def _evaluate_investigation(
        self, request: WorkflowInvestigationRequest
    ) -> tuple[
        str,
        ExceptionCategory,
        list[MatchedEntity],
        bool,
        SeverityLevel,
        str,
        str,
        str,
    ]:
        """Perform core deterministic triage and entity reconciliation."""
        category = self.classify_exception_type(request.failure_payload)

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

        self._exception_counter += 1
        exception_id = f"EXC-{self._exception_counter:04d}"

        audit_narrative = (
            f"Investigated case {request.case_id} "
            f"(workflow: {request.workflow_type}). "
            f"Classification: {category.value}. "
            f"{classification_reason} "
            f"Entities matched: {len(matched_entities)}. "
            f"Auto-resolvable: {auto_resolvable}. "
            f"Severity: {severity.value}."
        )

        return (
            exception_id,
            category,
            matched_entities,
            auto_resolvable,
            severity,
            recommended_remediation,
            classification_reason,
            audit_narrative,
        )

    def investigate_failure(
        self, request: WorkflowInvestigationRequest
    ) -> ExceptionInvestigationResult:
        """Synchronous investigation handler (demo and offline unit tests)."""
        (
            exception_id,
            category,
            matched_entities,
            auto_resolvable,
            severity,
            recommended_remediation,
            classification_reason,
            audit_narrative,
        ) = self._evaluate_investigation(request)

        # Record in in-memory state
        sanitized_payload = sanitize_payload(request.failure_payload)
        self._in_memory_exceptions[exception_id] = {
            "id": exception_id,
            "case_id": request.case_id,
            "workflow_type": request.workflow_type,
            "category": category.value,
            "root_cause": classification_reason,
            "is_resolved": False,
            "auto_resolvable": auto_resolvable,
            "severity": severity.value,
            "payload": sanitized_payload,
            "created_at": datetime.now(UTC).isoformat(),
        }
        self._in_memory_workflows[request.case_id] = {
            "id": request.case_id,
            "workflow_type": request.workflow_type,
            "status": "failed",
        }
        self._in_memory_audit.append(
            {
                "action_name": "exception_investigate_workflow",
                "actor": "system",
                "timestamp": datetime.now(UTC).isoformat(),
                "details": {
                    "case_id": request.case_id,
                    "exception_id": exception_id,
                    "category": category.value,
                    "severity": severity.value,
                    "auto_resolvable": auto_resolvable,
                },
                "correlation_id": request.case_id,
            }
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

    async def ainvestigate_failure(
        self, request: WorkflowInvestigationRequest
    ) -> ExceptionInvestigationResult:
        """Asynchronous investigation handler with transactional database persistence."""
        result = self.investigate_failure(request)
        sanitized_payload = sanitize_payload(request.failure_payload)

        try:
            async for session in get_db_session():
                async with session.begin():
                    wf_repo = WorkflowRepository(session)
                    exc_repo = ExceptionRepository(session)
                    audit_repo = AuditRepository(session)

                    # Look up or create parent workflow instance
                    wf = await wf_repo.get(request.case_id)
                    if wf is None:
                        wf = await wf_repo.create_workflow_instance(
                            workflow_type=request.workflow_type,
                            status="failed",
                            metadata={"case_id": request.case_id},
                            item_id=request.case_id
                            if len(request.case_id) <= 36
                            else None,
                        )
                    else:
                        await wf_repo.update_status(wf.id, "failed")

                    # Check for duplicate open exception on this workflow
                    existing_exceptions = await exc_repo.get_by_workflow_id(wf.id)
                    existing_open = [
                        e
                        for e in existing_exceptions
                        if not e.is_resolved and e.category == result.category.value
                    ]

                    if existing_open:
                        # Idempotency: reuse existing open exception
                        result.exception_id = existing_open[0].id
                    else:
                        # Ensure stable unique primary key
                        candidate_id = result.exception_id
                        while await exc_repo.get(candidate_id) is not None:
                            self._exception_counter += 1
                            candidate_id = f"EXC-{self._exception_counter:04d}"
                        result.exception_id = candidate_id

                        await exc_repo.create_exception(
                            item_id=result.exception_id,
                            workflow_id=wf.id,
                            category=result.category.value,
                            root_cause=result.classification_reason,
                            payload=sanitized_payload,
                        )

                    # Record immutable audit entry
                    await audit_repo.create_audit_entry(
                        action_name="exception_investigate_workflow",
                        actor="system",
                        details={
                            "case_id": request.case_id,
                            "exception_id": result.exception_id,
                            "category": result.category.value,
                            "severity": result.severity.value,
                            "auto_resolvable": result.auto_resolvable,
                        },
                        correlation_id=request.case_id,
                    )
        except Exception as exc:
            if isinstance(exc, ExceptionServiceError):
                raise
            if get_settings().workflow_env == "production":
                logger.error(
                    "Production database failure during exception persistence: %s", exc
                )
                raise ExceptionServiceError(
                    f"Production database persistence failure: {exc}"
                ) from exc
            logger.debug(
                "Database persistence unavailable; continuing in-memory: %s", exc
            )

        return result

    def recommend_resolution(self, request: ResolutionRequest) -> ResolutionPlan:
        """Synchronous resolution recommendation handler."""
        policy_context = request.policy_context or {}
        auto_approve = bool(policy_context.get("auto_approve", False))
        approval_tier = policy_context.get("approval_tier", "manager")

        # Check if already resolved in memory
        in_mem = self._in_memory_exceptions.get(request.exception_id)
        if in_mem and in_mem.get("is_resolved"):
            return ResolutionPlan(
                exception_id=request.exception_id,
                remediation_action="Exception has already been resolved.",
                auto_resolve=False,
                required_approval_tier=None,
            )

        remediation_action = (
            "Apply automated recovery per standard operating procedure."
            if auto_approve
            else "Route for manual review and approval."
        )

        if in_mem and auto_approve:
            in_mem["is_resolved"] = True
            in_mem["resolved_at"] = datetime.now(UTC).isoformat()
            self._in_memory_audit.append(
                {
                    "action_name": "exception_resolve",
                    "actor": "system",
                    "timestamp": datetime.now(UTC).isoformat(),
                    "details": {
                        "exception_id": request.exception_id,
                        "action": remediation_action,
                        "auto_resolved": True,
                    },
                    "correlation_id": in_mem.get("case_id", request.exception_id),
                }
            )

        return ResolutionPlan(
            exception_id=request.exception_id,
            remediation_action=remediation_action,
            auto_resolve=auto_approve,
            required_approval_tier=approval_tier if not auto_approve else None,
        )

    async def arecommend_resolution(self, request: ResolutionRequest) -> ResolutionPlan:
        """Asynchronous resolution recommendation handler with persistence."""
        policy_context = request.policy_context or {}
        auto_approve = bool(policy_context.get("auto_approve", False))
        approval_tier = policy_context.get("approval_tier", "manager")

        remediation_action = (
            "Apply automated recovery per standard operating procedure."
            if auto_approve
            else "Route for manual review and approval."
        )

        try:
            async for session in get_db_session():
                async with session.begin():
                    exc_repo = ExceptionRepository(session)
                    wf_repo = WorkflowRepository(session)
                    audit_repo = AuditRepository(session)

                    record = await exc_repo.get(request.exception_id)
                    if record is not None:
                        # Prevent duplicate resolution
                        if record.is_resolved:
                            return ResolutionPlan(
                                exception_id=request.exception_id,
                                remediation_action="Exception has already been resolved.",
                                auto_resolve=False,
                                required_approval_tier=None,
                            )

                        if auto_approve:
                            await exc_repo.mark_resolved(record.id)
                            # Check whether all exceptions for parent workflow are now resolved
                            wf_exceptions = await exc_repo.get_by_workflow_id(
                                record.workflow_id
                            )
                            if all(
                                e.is_resolved or e.id == record.id
                                for e in wf_exceptions
                            ):
                                await wf_repo.complete_workflow(record.workflow_id)

                            await audit_repo.create_audit_entry(
                                action_name="exception_resolve",
                                actor="system",
                                details={
                                    "exception_id": request.exception_id,
                                    "workflow_id": record.workflow_id,
                                    "approval_tier": approval_tier,
                                    "remediation_action": remediation_action,
                                    "auto_resolved": True,
                                },
                                correlation_id=record.workflow_id,
                            )
        except Exception as exc:
            if isinstance(exc, ExceptionServiceError):
                raise
            if get_settings().workflow_env == "production":
                logger.error(
                    "Production database failure during recommendation: %s", exc
                )
                raise ExceptionServiceError(
                    f"Production database lookup failure: {exc}"
                ) from exc
            logger.debug(
                "Database lookup unavailable; falling back to in-memory recommendation: %s",
                exc,
            )

        return self.recommend_resolution(request)

    def escalate_case(
        self,
        case_id: str,
        reason: str,
        target_department: str,
    ) -> EscalationResult:
        """Synchronous case escalation handler."""
        if not case_id or not reason or not target_department:
            raise ExceptionServiceError(
                "case_id, reason, and target_department are required for escalation."
            )

        # Prevent duplicate escalations in memory
        existing = self._in_memory_escalations.get(case_id)
        if existing and existing.get("department") == target_department:
            return EscalationResult(
                ticket_id=existing["ticket_id"],
                routed_department=target_department,
                priority=SeverityLevel(existing["priority"]),
                audit_narrative=(
                    f"Case {case_id} is already escalated to {target_department} "
                    f"under ticket {existing['ticket_id']}. Duplicate escalation suppressed."
                ),
            )

        self._exception_counter += 1
        ticket_id = f"TKT-{self._exception_counter:04d}"

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

        self._in_memory_escalations[case_id] = {
            "ticket_id": ticket_id,
            "department": target_department,
            "priority": priority.value,
            "timestamp": timestamp,
        }
        self._in_memory_audit.append(
            {
                "action_name": "exception_escalate_case",
                "actor": "system",
                "timestamp": timestamp,
                "details": {
                    "case_id": case_id,
                    "ticket_id": ticket_id,
                    "target_department": target_department,
                    "reason": reason,
                    "priority": priority.value,
                    "external_ticketing": False,
                },
                "correlation_id": case_id,
            }
        )

        return EscalationResult(
            ticket_id=ticket_id,
            routed_department=target_department,
            priority=priority,
            audit_narrative=audit_narrative,
        )

    async def aescalate_case(
        self,
        case_id: str,
        reason: str,
        target_department: str,
    ) -> EscalationResult:
        """Asynchronous case escalation handler with transactional database audit logging."""
        if not case_id or not reason or not target_department:
            raise ExceptionServiceError(
                "case_id, reason, and target_department are required for escalation."
            )

        priority_map = {
            "Finance": SeverityLevel.HIGH,
            "Procurement": SeverityLevel.MEDIUM,
            "IT_Ops": SeverityLevel.MEDIUM,
            "Management": SeverityLevel.HIGH,
        }
        priority = priority_map.get(target_department, SeverityLevel.MEDIUM)

        try:
            async for session in get_db_session():
                audit_repo = AuditRepository(session)
                wf_repo = WorkflowRepository(session)

                # Check for existing duplicate escalation
                prior_entries = await audit_repo.get_by_correlation_id(case_id)
                for entry in prior_entries:
                    if (
                        entry.action_name == "exception_escalate_case"
                        and entry.details_json
                        and entry.details_json.get("target_department")
                        == target_department
                    ):
                        existing_ticket = entry.details_json.get(
                            "ticket_id", "TKT-EXISTING"
                        )
                        return EscalationResult(
                            ticket_id=existing_ticket,
                            routed_department=target_department,
                            priority=priority,
                            audit_narrative=(
                                f"Case {case_id} is already escalated to {target_department} "
                                f"under ticket {existing_ticket}. Duplicate escalation suppressed."
                            ),
                        )

                # Assign tracking ticket ID
                self._exception_counter += 1
                ticket_id = f"TKT-{self._exception_counter:04d}"
                timestamp = datetime.now(UTC).isoformat()

                async with session.begin():
                    wf = await wf_repo.get(case_id)
                    if wf is None:
                        wf = await wf_repo.create_workflow_instance(
                            workflow_type="escalated_workflow",
                            status="escalated",
                            metadata={"case_id": case_id},
                            item_id=case_id if len(case_id) <= 36 else None,
                        )
                    else:
                        await wf_repo.update_status(wf.id, "escalated")

                    await audit_repo.create_audit_entry(
                        action_name="exception_escalate_case",
                        actor="system",
                        details={
                            "case_id": case_id,
                            "ticket_id": ticket_id,
                            "target_department": target_department,
                            "reason": reason,
                            "priority": priority.value,
                            "external_ticketing": False,
                        },
                        correlation_id=case_id,
                    )

                return EscalationResult(
                    ticket_id=ticket_id,
                    routed_department=target_department,
                    priority=priority,
                    audit_narrative=(
                        f"Escalation ticket {ticket_id} created at {timestamp} "
                        f"for case {case_id}. "
                        f"Reason: {reason}. "
                        f"Routed to: {target_department}. "
                        f"Priority: {priority.value}."
                    ),
                )
        except Exception as exc:
            if isinstance(exc, ExceptionServiceError):
                raise
            if get_settings().workflow_env == "production":
                logger.error("Production database failure during escalation: %s", exc)
                raise ExceptionServiceError(
                    f"Production database escalation failure: {exc}"
                ) from exc
            logger.debug(
                "Database unavailable during escalation; falling back to in-memory: %s",
                exc,
            )

        return self.escalate_case(
            case_id=case_id,
            reason=reason,
            target_department=target_department,
        )

    async def aretrieve_exception(self, exception_id: str) -> dict[str, Any] | None:
        """Retrieve an exception record by identifier from database or memory."""
        try:
            async for session in get_db_session():
                exc_repo = ExceptionRepository(session)
                record = await exc_repo.get(exception_id)
                if record is not None:
                    return {
                        "id": record.id,
                        "workflow_id": record.workflow_id,
                        "category": record.category,
                        "root_cause": record.root_cause,
                        "is_resolved": record.is_resolved,
                        "resolved_at": (
                            record.resolved_at.isoformat()
                            if record.resolved_at
                            else None
                        ),
                        "payload": record.payload,
                    }
        except Exception as exc:
            if isinstance(exc, ExceptionServiceError):
                raise
            if get_settings().workflow_env == "production":
                logger.error(
                    "Production database failure during exception retrieval: %s", exc
                )
                raise ExceptionServiceError(
                    f"Production database retrieval failure: {exc}"
                ) from exc
            logger.debug("Database retrieval failed: %s", exc)

        return self._in_memory_exceptions.get(exception_id)

    async def aresolve_exception(
        self, exception_id: str, resolution_notes: str = ""
    ) -> dict[str, Any]:
        """Manually mark an exception resolved and record audit entry."""
        timestamp = datetime.now(UTC).isoformat()
        try:
            async for session in get_db_session():
                exc_repo = ExceptionRepository(session)
                wf_repo = WorkflowRepository(session)
                audit_repo = AuditRepository(session)

                record = await exc_repo.get(exception_id)
                if record is None:
                    raise ExceptionServiceError(
                        f"Exception '{exception_id}' not found."
                    )

                if record.is_resolved:
                    return {
                        "exception_id": exception_id,
                        "status": "already_resolved",
                        "resolved_at": record.resolved_at.isoformat()
                        if record.resolved_at
                        else None,
                    }

                async with session.begin():
                    await exc_repo.mark_resolved(record.id)
                    wf_exceptions = await exc_repo.get_by_workflow_id(
                        record.workflow_id
                    )
                    if all(e.is_resolved or e.id == record.id for e in wf_exceptions):
                        await wf_repo.complete_workflow(record.workflow_id)

                    await audit_repo.create_audit_entry(
                        action_name="exception_resolve",
                        actor="system",
                        details={
                            "exception_id": exception_id,
                            "workflow_id": record.workflow_id,
                            "notes": resolution_notes,
                            "auto_resolved": False,
                        },
                        correlation_id=record.workflow_id,
                    )

                return {
                    "exception_id": exception_id,
                    "status": "resolved",
                    "resolved_at": timestamp,
                }
        except Exception as exc:
            if isinstance(exc, ExceptionServiceError):
                raise
            if get_settings().workflow_env == "production":
                logger.error(
                    "Production database failure during exception resolution: %s", exc
                )
                raise ExceptionServiceError(
                    f"Production database resolution failure: {exc}"
                ) from exc
            logger.debug("Database unavailable; updating in-memory: %s", exc)

        in_mem = self._in_memory_exceptions.get(exception_id)
        if in_mem is None:
            raise ExceptionServiceError(f"Exception '{exception_id}' not found.")

        if in_mem.get("is_resolved"):
            return {
                "exception_id": exception_id,
                "status": "already_resolved",
                "resolved_at": in_mem.get("resolved_at"),
            }

        in_mem["is_resolved"] = True
        in_mem["resolved_at"] = timestamp
        return {
            "exception_id": exception_id,
            "status": "resolved",
            "resolved_at": timestamp,
        }
