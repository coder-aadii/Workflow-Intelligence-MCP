"""FastMCP protocol server for the Workflow Intelligence MCP.

Assembles the three core tool suites (``process_*``, ``exception_*``,
``decision_*``) plus MCP Resources, MCP Prompts, and observability
(structured logging and OpenTelemetry tracing) into a single
``workflow-intelligence`` FastMCP instance. Exposes STDIO (default) and
Streamable HTTP / SSE transports, and wires startup/shutdown lifecycle
hooks plus a CLI entry point.
"""

from __future__ import annotations

import argparse
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse

from workflow_intelligence import __version__
from workflow_intelligence.config import get_settings
from workflow_intelligence.observability.logging import (
    configure_logging,
    get_logger,
    log_context,
)
from workflow_intelligence.observability.tracing import init_tracing
from workflow_intelligence.prompts import register_prompts
from workflow_intelligence.resources import register_resources
from workflow_intelligence.security.auth import (
    StaticTokenVerifier,
    validate_production_auth_token,
    validate_production_database_credentials,
)
from workflow_intelligence.services.analytics_service import AnalyticsService
from workflow_intelligence.services.decision_service import DecisionService
from workflow_intelligence.services.exception_service import ExceptionService
from workflow_intelligence.services.process_service import ProcessService
from workflow_intelligence.tools.decision_intelligence import register_decision_tools
from workflow_intelligence.tools.exception_resolver import register_exception_tools
from workflow_intelligence.tools.process_discovery import register_process_tools

logger = get_logger("workflow_intelligence.server")

DEFAULT_SOURCE_URI = "sample-data/procurement_events.csv"
DEFAULT_DATA_DIR = Path(__file__).resolve().parents[2] / "sample-data"


@asynccontextmanager
async def _lifespan(server: FastMCP) -> AsyncGenerator[None, None]:
    """Server lifespan combining startup and shutdown hooks.

    Startup: configure structured logging and OpenTelemetry tracing,
    initialize shared application services, and register the default
    procurement cost dataset so decision tools can resolve it without a
    caller-supplied source URI.
    Shutdown: release the DuckDB adapter connection.
    """
    settings = get_settings()
    configure_logging(
        workflow_env=settings.workflow_env,
        log_level=settings.log_level,
    )
    init_tracing(workflow_env=settings.workflow_env)

    with log_context(
        correlation_id="server-startup",
        tool_name="server",
    ):
        logger.info(
            "Starting Workflow Intelligence MCP server (env=%s)",
            settings.workflow_env,
        )

    analytics = AnalyticsService()
    try:
        analytics.register_source()
        logger.info("Registered default analytics source: %s", analytics.source_uri)
    except Exception as exc:  # noqa: BLE001 - startup must not hard-fail the server
        logger.warning("Could not register default analytics source: %s", exc)

    try:
        from workflow_intelligence.persistence.database import init_db

        await init_db()
        logger.info("Database persistence initialized successfully.")
    except Exception as exc:  # noqa: BLE001
        if settings.workflow_env == "production":
            logger.critical("Production database initialization failed: %s", exc)
            raise RuntimeError(
                f"Production database initialization failed: {exc}"
            ) from exc
        logger.warning(
            "Database persistence unavailable; running in non-persistent demo mode: %s",
            exc,
        )

    try:
        yield
    finally:
        with log_context(
            correlation_id="server-shutdown",
            tool_name="server",
        ):
            logger.info("Shutting down Workflow Intelligence MCP server")
        analytics.adapter.close()


