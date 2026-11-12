"""Async repository base class for SQLAlchemy domain models.

Provides generic CRUD operations (get, create, update, delete, list) that
concrete repositories inherit from. All methods operate on an injected
:class:`sqlalchemy.ext.asyncio.AsyncSession` so repositories remain
decoupled from session management and easy to unit test.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase


class BaseRepository[ModelT: DeclarativeBase]:
    """Generic async repository for a SQLAlchemy declarative model.

    Args:
        session: Async SQLAlchemy session to use for all operations.
        model: The declarative model class this repository manages.
    """

    def __init__(self, session: AsyncSession, model: type[ModelT]) -> None:
        self._session = session
        self._model = model

    async def get(self, item_id: str) -> ModelT | None:
        """Return the model instance with the given primary key, or None."""
        result = await self._session.execute(
            select(self._model).where(self._model.id == item_id)  # type: ignore[attr-defined]
        )
        return result.scalar_one_or_none()

    async def list(self, offset: int = 0, limit: int = 100) -> list[ModelT]:
        """Return a paginated list of model instances."""
        result = await self._session.execute(
            select(self._model).offset(offset).limit(limit)
        )
        return list(result.scalars().all())

    async def create(self, **attributes: Any) -> ModelT:
        """Create and persist a new model instance with the given attributes."""
        instance = self._model(**attributes)
        self._session.add(instance)
        await self._session.flush()
        await self._session.refresh(instance)
        return instance

    async def update(self, item_id: str, **attributes: Any) -> ModelT | None:
        """Update attributes on the model instance with the given id.

        Returns the updated instance, or None if not found.
        """
        instance = await self.get(item_id)
        if instance is None:
            return None
        for key, value in attributes.items():
            setattr(instance, key, value)
        await self._session.flush()
        await self._session.refresh(instance)
        return instance

    async def delete(self, item_id: str) -> bool:
        """Delete the model instance with the given id.

        Returns True if an instance was deleted, False if not found.
        """
        instance = await self.get(item_id)
        if instance is None:
            return False
        await self._session.delete(instance)
        await self._session.flush()
        return True
