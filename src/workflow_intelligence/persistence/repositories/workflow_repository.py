"""Workflow instance repository for the persistence layer.

Extends :class:`BaseRepository` with workflow-specific queries such as
fetching by type/status and marking a workflow as completed.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from workflow_intelligence.models.domain import WorkflowInstance
from workflow_intelligence.persistence.repositories.base import BaseRepository


class WorkflowRepository(BaseRepository[WorkflowInstance]):
    """Repository for :class:`WorkflowInstance` domain entities."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, WorkflowInstance)

    async def get_by_workflow_type(
        self, workflow_type: str, offset: int = 0, limit: int = 100
    ) -> list[WorkflowInstance]:
        """Return workflow instances of the given type, paginated."""
        result = await self._session.execute(
            select(WorkflowInstance)
            .where(WorkflowInstance.workflow_type == workflow_type)
            .offset(offset)
            .limit(limit)
        )
        return list(result.scalars().all())

    async def get_by_status(
        self, status: str, offset: int = 0, limit: int = 100
    ) -> list[WorkflowInstance]:
        """Return workflow instances with the given status, paginated."""
        result = await self._session.execute(
            select(WorkflowInstance)
            .where(WorkflowInstance.status == status)
            .offset(offset)
            .limit(limit)
        )
        return list(result.scalars().all())

    async def create_workflow_instance(
        self,
        workflow_type: str,
        status: str = "pending",
        metadata: dict[str, Any] | None = None,
    ) -> WorkflowInstance:
        """Create and persist a new workflow instance."""
        return await self.create(
            workflow_type=workflow_type,
            status=status,
            metadata_json=metadata,
        )

    async def update_status(self, item_id: str, status: str) -> WorkflowInstance | None:
        """Update the status of a workflow instance."""
        return await self.update(item_id, status=status)

    async def complete_workflow(self, item_id: str) -> WorkflowInstance | None:
        """Mark a workflow instance as completed with the current timestamp."""
        return await self.update(
            item_id, status="completed", end_time=datetime.now(UTC)
        )