def create_server() -> FastMCP:
    """Build and configure the unified FastMCP server.

    Registers:

    - All nine domain tools across the ``process_*``, ``exception_*``, and
      ``decision_*`` tool suites.
    - MCP Resources: ``workflow://process-maps/{process_name}``,
      ``workflow://exceptions/active``,
      ``workflow://metrics/kpi-definitions``, ``workflow://audit/recent``.
    - MCP Prompts: ``process_bottleneck_review``,
      ``exception_triage_session``, ``management_decision_brief``.

    Returns:
        Configured FastMCP server instance.
    """
    settings = get_settings()
    configure_logging(
        workflow_env=settings.workflow_env,
        log_level=settings.log_level,
    )

    auth_verifier = None
    if settings.mcp_auth_token:
        if settings.workflow_env == "production":
            validate_production_auth_token(settings.mcp_auth_token)
        auth_verifier = StaticTokenVerifier(settings.mcp_auth_token)
        logger.info("Configured bearer token authentication for MCP endpoints.")
    elif settings.workflow_env == "production" and settings.server_transport != "stdio":
        validate_production_auth_token(settings.mcp_auth_token)

    mcp = FastMCP(
        name="workflow-intelligence",
        version=__version__,
        instructions=(
            "Workflow Intelligence MCP server. Provides process discovery, "
            "workflow exception resolution, and business decision intelligence "
            "tools backed by deterministic analytics, plus MCP resources, "
            "prompts, and observability."
        ),
        lifespan=_lifespan,
        auth=auth_verifier,
    )

    from workflow_intelligence.dependencies import get_service_container

    container = get_service_container()

    register_process_tools(mcp, container.process_service)
    register_exception_tools(mcp, container.exception_service)
    register_decision_tools(mcp, container.decision_service)

    register_resources(
        mcp, container.process_service, DEFAULT_SOURCE_URI, data_dir=DEFAULT_DATA_DIR
    )
    register_prompts(mcp)

    @mcp.custom_route("/health", methods=["GET"])
    async def _health_check(request: Request) -> JSONResponse:
        """Health check endpoint for container probes and load balancers."""
        return JSONResponse(
            {
                "status": "healthy",
                "service": "workflow-intelligence",
                "version": __version__,
            }
        )

    @mcp.custom_route("/ready", methods=["GET"])
    async def _readiness_check(request: Request) -> JSONResponse:
        """Readiness probe verifying services, database, and MCP registries are initialized."""
        from workflow_intelligence.persistence.database import (
            check_db_health,
            is_db_initialized,
        )

        db_healthy = await check_db_health()
        db_initialized = is_db_initialized()

        # In production, failed database initialization or broken connectivity must fail readiness
        if settings.workflow_env == "production" and (
            not db_initialized or not db_healthy
        ):
            return JSONResponse(
                {
                    "status": "not_ready",
                    "service": "workflow-intelligence",
                    "error": (
                        "Database not initialized"
                        if not db_initialized
                        else "Database connectivity check failed"
                    ),
                    "database_initialized": db_initialized,
                    "database_healthy": db_healthy,
                    "tools_count": 9,
                    "resources_count": 4,
                    "prompts_count": 3,
                },
                status_code=503,
            )

        return JSONResponse(
            {
                "status": "ready",
                "service": "workflow-intelligence",
                "database_initialized": db_initialized,
                "database_healthy": db_healthy,
                "tools_count": 9,
                "resources_count": 4,
                "prompts_count": 3,
            }
        )

    logger.info(
        "Registered 9 domain tools, 4 resources, and 3 prompts on "
        "FastMCP server 'workflow-intelligence'"
    )
    return mcp


def create_unified_http_app(mcp: FastMCP) -> Starlette:
    """Create a unified HTTP application supporting Streamable HTTP (/mcp),
    legacy SSE (/sse, /messages), and health probes (/health, /ready) on the same port.
    """
    streamable_app = mcp.http_app(transport="streamable-http")
    sse_app = mcp.http_app(transport="sse")

    @asynccontextmanager
    async def unified_lifespan(app: Starlette) -> AsyncGenerator[None, None]:
        async with mcp._lifespan_manager():
            async with streamable_app.router.lifespan_context(streamable_app):
                async with sse_app.router.lifespan_context(sse_app):
                    yield

    routes = (
        list(mcp._additional_http_routes)
        + list(streamable_app.routes)
        + list(sse_app.routes)
    )
    return Starlette(
        routes=routes,
        middleware=streamable_app.user_middleware,
        lifespan=unified_lifespan,
    )


def _build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser for the MCP server."""
    settings = get_settings()
    parser = argparse.ArgumentParser(
        prog="workflow-intelligence-mcp",
        description="Workflow Intelligence MCP server.",
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse", "streamable-http", "http", "unified"],
        default=settings.server_transport,
        help="Transport protocol (stdio, sse, streamable-http, http, or unified; default: %(default)s).",
    )
    parser.add_argument(
        "--host",
        default=settings.server_host,
        help="Host to bind for HTTP/SSE transports (default: 127.0.0.1).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=settings.server_port,
        help="Port to bind for HTTP/SSE transports (default: 8000).",
    )
    return parser


def main() -> None:
    """CLI entry point for the Workflow Intelligence MCP server."""
    parser = _build_parser()
    args = parser.parse_args()
    settings = get_settings()

    # Always ensure structured logging is configured to stream to sys.stderr,
    # preventing stdout corruption for STDIO JSON-RPC clients.
    configure_logging(workflow_env=settings.workflow_env, log_level=settings.log_level)

    # In production, enforce strict security safeguards on credentials and authentication
    if settings.workflow_env == "production":
        validate_production_database_credentials(
            database_url=str(settings.database_url),
            postgres_password=settings.postgres_password,
        )

        if args.transport != "stdio":
            validate_production_auth_token(settings.mcp_auth_token)

    mcp = create_server()

    if args.transport == "stdio":
        mcp.run(transport="stdio")
    elif args.transport in ("streamable-http", "http"):
        mcp.run(transport="streamable-http", host=args.host, port=args.port)
    elif args.transport == "sse":
        mcp.run(transport="sse", host=args.host, port=args.port)
    else:  # "unified"
        import uvicorn

        unified_app = create_unified_http_app(mcp)
        logger.info(
            "Starting unified MCP server on http://%s:%d (Streamable HTTP: /mcp, SSE: /sse)",
            args.host,
            args.port,
        )
        uvicorn.run(
            unified_app,
            host=args.host,
            port=args.port,
            log_level=settings.log_level.lower(),
        )


if __name__ == "__main__":
    main()
