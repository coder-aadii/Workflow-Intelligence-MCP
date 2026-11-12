"""Audit log repository for the persistence layer.

Extends :class:`BaseRepository` with audit-specific queries such as
fetching by correlation id or action name.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from workflow_intelligence.models.domain import AuditLog
from workflow_intelligence.persistence.repositories.base import BaseRepository


class AuditRepository(BaseRepository[AuditLog]):
    """Repository for :class:`AuditLog` domain entities."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, AuditLog)

    async def get_by_correlation_id(self, correlation_id: str) -> list[AuditLog]:
        """Return all audit entries sharing the given correlation id."""
        result = await self._session.execute(
            select(AuditLog).where(AuditLog.correlation_id == correlation_id)
        )
        return list(result.scalars().all())

    async def get_by_action(
        self, action_name: str, offset: int = 0, limit: int = 100
    ) -> list[AuditLog]:
        """Return audit entries for the given action, paginated."""
        result = await self._session.execute(
            select(AuditLog)
            .where(AuditLog.action_name == action_name)
            .offset(offset)
            .limit(limit)
        )
        return list(result.scalars().all())

    async def create_audit_entry(
        self,
        action_name: str,
        actor: str,
        details: dict[str, Any] | None = None,
        correlation_id: str | None = None,
    ) -> AuditLog:
        """Create and persist a new audit log entry."""
        return await self.create(
            action_name=action_name,
            actor=actor,
            details_json=details,
            correlation_id=correlation_id,
        )
