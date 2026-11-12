"""FastMCP protocol server for the Workflow Intelligence MCP.

Assembles the three core tool suites (``process_*``, ``exception_*``,
``decision_*``) into a single ``workflow-intelligence`` FastMCP instance,
exposes STDIO (default) and Streamable HTTP / SSE transports, and wires
startup/shutdown lifecycle hooks plus a CLI entry point.
"""

from __future__ import annotations

import argparse
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastmcp import FastMCP

from workflow_intelligence.config import get_settings
from workflow_intelligence.services.analytics_service import AnalyticsService
from workflow_intelligence.services.decision_service import DecisionService
from workflow_intelligence.services.exception_service import ExceptionService
from workflow_intelligence.services.process_service import ProcessService
from workflow_intelligence.tools.decision_intelligence import register_decision_tools
from workflow_intelligence.tools.exception_resolver import register_exception_tools
from workflow_intelligence.tools.process_discovery import register_process_tools

logger = logging.getLogger("workflow_intelligence.server")


@asynccontextmanager
async def _lifespan(server: FastMCP) -> AsyncGenerator[None, None]:
    """Server lifespan combining startup and shutdown hooks.

    Startup: initialize shared application services and register the default
    procurement cost dataset so decision tools can resolve it without a
    caller-supplied source URI.
    Shutdown: release the DuckDB adapter connection.
    """
    settings = get_settings()
    logger.info(
        "Starting Workflow Intelligence MCP server (env=%s)", settings.workflow_env
    )

    analytics = AnalyticsService()
    try:
        analytics.register_source()
        logger.info("Registered default analytics source: %s", analytics.source_uri)
    except Exception as exc:  # noqa: BLE001 - startup must not hard-fail the server
        logger.warning("Could not register default analytics source: %s", exc)

    try:
        yield
    finally:
        logger.info("Shutting down Workflow Intelligence MCP server")
        analytics.adapter.close()


def create_server() -> FastMCP:
    """Build and configure the unified FastMCP server.

    Registers all nine domain tools across the three tool suites:

    - ``process_analyze_business_process``
    - ``process_identify_bottlenecks``
    - ``process_score_automation_opportunities``
    - ``exception_investigate_workflow``
    - ``exception_recommend_resolution``
    - ``exception_escalate_case``
    - ``decision_analyze_kpi_performance``
    - ``decision_explain_kpi_deviation``
    - ``decision_generate_brief``

    Returns:
        Configured FastMCP server instance.
    """
    mcp = FastMCP(
        name="workflow-intelligence",
        version="0.1.0",
        instructions=(
            "Workflow Intelligence MCP server. Provides process discovery, "
            "workflow exception resolution, and business decision intelligence "
            "tools backed by deterministic analytics."
        ),
        lifespan=_lifespan,
    )

    process_service = ProcessService()
    exception_service = ExceptionService()
    analytics = AnalyticsService()
    decision_service = DecisionService(analytics=analytics)

    register_process_tools(mcp, process_service)
    register_exception_tools(mcp, exception_service)
    register_decision_tools(mcp, decision_service)

    logger.info("Registered 9 domain tools on FastMCP server 'workflow-intelligence'")
    return mcp


def _build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser for the MCP server."""
    settings = get_settings()
    parser = argparse.ArgumentParser(
        prog="workflow-intelligence-mcp",
        description="Workflow Intelligence MCP server.",
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse"],
        default=settings.server_transport,
        help="Transport protocol (default: stdio).",
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

    mcp = create_server()

    if args.transport == "stdio":
        mcp.run(transport="stdio")
    else:
        mcp.run(transport="sse", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
