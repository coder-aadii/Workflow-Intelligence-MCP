"""Initial schema for Workflow Intelligence persistence layer.

Creates the core relational tables:
- workflow_instance
- exception_record
- audit_log
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "001_initial_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the initial tables."""
    op.create_table(
        "workflow_instance",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workflow_type", sa.String(length=100), nullable=False),
        sa.Column(
            "status",
            sa.String(length=50),
            nullable=False,
            server_default="pending",
        ),
        sa.Column(
            "start_time",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("end_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workflow_instance")),
    )
    op.create_index(
        op.f("ix_workflow_instance_workflow_type"),
        "workflow_instance",
        ["workflow_type"],
        unique=False,
    )
    op.create_index(
        op.f("ix_workflow_instance_status"),
        "workflow_instance",
        ["status"],
        unique=False,
    )

    op.create_table(
        "exception_record",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workflow_id", sa.String(length=36), nullable=False),
        sa.Column("category", sa.String(length=50), nullable=False),
        sa.Column("root_cause", sa.Text(), nullable=False),
        sa.Column(
            "is_resolved",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(
            ["workflow_id"],
            ["workflow_instance.id"],
            name=op.f("fk_exception_record_workflow_id_workflow_instance"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_exception_record")),
    )
    op.create_index(
        op.f("ix_exception_record_workflow_id"),
        "exception_record",
        ["workflow_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_exception_record_category"),
        "exception_record",
        ["category"],
        unique=False,
    )
    op.create_index(
        op.f("ix_exception_record_is_resolved"),
        "exception_record",
        ["is_resolved"],
        unique=False,
    )

    op.create_table(
        "audit_log",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("action_name", sa.String(length=100), nullable=False),
        sa.Column("actor", sa.String(length=100), nullable=False),
        sa.Column(
            "timestamp",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("details_json", sa.JSON(), nullable=True),
        sa.Column("correlation_id", sa.String(length=255), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_log")),
    )
    op.create_index(
        op.f("ix_audit_log_correlation_id"),
        "audit_log",
        ["correlation_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_audit_log_action_name"),
        "audit_log",
        ["action_name"],
        unique=False,
    )


def downgrade() -> None:
    """Drop the initial tables."""
    op.drop_index(
        op.f("ix_audit_log_action_name"),
        table_name="audit_log",
    )
    op.drop_index(
        op.f("ix_audit_log_correlation_id"),
        table_name="audit_log",
    )
    op.drop_table("audit_log")
    op.drop_index(
        op.f("ix_exception_record_is_resolved"),
        table_name="exception_record",
    )
    op.drop_index(
        op.f("ix_exception_record_category"),
        table_name="exception_record",
    )
    op.drop_index(
        op.f("ix_exception_record_workflow_id"),
        table_name="exception_record",
    )
    op.drop_table("exception_record")
    op.drop_index(
        op.f("ix_workflow_instance_status"),
        table_name="workflow_instance",
    )
    op.drop_index(
        op.f("ix_workflow_instance_workflow_type"),
        table_name="workflow_instance",
    )
    op.drop_table("workflow_instance")
