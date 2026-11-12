"""Async SQLAlchemy database engine and session management.

Provides:
- An async engine built from the configured ``DATABASE_URL`` (PostgreSQL
  via asyncpg in production; SQLite via aiosqlite for offline tests).
- A declarative ORM base shared by all domain models.
- An async session factory and a ``get_db_session()`` context manager.
- Helpers to create/drop all tables for test databases.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

from sqlalchemy import MetaData, event
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, declared_attr

from workflow_intelligence.config import get_settings


class Base(DeclarativeBase):
    """Declarative base for all Workflow Intelligence ORM models.

    Subclasses automatically get a lowercase-tablename derived from the
    class name (e.g. ``WorkflowInstance`` -> ``workflow_instance``).
    """

    metadata = MetaData()

    @declared_attr.directive
    def __tablename__(cls) -> str:  # noqa: N805
        return cls.__name__.lower()


# Lazily-initialized engine / session factory. The engine is created on
# first use so that tests can override the database URL before any session
# is requested.
_engine: Any = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def _build_engine(database_url: str | None = None) -> Any:
    """Create the async SQLAlchemy engine for the given database URL."""
    settings = get_settings()
    url = database_url or settings.database_url
    # ``echo`` is enabled only in development for visibility into SQL.
    echo = settings.workflow_env == "development"
    engine = create_async_engine(url, echo=echo, pool_pre_ping=True)

    # SQLite does not enforce foreign-key constraints unless explicitly
    # enabled per connection. Enable them so ON DELETE CASCADE works in
    # offline/test databases.
    if engine.dialect.name == "sqlite":

        @event.listens_for(engine.sync_engine, "connect")
        def _set_sqlite_foreign_keys(
            dbapi_connection: Any, connection_record: Any
        ) -> None:  # noqa: ARG001
            cursor = dbapi_connection.cursor()
            try:
                cursor.execute("PRAGMA foreign_keys = ON")
            finally:
                cursor.close()

    return engine


def get_engine(database_url: str | None = None) -> Any:
    """Return the lazily-initialized async engine."""
    global _engine
    if _engine is None:
        _engine = _build_engine(database_url)
    return _engine


def reset_engine(database_url: str | None = None) -> None:
    """Dispose and recreate the engine (useful in test teardown)."""
    global _engine, _session_factory
    if _engine is not None:
        try:  # best effort; never block engine recreation on disposal
            _engine.sync_engine.dispose()
        except Exception:  # noqa: BLE001 - best effort disposal
            pass
    _engine = None
    _session_factory = None
    _engine = _build_engine(database_url)


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Return the async session factory, initializing the engine if needed."""
    global _session_factory
    if _session_factory is None:
        engine = get_engine()
        _session_factory = async_sessionmaker(
            engine, class_=AsyncSession, expire_on_commit=False
        )
    return _session_factory


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Yield an async SQLAlchemy session and ensure it is closed afterward."""
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
        finally:
            await session.close()


async def init_db(database_url: str | None = None) -> None:
    """Create all ORM tables defined on ``Base.metadata``.

    Intended for application startup and test database bootstrapping.
    """
    engine = get_engine(database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def drop_all(database_url: str | None = None) -> None:
    """Drop all ORM tables (test cleanup helper)."""
    engine = get_engine(database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
