"""Tests for application configuration loading."""

from pathlib import Path

from workflow_intelligence.config import Settings, get_settings


def test_settings_defaults() -> None:
    """Settings should expose documented defaults."""
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.workflow_env == "development"
    assert settings.server_transport == "stdio"
    assert settings.server_host == "127.0.0.1"
    assert settings.server_port == 8000
    assert settings.duckdb_path == ":memory:"
    assert settings.redis_url == "redis://localhost:6379/0"
    assert settings.log_level == "INFO"


def test_settings_loads_env_example(tmp_path: Path) -> None:
    """Settings should parse values from an env file like .env.example."""
    env_file = tmp_path / ".env.example"
    env_file.write_text(
        "\n".join(
            [
                "WORKFLOW_ENV=staging",
                "SERVER_TRANSPORT=sse",
                "SERVER_HOST=0.0.0.0",
                "SERVER_PORT=9000",
                "DATABASE_URL=postgresql+asyncpg://user:pass@db:5432/wi",
                "DUCKDB_PATH=/tmp/wi.duckdb",
                "REDIS_URL=redis://redis:6379/1",
                "LOG_LEVEL=DEBUG",
            ]
        ),
        encoding="utf-8",
    )

    settings = Settings(_env_file=env_file)  # type: ignore[call-arg]
    assert settings.workflow_env == "staging"
    assert settings.server_transport == "sse"
    assert settings.server_host == "0.0.0.0"
    assert settings.server_port == 9000
    assert settings.database_url == "postgresql+asyncpg://user:pass@db:5432/wi"
    assert settings.duckdb_path == "/tmp/wi.duckdb"
    assert settings.redis_url == "redis://redis:6379/1"
    assert settings.log_level == "DEBUG"


def test_get_settings_cached() -> None:
    """get_settings should return a cached Settings instance."""
    get_settings.cache_clear()
    first = get_settings()
    second = get_settings()
    assert first is second
    get_settings.cache_clear()
