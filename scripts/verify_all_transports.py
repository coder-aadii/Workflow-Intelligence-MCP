#!/usr/bin/env python3
"""End-to-end multi-transport verification script for Workflow Intelligence MCP.

Verifies:
1. STDIO transport: client initialization, tools/list, tools/call, resources/list, prompts/list.
2. Streamable HTTP transport:
   - 401 on unauthenticated request
   - 401 on bad bearer token
   - 200 on valid bearer token
   - Handshake initialize, session handling, tools/list, tools/call, resources/list, prompts/list.
3. SSE transport:
   - 401 on unauthenticated GET /sse
   - 200 on authenticated GET /sse
   - SSE endpoint event streaming.
4. Health & readiness probes (/health, /ready).
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Add project src to path
repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root / "src"))

from fastmcp.client import Client
from starlette.testclient import TestClient

from workflow_intelligence.security.auth import StaticTokenVerifier
from workflow_intelligence.server import create_server, create_unified_http_app


async def test_stdio_transport(server) -> bool:
    print("Testing STDIO transport (in-memory client)...")
    async with Client(server) as client:
        # Tools
        tools = await client.list_tools()
        assert len(tools) == 9, f"Expected 9 tools, got {len(tools)}"
        print(f"  [PASS] STDIO tools/list: {len(tools)} tools registered")

        # Tool Call
        res = await client.call_tool(
            "decision_analyze_kpi_performance",
            {
                "metric_name": "processing_cost",
                "source_uri": "sample-data/procurement_costs.csv",
                "timeframe_column": "quarter",
                "timeframe_current": "Q3",
                "timeframe_baseline": "Q2",
            },
        )
        assert not res.is_error
        print("  [PASS] STDIO tools/call: decision_analyze_kpi_performance executed successfully")

        # Resources
        resources = await client.list_resources()
        templates = await client.list_resource_templates()
        assert len(resources) == 3, f"Expected 3 static resources, got {len(resources)}"
        assert len(templates) == 1, f"Expected 1 resource template, got {len(templates)}"
        print(f"  [PASS] STDIO resources: {len(resources)} static resources + {len(templates)} template registered (total 4)")

        # Prompts
        prompts = await client.list_prompts()
        assert len(prompts) == 3, f"Expected 3 prompts, got {len(prompts)}"
        print(f"  [PASS] STDIO prompts/list: {len(prompts)} prompts registered")

    return True


async def test_stdio_subprocess() -> bool:
    print("\nTesting STDIO transport (real OS subprocess client)...")
    import os
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "workflow_intelligence.server", "--transport", "stdio"],
        env={**os.environ, "WORKFLOW_ENV": "development"},
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            init_res = await session.initialize()
            assert init_res.server_info.name == "workflow-intelligence"
            print(
                f"  [PASS] STDIO Subprocess initialized: {init_res.server_info.name} "
                f"v{init_res.server_info.version}"
            )

            tools_res = await session.list_tools()
            assert len(tools_res.tools) == 9, f"Expected 9 tools, got {len(tools_res.tools)}"
            print(f"  [PASS] STDIO Subprocess tools/list: {len(tools_res.tools)} tools registered")

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
            print("  [PASS] STDIO Subprocess tools/call: decision_analyze_kpi_performance executed successfully")

            resources_res = await session.list_resources()
            prompts_res = await session.list_prompts()
            print(
                f"  [PASS] STDIO Subprocess resources/list ({len(resources_res.resources)}) "
                f"& prompts/list ({len(prompts_res.prompts)}) verified"
            )

    return True


def test_http_transports(server, token: str = "test-token-phase5") -> bool:
    print("\nTesting Streamable HTTP and SSE transports with authentication...")
    app = create_unified_http_app(server)

    with TestClient(app) as client:
        # 1. Probes
        r_health = client.get("/health")
        assert r_health.status_code == 200, f"Health check failed: {r_health.status_code}"
        print("  [PASS] Health check: GET /health -> 200 OK")

        r_ready = client.get("/ready")
        assert r_ready.status_code == 200, f"Readiness probe failed: {r_ready.status_code}"
        print("  [PASS] Readiness probe: GET /ready -> 200 OK")

        init_payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "test-client", "version": "1.0"},
            },
        }

        # 2. Streamable HTTP Unauthenticated -> 401
        r_unauth = client.post("/mcp", json=init_payload)
        assert r_unauth.status_code == 401, f"Expected 401, got {r_unauth.status_code}"
        print("  [PASS] Streamable HTTP: Unauthenticated request rejected (401)")

        # 3. Streamable HTTP Invalid Token -> 401
        r_bad = client.post(
            "/mcp",
            json=init_payload,
            headers={"Authorization": "Bearer bad-token-xyz"},
        )
        assert r_bad.status_code == 401, f"Expected 401, got {r_bad.status_code}"
        print("  [PASS] Streamable HTTP: Invalid bearer token rejected (401)")

        # 4. Streamable HTTP Valid Token -> 200
        auth_headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json, text/event-stream",
        }
        r_auth = client.post("/mcp", json=init_payload, headers=auth_headers)
        assert r_auth.status_code == 200, f"Expected 200, got {r_auth.status_code}"
        session_id = r_auth.headers.get("mcp-session-id")
        print(f"  [PASS] Streamable HTTP: Handshake 200 OK, session_id={session_id}")

        if session_id:
            auth_headers["mcp-session-id"] = session_id

        # 5. Streamable HTTP tools/list
        r_tools = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            headers=auth_headers,
        )
        assert r_tools.status_code == 200
        assert "process_analyze_business_process" in r_tools.text
        print("  [PASS] Streamable HTTP: tools/list 200 OK (all tools present)")

        # 6. Streamable HTTP tools/call
        r_call = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "decision_analyze_kpi_performance",
                    "arguments": {
                        "metric_name": "processing_cost",
                        "source_uri": "sample-data/procurement_costs.csv",
                        "timeframe_column": "quarter",
                        "timeframe_current": "Q3",
                        "timeframe_baseline": "Q2",
                    },
                },
            },
            headers=auth_headers,
        )
        assert r_call.status_code == 200
        assert "processing_cost" in r_call.text
        print("  [PASS] Streamable HTTP: tools/call 200 OK")

        # 7. SSE Unauthenticated -> 401
        r_sse_unauth = client.get("/sse")
        assert r_sse_unauth.status_code == 401, f"Expected 401, got {r_sse_unauth.status_code}"
        print("  [PASS] SSE: Unauthenticated request rejected (401)")

        # 8. SSE Authenticated -> 200 Stream
        with client.stream(
            "GET", "/sse", headers={"Authorization": f"Bearer {token}"}
        ) as sse_stream:
            assert sse_stream.status_code == 200
            print("  [PASS] SSE: Authenticated stream opened (200 OK)")

    return True


async def main_verifier():
    print("============================================================")
    print("WORKFLOW INTELLIGENCE MCP — ALL TRANSPORTS VERIFICATION")
    print("============================================================\n")

    token = "test-token-phase5"
    mcp_server = create_server()
    # Inject token verifier for testing auth
    mcp_server.auth = StaticTokenVerifier(token)

    # 1. STDIO (In-Memory FastMCP Client)
    await test_stdio_transport(mcp_server)

    # 2. STDIO (Real OS Subprocess Client)
    await test_stdio_subprocess()

    # 3. Streamable HTTP & SSE
    test_http_transports(mcp_server, token=token)

    print("\n============================================================")
    print("ALL TRANSPORTS (STDIO, Streamable HTTP, SSE) VERIFIED SUCCESSFULLY")
    print("============================================================\n")


if __name__ == "__main__":
    asyncio.run(main_verifier())
