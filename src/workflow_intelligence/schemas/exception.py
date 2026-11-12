"""Pydantic V2 schemas for the Workflow Exception Resolver (exception_* tools)."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from .common import BaseMCPResponse, SeverityLevel


class ExceptionCategory(StrEnum):
    """Categories of workflow exceptions per Phase 3 triage taxonomy."""

    MASTER_DATA_DISCREPANCY = "MASTER_DATA_DISCREPANCY"
    GOVERNANCE_INCOMPLETE = "GOVERNANCE_INCOMPLETE"
    CONCURRENCY_CONFLICT = "CONCURRENCY_CONFLICT"
    TRANSIENT_NETWORK_DROP = "TRANSIENT_NETWORK_DROP"
    FINANCIAL_POLICY_BREACH = "FINANCIAL_POLICY_BREACH"


class ExceptionInvestigationResult(BaseMCPResponse):
    """Result of investigating a failed workflow case.

    Attributes:
        case_id: Unique case identifier.
        exception_id: Unique exception identifier assigned during investigation.
        category: Classified exception category.
        matched_entities: Entities matched against master data during investigation.
        classification_reason: Human-readable explanation of the classification.
        severity: Priority level of the exception.
        auto_resolvable: Whether the exception can be automatically resolved.
        recommended_remediation: Suggested remediation action.
        audit_narrative: Structured audit narrative of the investigation.
    """

    case_id: str = Field(..., description="Unique case identifier.")
    exception_id: str = Field(
        ..., description="Unique exception identifier assigned during investigation."
    )
    category: ExceptionCategory = Field(
        ..., description="Classified exception category."
    )
    matched_entities: list[MatchedEntity] = Field(
        default_factory=list,
        description="Entities matched against master data during investigation.",
    )
    classification_reason: str = Field(
        ..., description="Human-readable explanation of the classification."
    )
    severity: SeverityLevel = Field(
        default=SeverityLevel.MEDIUM,
        description="Priority level of the exception.",
    )
    auto_resolvable: bool = Field(
        default=False,
        description="Whether the exception can be automatically resolved.",
    )
    recommended_remediation: str = Field(
        ..., description="Suggested remediation action."
    )
    audit_narrative: str = Field(
        ..., description="Structured audit narrative of the investigation."
    )


class WorkflowInvestigationRequest(BaseModel):
    """Request to investigate a failed workflow case.

    Attributes:
        case_id: Unique case identifier.
        workflow_type: Type of workflow that failed.
        failure_payload: Failure details as a free-form dictionary.
    """

    case_id: str = Field(..., min_length=1, description="Unique case identifier.")
    workflow_type: str = Field(
        ..., min_length=1, description="Type of workflow that failed."
    )
    failure_payload: dict[str, Any] = Field(
        default_factory=dict,
        description="Failure details as a free-form dictionary.",
    )


class ResolutionRequest(BaseModel):
    """Request to recommend a resolution for an exception.

    Attributes:
        exception_id: Unique exception identifier.
        policy_context: Optional policy context for resolution constraints.
    """

    exception_id: str = Field(
        ..., min_length=1, description="Unique exception identifier."
    )
    policy_context: dict[str, Any] | None = Field(
        default=None,
        description="Optional policy context for resolution constraints.",
    )


class EscalationRequest(BaseModel):
    """Request to escalate an unresolved exception case.

    Attributes:
        case_id: Unique case identifier.
        reason: Reason for escalation.
        target_department: Department to route the escalation to.
    """

    case_id: str = Field(..., min_length=1, description="Unique case identifier.")
    reason: str = Field(..., min_length=1, description="Reason for escalation.")
    target_department: str = Field(
        ..., min_length=1, description="Department to route the escalation to."
    )


class MatchedEntity(BaseModel):
    """A matched entity record discovered during exception investigation.

    Attributes:
        entity_id: Registered entity identifier.
        registered_name: Official registered name of the entity.
        matching_score: Confidence score of the match (0-100).
        discrepancies: Optional list of discrepancies between submitted
            and registered data.
    """

    entity_id: str = Field(..., description="Registered entity identifier.")
    registered_name: str = Field(
        ..., description="Official registered name of the entity."
    )
    matching_score: float = Field(
        default=0.0,
        ge=0,
        le=100,
        description="Confidence score of the match (0-100).",
    )
    discrepancies: list[str] | None = Field(
        default=None, description="Optional list of discrepancies."
    )


class ResolutionPlan(BaseModel):
    """Recommended resolution plan for a workflow exception.

    Attributes:
        exception_id: Unique exception identifier.
        remediation_action: Recommended remediation action.
        auto_resolve: Whether the exception can be automatically resolved.
        required_approval_tier: Optional approval tier required
            for manual resolution.
    """

    exception_id: str = Field(
        ..., min_length=1, description="Unique exception identifier."
    )
    remediation_action: str = Field(..., description="Recommended remediation action.")
    auto_resolve: bool = Field(
        default=False,
        description="Whether the exception can be automatically resolved.",
    )
    required_approval_tier: str | None = Field(
        default=None,
        description="Optional approval tier required for manual resolution.",
    )


class EscalationResult(BaseMCPResponse):
    """Result of escalating an unresolved exception case.

    Attributes:
        ticket_id: Unique ticket identifier for the escalation.
        routed_department: Department the case was routed to.
        priority: Priority level of the escalation.
        audit_narrative: Human-readable audit narrative of the escalation.
    """

    ticket_id: str = Field(
        ..., description="Unique ticket identifier for the escalation."
    )
    routed_department: str = Field(
        ..., description="Department the case was routed to."
    )
    priority: SeverityLevel = Field(
        default=SeverityLevel.MEDIUM,
        description="Priority level of the escalation.",
    )
    audit_narrative: str = Field(
        ..., description="Human-readable audit narrative of the escalation."
    )
