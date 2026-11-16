"""Application configuration loaded from environment variables."""

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings for Workflow Intelligence MCP."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    workflow_env: Literal["development", "staging", "production"] = Field(
        default="development", alias="WORKFLOW_ENV"
    )
    server_transport: Literal[
        "stdio", "sse", "streamable-http", "http", "unified"
    ] = Field(default="stdio", alias="SERVER_TRANSPORT")
    server_host: str = Field(default="127.0.0.1", alias="SERVER_HOST")
    server_port: int = Field(default=8000, alias="SERVER_PORT")
    database_url: str = Field(
        default="postgresql+asyncpg://postgres:postgres@localhost:5432/workflow_intelligence",
        alias="DATABASE_URL",
    )
    duckdb_path: str = Field(default=":memory:", alias="DUCKDB_PATH")
    redis_url: str = Field(default="redis://localhost:6379/0", alias="REDIS_URL")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    # Security & Gateway settings
    allowed_data_dirs: list[str] = Field(
        default_factory=lambda: ["sample-data", "data", "local"],
        alias="ALLOWED_DATA_DIRS",
    )
    max_file_size_bytes: int = Field(
        default=200 * 1024 * 1024, alias="MAX_FILE_SIZE_BYTES"
    )
    mcp_auth_token: str | None = Field(default=None, alias="MCP_AUTH_TOKEN")
    trusted_auth_gateway: bool = Field(default=False, alias="TRUSTED_AUTH_GATEWAY")

    # Reliability & Execution limits
    analysis_timeout_seconds: float = Field(
        default=60.0, alias="ANALYSIS_TIMEOUT_SECONDS"
    )
    query_timeout_ms: int = Field(
        default=30000, alias="QUERY_TIMEOUT_MS"
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached application settings."""
    return Settings()
