"""SQLAlchemy ORM domain models for the Workflow Intelligence persistence layer.

Defines:
- ``WorkflowInstance``: a tracked business workflow case.
- ``ExceptionRecord``: a persisted exception linked to a workflow instance.
- ``AuditLog``: an immutable audit trail entry for state mutations.

All models use a stringified UUID4 primary key and share the declarative
``Base`` from :mod:`workflow_intelligence.persistence.database`. String
primary keys keep the schema portable across PostgreSQL (production) and
SQLite (offline tests) without dialect-specific column types.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from workflow_intelligence.persistence.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class WorkflowInstance(Base):
    """A tracked business workflow instance/case.

    Attributes:
        id: Unique workflow instance identifier (stringified UUID4).
        workflow_type: Type of workflow (e.g. ``purchase_order_creation``).
        status: Current lifecycle status.
        start_time: When the workflow started.
        end_time: When the workflow completed (nullable while running).
        metadata_json: Arbitrary structured metadata as JSON.
        exceptions: Related exception records (one-to-many).
    """

    __tablename__ = "workflow_instance"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=_uuid,
        comment="Unique workflow instance identifier.",
    )
    workflow_type: Mapped[str] = mapped_column(
        String(100), nullable=False, comment="Type of workflow."
    )
    status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="pending",
        comment="Current lifecycle status.",
    )
    start_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
        comment="When the workflow started.",
    )
    end_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="When the workflow completed.",
    )
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(
        JSON(), comment="Arbitrary structured metadata as JSON."
    )

    exceptions: Mapped[list[ExceptionRecord]] = relationship(
        "ExceptionRecord",
        back_populates="workflow",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class ExceptionRecord(Base):
    """A persisted workflow exception linked to a workflow instance.

    Attributes:
        id: Unique exception record identifier (stringified UUID4).
        workflow_id: Foreign key to the parent workflow instance.
        category: Classified exception category.
        root_cause: Human-readable root-cause description.
        is_resolved: Whether the exception has been resolved.
        resolved_at: When the exception was resolved (nullable).
        payload: Original failure payload as JSON.
        workflow: Parent workflow instance (many-to-one).
    """

    __tablename__ = "exception_record"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=_uuid,
        comment="Unique exception record identifier.",
    )
    workflow_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("workflow_instance.id", ondelete="CASCADE"),
        nullable=False,
        comment="Parent workflow instance identifier.",
    )
    category: Mapped[str] = mapped_column(
        String(50), nullable=False, comment="Classified exception category."
    )
    root_cause: Mapped[str] = mapped_column(
        Text, nullable=False, comment="Root-cause description."
    )
    is_resolved: Mapped[bool] = mapped_column(
        default=False,
        nullable=False,
        comment="Whether the exception has been resolved.",
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="When the exception was resolved.",
    )
    payload: Mapped[dict[str, Any] | None] = mapped_column(
        JSON(), comment="Original failure payload as JSON."
    )

    workflow: Mapped[WorkflowInstance] = relationship(
        "WorkflowInstance", back_populates="exceptions", lazy="selectin"
    )


class AuditLog(Base):
    """An immutable audit trail entry for state mutations.

    Attributes:
        id: Unique audit log identifier (stringified UUID4).
        action_name: Name of the performed action.
        actor: Actor who performed the action (user/system).
        timestamp: UTC timestamp of the action.
        details_json: Structured details of the action as JSON.
        correlation_id: Optional correlation identifier linking related
            audit entries.
    """

    __tablename__ = "audit_log"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=_uuid,
        comment="Unique audit log identifier.",
    )
    action_name: Mapped[str] = mapped_column(
        String(100), nullable=False, comment="Name of the performed action."
    )
    actor: Mapped[str] = mapped_column(
        String(100), nullable=False, comment="Actor who performed the action."
    )
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
        comment="UTC timestamp of the action.",
    )
    details_json: Mapped[dict[str, Any] | None] = mapped_column(
        JSON(), comment="Structured details of the action as JSON."
    )
    correlation_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        comment="Correlation identifier linking related audit entries.",
    )
