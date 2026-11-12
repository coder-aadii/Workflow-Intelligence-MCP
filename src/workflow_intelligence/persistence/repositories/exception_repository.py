"""Exception record repository for the persistence layer.

Extends :class:`BaseRepository` with exception-specific queries such as
fetching by workflow, category, and resolution status.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from workflow_intelligence.models.domain import ExceptionRecord
from workflow_intelligence.persistence.repositories.base import BaseRepository


class ExceptionRepository(BaseRepository[ExceptionRecord]):
    """Repository for :class:`ExceptionRecord` domain entities."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, ExceptionRecord)

    async def get_by_workflow_id(self, workflow_id: str) -> list[ExceptionRecord]:
        """Return all exception records for a given workflow instance."""
        result = await self._session.execute(
            select(ExceptionRecord).where(ExceptionRecord.workflow_id == workflow_id)
        )
        return list(result.scalars().all())

    async def get_by_category(
        self, category: str, offset: int = 0, limit: int = 100
    ) -> list[ExceptionRecord]:
        """Return exception records of the given category, paginated."""
        result = await self._session.execute(
            select(ExceptionRecord)
            .where(ExceptionRecord.category == category)
            .offset(offset)
            .limit(limit)
        )
        return list(result.scalars().all())

    async def get_unresolved(
        self, offset: int = 0, limit: int = 100
    ) -> list[ExceptionRecord]:
        """Return unresolved exception records, paginated."""
        result = await self._session.execute(
            select(ExceptionRecord)
            .where(ExceptionRecord.is_resolved.is_(False))
            .offset(offset)
            .limit(limit)
        )
        return list(result.scalars().all())

    async def create_exception(
        self,
        workflow_id: str,
        category: str,
        root_cause: str,
        payload: dict[str, Any] | None = None,
    ) -> ExceptionRecord:
        """Create and persist a new exception record."""
        return await self.create(
            workflow_id=workflow_id,
            category=category,
            root_cause=root_cause,
            payload=payload,
        )

    async def mark_resolved(self, item_id: str) -> ExceptionRecord | None:
        """Mark an exception record as resolved with the current timestamp."""
        return await self.update(
            item_id, is_resolved=True, resolved_at=datetime.now(UTC)
        )
