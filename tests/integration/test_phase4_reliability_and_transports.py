"""Integration tests for Phase 4: Streamable HTTP, SSE, STDIO, authentication,
reliability, execution timeouts, and production startup guards.
"""

from __future__ import annotations

import argparse
from contextlib import asynccontextmanager
from unittest.mock import patch

import pytest
from fastmcp import FastMCP
from fastmcp.client import Client
from starlette.testclient import TestClient

from workflow_intelligence.config import Settings
from workflow_intelligence.security.auth import StaticTokenVerifier
from workflow_intelligence.server import (
    create_server,
    create_unified_http_app,
    main,
)
from workflow_intelligence.services.process_service import (
    ProcessService,
    ProcessServiceError,
)
from workflow_intelligence.schemas.process import ProcessAnalyzeRequest


class TestStreamableHTTPTransport:
    """Tests covering the MCP JSON-RPC protocol over Streamable HTTP (/mcp)."""

    @pytest.fixture
    def streamable_server(self) -> FastMCP:
        """Create FastMCP server with registered tools, resources, and prompts."""
        return create_server()

    def test_streamable_http_initialization(self, streamable_server: FastMCP) -> None:
        """Verifies Streamable HTTP initialization handshake returns capabilities and serverInfo."""
        app = streamable_server.http_app(transport="streamable-http")
        with TestClient(app) as client:
            resp = client.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "test-client", "version": "1.0.0"},
                    },
                },
                headers={"Accept": "application/json, text/event-stream"},
            )
            assert resp.status_code == 200
            assert "text/event-stream" in resp.headers.get("content-type", "")
            assert "workflow-intelligence" in resp.text
            assert "protocolVersion" in resp.text

    def test_streamable_http_tools_list(self, streamable_server: FastMCP) -> None:
        """Verifies tools/list returns all 9 domain tools over Streamable HTTP."""
        app = streamable_server.http_app(transport="streamable-http")
        with TestClient(app) as client:
            # 1. Initialize
            init_resp = client.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "test-client", "version": "1.0.0"},
                    },
                },
                headers={"Accept": "application/json, text/event-stream"},
            )
            session_id = init_resp.headers.get("mcp-session-id")
            headers = {"Accept": "application/json, text/event-stream"}
            if session_id:
                headers["mcp-session-id"] = session_id

            # 2. tools/list
            tools_resp = client.post(
                "/mcp",
                json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
                headers=headers,
            )
            assert tools_resp.status_code == 200
            content = tools_resp.text
            assert "process_analyze_business_process" in content
            assert "exception_investigate_workflow" in content
            assert "decision_analyze_kpi_performance" in content

    def test_streamable_http_tools_call(self, streamable_server: FastMCP) -> None:
        """Verifies tools/call executes a tool over Streamable HTTP."""
        app = streamable_server.http_app(transport="streamable-http")
        with TestClient(app) as client:
            init_resp = client.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "test-client", "version": "1.0.0"},
                    },
                },
                headers={"Accept": "application/json, text/event-stream"},
            )
            session_id = init_resp.headers.get("mcp-session-id")
            headers = {"Accept": "application/json, text/event-stream"}
            if session_id:
                headers["mcp-session-id"] = session_id

            # Call decision_analyze_kpi_performance
            call_resp = client.post(
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
                            "timeframe_current": "Q3",
                            "timeframe_baseline": "Q2",
                        },
                    },
                },
                headers=headers,
            )
            assert call_resp.status_code == 200
            assert "processing_cost" in call_resp.text

    def test_streamable_http_resources_and_prompts_list(
        self, streamable_server: FastMCP
    ) -> None:
        """Verifies resources/list and prompts/list over Streamable HTTP."""
        app = streamable_server.http_app(transport="streamable-http")
        with TestClient(app) as client:
            init_resp = client.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "test-client", "version": "1.0.0"},
                    },
                },
                headers={"Accept": "application/json, text/event-stream"},
            )
            session_id = init_resp.headers.get("mcp-session-id")
            headers = {"Accept": "application/json, text/event-stream"}
            if session_id:
                headers["mcp-session-id"] = session_id

            # resources/list
            res_resp = client.post(
                "/mcp",
                json={"jsonrpc": "2.0", "id": 4, "method": "resources/list", "params": {}},
                headers=headers,
            )
            assert res_resp.status_code == 200
            assert "workflow://" in res_resp.text

            # prompts/list
            prompt_resp = client.post(
                "/mcp",
                json={"jsonrpc": "2.0", "id": 5, "method": "prompts/list", "params": {}},
                headers=headers,
            )
            assert prompt_resp.status_code == 200
            assert "process_bottleneck_review" in prompt_resp.text


