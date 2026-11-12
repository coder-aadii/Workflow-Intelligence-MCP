"""Common Pydantic V2 schemas: base envelope, enums, pagination, audit records."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class SeverityLevel(StrEnum):
    """Severity levels for alerts, exceptions, and audit records."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class ExecutionStatus(StrEnum):
    """Execution statuses for tool invocations and state transitions."""

    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"
    PARTIAL = "PARTIAL"
    ESCALATED = "ESCALATED"


class BaseMCPResponse(BaseModel):
    """Root envelope for all MCP tool responses.

    Attributes:
        success: Whether the operation succeeded.
        timestamp: UTC timestamp of response generation.
        execution_id: Unique execution identifier (UUID4).
        warnings: Optional list of non-fatal warnings.
    """

    success: bool = Field(default=True, description="Whether the operation succeeded.")
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="UTC timestamp of response generation.",
    )
    execution_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Unique execution identifier (UUID4).",
    )
    warnings: list[str] | None = Field(
        default=None, description="Optional list of non-fatal warnings."
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "success": True,
                "timestamp": "2026-11-12T08:00:00+00:00",
                "execution_id": "550e8400-e29b-41d4-a716-446655440000",
                "warnings": None,
            }
        }
    }


class PaginationParams(BaseModel):
    """Pagination parameters with boundary validation.

    Attributes:
        offset: Zero-based starting offset (default 0).
        limit: Maximum number of items to return (default 100, max 1000).
    """

    offset: int = Field(default=0, ge=0, description="Zero-based starting offset.")
    limit: int = Field(
        default=100,
        ge=1,
        le=1000,
        description="Maximum number of items to return.",
    )


class AuditRecord(BaseModel):
    """Standard schema for recording tool operations and state changes.

    Attributes:
        operation: Name of the performed operation.
        tool_name: MCP tool that generated the record.
        case_id: Optional associated case identifier.
        timestamp: UTC timestamp of the record.
        status: Execution status of the operation.
        details: Optional additional structured details.
    """

    operation: str = Field(..., description="Name of the performed operation.")
    tool_name: str = Field(..., description="MCP tool that generated the record.")
    case_id: str | None = Field(
        default=None, description="Optional associated case identifier."
    )
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="UTC timestamp of the record.",
    )
    status: ExecutionStatus = Field(
        default=ExecutionStatus.SUCCESS,
        description="Execution status of the operation.",
    )
    details: dict[str, Any] | None = Field(
        default=None, description="Optional additional structured details."
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "operation": "process_analyze_business_process",
                "tool_name": "process_*",
                "case_id": "PO-1001",
                "timestamp": "2026-11-12T08:00:00+00:00",
                "status": "SUCCESS",
                "details": None,
            }
        }
    }
