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
    server_transport: Literal["stdio", "sse"] = Field(
        default="stdio", alias="SERVER_TRANSPORT"
    )
    server_host: str = Field(default="127.0.0.1", alias="SERVER_HOST")
    server_port: int = Field(default=8000, alias="SERVER_PORT")
    database_url: str = Field(
        default="postgresql+asyncpg://postgres:postgres@localhost:5432/workflow_intelligence",
        alias="DATABASE_URL",
    )
    duckdb_path: str = Field(default=":memory:", alias="DUCKDB_PATH")
    redis_url: str = Field(default="redis://localhost:6379/0", alias="REDIS_URL")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached application settings."""
    return Settings()
