"""Shared pytest fixtures for Workflow Intelligence MCP tests."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.fixture
def isolated_duckdb_path(tmp_path: Path) -> Path:
    """Provide an isolated DuckDB file path for tests."""
    return tmp_path / "test.duckdb"


@pytest.fixture
def mock_env(isolated_duckdb_path: Path) -> Iterator[None]:
    """Set deterministic environment variables for configuration tests."""
    original = os.environ.copy()
    os.environ.update(
        {
            "WORKFLOW_ENV": "development",
            "SERVER_TRANSPORT": "stdio",
            "SERVER_HOST": "127.0.0.1",
            "SERVER_PORT": "8000",
            "DATABASE_URL": (
                "postgresql+asyncpg://postgres:postgres@localhost:5432/"
                "workflow_intelligence"
            ),
            "DUCKDB_PATH": str(isolated_duckdb_path),
            "REDIS_URL": "redis://localhost:6379/0",
            "LOG_LEVEL": "INFO",
        }
    )
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(original)
