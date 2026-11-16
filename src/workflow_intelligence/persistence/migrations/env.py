"""Alembic environment configuration for Workflow Intelligence migrations."""

from __future__ import annotations

import os
from logging.config import fileConfig
from typing import Any

from alembic import context
from sqlalchemy import engine_from_config, pool
from sqlalchemy.ext.asyncio import async_engine_from_config

# Import the declarative Base so Alembic can detect model metadata for
# autogeneration and offline migration rendering.
from workflow_intelligence.models.domain import (  # noqa: F401
    AuditLog,  # noqa: F401
    ExceptionRecord,  # noqa: F401
    WorkflowInstance,  # noqa: F401
)
from workflow_intelligence.persistence.database import Base  # noqa: F401

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Add your model's MetaData object here for 'autogenerate' support.
target_metadata = Base.metadata

# Allow the DATABASE_URL environment variable or settings to override sqlalchemy.url
# in alembic.ini so credentials never need to be committed.
database_url = os.environ.get("DATABASE_URL")
if not database_url:
    try:
        from workflow_intelligence.config import get_settings

        database_url = get_settings().database_url
    except Exception:
        pass
if database_url:
    config.set_main_option("sqlalchemy.url", database_url)


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL and not an Engine, though an
    Engine is acceptable here as well.  By skipping the Engine creation we
    don't even need a DBAPI to be available.
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Any) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Run migrations in 'online' mode with an async connection."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    Supports both asyncpg (via asyncio) and standard synchronous engines.
    """
    url = config.get_main_option("sqlalchemy.url") or ""
    if "+asyncpg" in url or "async" in url:
        import asyncio

        asyncio.run(run_async_migrations())
    else:
        connectable = engine_from_config(
            config.get_section(config.config_ini_section, {}),
            prefix="sqlalchemy.",
            poolclass=pool.NullPool,
        )

        with connectable.connect() as connection:
            do_run_migrations(connection)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