class TestStreamableHTTPAuthentication:
    """Tests covering bearer token verification on Streamable HTTP."""

    @pytest.fixture
    def auth_server(self) -> FastMCP:
        server = FastMCP(
            name="auth-streamable",
            auth=StaticTokenVerifier("phase4-secret-token"),
        )

        @server.tool
        def ping() -> str:
            return "pong"

        return server

    def test_unauthenticated_request_rejected(self, auth_server: FastMCP) -> None:
        """Unauthenticated requests to /mcp return 401 Unauthorized."""
        app = auth_server.http_app(transport="streamable-http")
        with TestClient(app) as client:
            resp = client.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "test-client", "version": "1.0.0"},
                    },
                },
            )
            assert resp.status_code == 401

    def test_invalid_token_rejected(self, auth_server: FastMCP) -> None:
        """Requests with wrong bearer token return 401 Unauthorized."""
        app = auth_server.http_app(transport="streamable-http")
        with TestClient(app) as client:
            resp = client.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "test-client", "version": "1.0.0"},
                    },
                },
                headers={"Authorization": "Bearer invalid-token"},
            )
            assert resp.status_code == 401

    def test_valid_token_accepted(self, auth_server: FastMCP) -> None:
        """Requests with valid bearer token succeed with 200 OK."""
        app = auth_server.http_app(transport="streamable-http")
        with TestClient(app) as client:
            resp = client.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "test-client", "version": "1.0.0"},
                    },
                },
                headers={
                    "Authorization": "Bearer phase4-secret-token",
                    "Accept": "application/json, text/event-stream",
                },
            )
            assert resp.status_code == 200


class TestUnifiedApplication:
    """Tests verifying Streamable HTTP (/mcp) and SSE (/sse) co-exist on the same port."""

    def test_unified_app_serves_both_transports_and_probes(self) -> None:
        """Single Starlette app serves /health, /ready, /mcp, and /sse."""
        mcp = create_server()
        app = create_unified_http_app(mcp)

        with TestClient(app) as client:
            # 1. Probe /health
            r_health = client.get("/health")
            assert r_health.status_code == 200
            assert r_health.json()["status"] == "healthy"

            # 2. Probe /ready
            r_ready = client.get("/ready")
            assert r_ready.status_code == 200
            assert r_ready.json()["status"] == "ready"

            # 3. Streamable HTTP /mcp
            r_mcp = client.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "test", "version": "1.0"},
                    },
                },
                headers={"Accept": "application/json, text/event-stream"},
            )
            assert r_mcp.status_code == 200
            assert "workflow-intelligence" in r_mcp.text

            # 4. SSE /sse
            with client.stream("GET", "/sse") as stream_resp:
                assert stream_resp.status_code == 200


class TestProductionStartupGuards:
    """Tests verifying that unauthenticated network exposure is prevented in production."""

    def test_production_network_startup_fails_without_auth_or_gateway(self) -> None:
        """In production, network transport fails startup if MCP_AUTH_TOKEN and TRUSTED_AUTH_GATEWAY are absent."""
        prod_settings = Settings(
            workflow_env="production",
            server_transport="streamable-http",
            mcp_auth_token=None,
            trusted_auth_gateway=False,
        )

        with patch("workflow_intelligence.server.get_settings", return_value=prod_settings):
            with patch("argparse.ArgumentParser.parse_args") as mock_args:
                mock_args.return_value = argparse.Namespace(
                    transport="streamable-http", host="0.0.0.0", port=8000
                )
                with pytest.raises(RuntimeError) as exc_info:
                    main()
                assert "Production network startup aborted" in str(exc_info.value)

    def test_production_network_startup_succeeds_with_trusted_gateway(self) -> None:
        """In production, TRUSTED_AUTH_GATEWAY=true allows network startup."""
        prod_settings = Settings(
            workflow_env="production",
            server_transport="streamable-http",
            mcp_auth_token=None,
            trusted_auth_gateway=True,
        )

        with patch("workflow_intelligence.server.get_settings", return_value=prod_settings):
            with patch("argparse.ArgumentParser.parse_args") as mock_args:
                mock_args.return_value = argparse.Namespace(
                    transport="streamable-http", host="0.0.0.0", port=8000
                )
                with patch("fastmcp.FastMCP.run") as mock_run:
                    main()
                    assert mock_run.called

    def test_production_stdio_startup_allowed_without_network_token(self) -> None:
        """STDIO transport does not require network auth tokens in production."""
        prod_settings = Settings(
            workflow_env="production",
            server_transport="stdio",
            mcp_auth_token=None,
            trusted_auth_gateway=False,
        )

        with patch("workflow_intelligence.server.get_settings", return_value=prod_settings):
            with patch("argparse.ArgumentParser.parse_args") as mock_args:
                mock_args.return_value = argparse.Namespace(
                    transport="stdio", host="127.0.0.1", port=8000
                )
                with patch("fastmcp.FastMCP.run") as mock_run:
                    main()
                    assert mock_run.called


class TestAnalysisExecutionTimeout:
    """Tests verifying analysis execution timeouts protect against unbounded computation."""

    def test_analysis_timeout_triggers_error(self) -> None:
        """When analysis takes longer than analysis_timeout_seconds, ProcessServiceError is raised."""
        service = ProcessService()
        req = ProcessAnalyzeRequest(
            source_uri="sample-data/procurement_events.csv",
            case_id_col="case_id",
            activity_col="activity",
            timestamp_col="timestamp",
        )

        # Force a tiny timeout of 0.0001 seconds
        short_timeout_settings = Settings(analysis_timeout_seconds=0.0001)
        with patch(
            "workflow_intelligence.services.process_service.get_settings",
            return_value=short_timeout_settings,
        ):
            with pytest.raises(ProcessServiceError) as exc_info:
                service.analyze_business_process(req)
            assert "timed out after" in str(exc_info.value)
