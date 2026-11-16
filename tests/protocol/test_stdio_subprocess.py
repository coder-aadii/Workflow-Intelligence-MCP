"""Real subprocess-based STDIO transport tests for Workflow Intelligence MCP.

Spawns the MCP server as an independent OS subprocess via stdio transport,
communicating strictly over JSON-RPC stdin/stdout pipelines, validating
that no stray logging corrupts protocol framing.
"""

from __future__ import annotations

import os
import sys
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


@pytest.mark.asyncio
async def test_real_subprocess_stdio_handshake_and_tools() -> None:
    """Verify that a real OS subprocess server initializes and advertises 9 tools over stdio."""
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "workflow_intelligence.server", "--transport", "stdio"],
        env={**os.environ, "WORKFLOW_ENV": "development"},
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            init_res = await session.initialize()
            assert init_res.server_info.name == "workflow-intelligence"
            assert init_res.server_info.version == "1.0.1"

            tools_res = await session.list_tools()
            assert len(tools_res.tools) == 9
            tool_names = {t.name for t in tools_res.tools}
            assert "decision_analyze_kpi_performance" in tool_names
            assert "process_analyze_business_process" in tool_names
            assert "exception_investigate_workflow" in tool_names


@pytest.mark.asyncio
async def test_real_subprocess_stdio_tool_execution() -> None:
    """Verify that tool execution over a real subprocess stdio pipe succeeds."""
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "workflow_intelligence.server", "--transport", "stdio"],
        env={**os.environ, "WORKFLOW_ENV": "development"},
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            call_res = await session.call_tool(
                "decision_analyze_kpi_performance",
                {
                    "metric_name": "processing_cost",
                    "source_uri": "sample-data/procurement_costs.csv",
                    "timeframe_column": "quarter",
                    "timeframe_current": "Q3",
                    "timeframe_baseline": "Q2",
                },
            )
            assert not call_res.is_error
            assert call_res.content is not None
            assert len(call_res.content) > 0


@pytest.mark.asyncio
async def test_real_subprocess_stdio_resources_and_prompts() -> None:
    """Verify that resources and prompts can be enumerated over real subprocess stdio."""
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "workflow_intelligence.server", "--transport", "stdio"],
        env={**os.environ, "WORKFLOW_ENV": "development"},
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            resources_res = await session.list_resources()
            prompts_res = await session.list_prompts()

            assert len(resources_res.resources) >= 3
            assert len(prompts_res.prompts) == 3
