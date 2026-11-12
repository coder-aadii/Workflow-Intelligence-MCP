"""MCP tool handlers for the Workflow Exception Resolver (exception_* tools).

Handlers are thin transport layers: they validate input via Pydantic schemas,
delegate to ExceptionService, and return validated output schemas.
"""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP

from workflow_intelligence.schemas.exception import (
    EscalationRequest,
    ResolutionRequest,
    WorkflowInvestigationRequest,
)
from workflow_intelligence.services.exception_service import ExceptionService


def register_exception_tools(mcp: FastMCP, service: ExceptionService) -> None:
    """Register workflow exception resolver tools with the FastMCP server.

    Args:
        mcp: FastMCP server instance.
        service: ExceptionService instance for business logic.
    """

    @mcp.tool()
    async def exception_investigate_workflow(
        case_id: str,
        workflow_type: str,
        failure_payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Investigate a failed workflow case and classify the exception.

        Classifies the failure into a standard category, performs entity
        reconciliation against master data, and produces a structured
        investigation result with audit trail.

        Args:
            case_id: Unique case identifier.
            workflow_type: Type of workflow that failed.
            failure_payload: Failure details as a free-form dictionary.

        Returns:
            Exception investigation result as a dictionary.
        """
        request = WorkflowInvestigationRequest(
            case_id=case_id,
            workflow_type=workflow_type,
            failure_payload=failure_payload,
        )
        result = service.investigate_failure(request)
        return result.model_dump()

    @mcp.tool()
    async def exception_recommend_resolution(
        exception_id: str,
        policy_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Recommend a resolution plan for a classified exception.

        Evaluates business policy context and generates a remediation
        action with auto-resolve eligibility.

        Args:
            exception_id: Unique exception identifier.
            policy_context: Optional policy context for resolution constraints.

        Returns:
            Resolution plan as a dictionary.
        """
        request = ResolutionRequest(
            exception_id=exception_id,
            policy_context=policy_context,
        )
        result = service.recommend_resolution(request)
        return result.model_dump()

    @mcp.tool()
    async def exception_escalate_case(
        case_id: str,
        reason: str,
        target_department: str,
    ) -> dict[str, Any]:
        """Escalate an unresolved exception case to a functional department.

        Generates a unique escalation tracking ticket with full audit trail.

        Args:
            case_id: Unique case identifier.
            reason: Reason for escalation.
            target_department: Department to route the escalation to.

        Returns:
            Escalation result as a dictionary.
        """
        request = EscalationRequest(
            case_id=case_id,
            reason=reason,
            target_department=target_department,
        )
        result = service.escalate_case(
            case_id=request.case_id,
            reason=request.reason,
            target_department=request.target_department,
        )
        return result.model_dump()
