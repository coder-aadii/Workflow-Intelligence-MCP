"""Integration tests for Phase 4: Streamable HTTP, SSE, STDIO, authentication,
reliability, execution timeouts, and production startup guards.
"""

from __future__ import annotations

import argparse
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

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
    """Tests verifying that unauthenticated network exposure and default passwords are prevented in production."""

    def test_production_network_startup_fails_without_auth(self) -> None:
        """In production, network transport fails startup if MCP_AUTH_TOKEN is absent."""
        prod_settings = Settings(
            workflow_env="production",
            server_transport="streamable-http",
            mcp_auth_token=None,
            trusted_auth_gateway=False,
            database_url="postgresql+asyncpg://app_user:secure_pass_123@localhost:5432/workflow_intelligence",
        )

        with patch("workflow_intelligence.server.get_settings", return_value=prod_settings):
            with patch("argparse.ArgumentParser.parse_args") as mock_args:
                mock_args.return_value = argparse.Namespace(
                    transport="streamable-http", host="127.0.0.1", port=8000
                )
                with pytest.raises(RuntimeError) as exc_info:
                    main()
                assert "Production network startup aborted" in str(exc_info.value)

    def test_production_network_startup_fails_even_with_gateway_flag_if_token_missing(self) -> None:
        """A gateway flag alone cannot be treated as proof of authentication without token enforcement."""
        prod_settings = Settings(
            workflow_env="production",
            server_transport="streamable-http",
            mcp_auth_token=None,
            trusted_auth_gateway=True,
            database_url="postgresql+asyncpg://app_user:secure_pass_123@localhost:5432/workflow_intelligence",
        )

        with patch("workflow_intelligence.server.get_settings", return_value=prod_settings):
            with patch("argparse.ArgumentParser.parse_args") as mock_args:
                mock_args.return_value = argparse.Namespace(
                    transport="streamable-http", host="127.0.0.1", port=8000
                )
                with pytest.raises(RuntimeError) as exc_info:
                    main()
                assert "A gateway flag alone cannot be treated as proof of authentication" in str(exc_info.value)

    def test_production_startup_fails_with_default_postgres_password(self) -> None:
        """In production, default 'postgres:postgres' database credentials fail startup."""
        prod_settings = Settings(
            workflow_env="production",
            server_transport="stdio",
            mcp_auth_token=None,
            database_url="postgresql+asyncpg://postgres:postgres@localhost:5432/workflow_intelligence",
        )

        with patch("workflow_intelligence.server.get_settings", return_value=prod_settings):
            with patch("argparse.ArgumentParser.parse_args") as mock_args:
                mock_args.return_value = argparse.Namespace(
                    transport="stdio", host="127.0.0.1", port=8000
                )
                with pytest.raises(RuntimeError) as exc_info:
                    main()
                assert "default or trivial database credentials" in str(exc_info.value)

    def test_production_network_startup_succeeds_with_valid_token_and_secure_db(self) -> None:
        """In production, configuring a valid token and secure credentials allows network startup."""
        prod_settings = Settings(
            workflow_env="production",
            server_transport="streamable-http",
            mcp_auth_token="secure-production-token-12345",
            trusted_auth_gateway=False,
            database_url="postgresql+asyncpg://app_user:strong_prod_pass_987@localhost:5432/workflow_intelligence",
        )

        with patch("workflow_intelligence.server.get_settings", return_value=prod_settings):
            with patch("argparse.ArgumentParser.parse_args") as mock_args:
                mock_args.return_value = argparse.Namespace(
                    transport="streamable-http", host="127.0.0.1", port=8000
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
            database_url="postgresql+asyncpg://app_user:strong_prod_pass_987@localhost:5432/workflow_intelligence",
        )

        with patch("workflow_intelligence.server.get_settings", return_value=prod_settings):
            with patch("argparse.ArgumentParser.parse_args") as mock_args:
                mock_args.return_value = argparse.Namespace(
                    transport="stdio", host="127.0.0.1", port=8000
                )
                with patch("fastmcp.FastMCP.run") as mock_run:
                    main()
                    assert mock_run.called


class TestDatabaseReadinessBehavior:
    """Tests verifying database readiness probe behavior across production and non-production modes."""

    def test_production_lifespan_fails_when_init_db_fails(self) -> None:
        """In production, server lifespan raises RuntimeError if init_db fails."""
        prod_settings = Settings(
            workflow_env="production",
            mcp_auth_token="valid-secret-token",
            database_url="postgresql+asyncpg://app_user:strong_prod_pass_987@localhost:5432/workflow_intelligence",
        )
        with patch("workflow_intelligence.server.get_settings", return_value=prod_settings):
            mcp = create_server()
            app = create_unified_http_app(mcp)
            with patch(
                "workflow_intelligence.persistence.database.init_db",
                AsyncMock(side_effect=ConnectionRefusedError("PostgreSQL connection refused")),
            ):
                with pytest.raises(RuntimeError) as exc_info:
                    with TestClient(app):
                        pass
                assert "Production database initialization failed" in str(exc_info.value)

    def test_production_readiness_fails_503_when_database_uninitialized(self) -> None:
        """In production, /ready returns 503 if database initialization has not completed."""
        prod_settings = Settings(
            workflow_env="production",
            mcp_auth_token="valid-secret-token",
            database_url="postgresql+asyncpg://app_user:strong_prod_pass_987@localhost:5432/workflow_intelligence",
        )
        with patch("workflow_intelligence.server.get_settings", return_value=prod_settings):
            mcp = create_server()
            app = create_unified_http_app(mcp)
            with patch("workflow_intelligence.persistence.database.init_db", AsyncMock()):
                with patch("workflow_intelligence.persistence.database.is_db_initialized", return_value=False):
                    with patch("workflow_intelligence.persistence.database.check_db_health", AsyncMock(return_value=False)):
                        with TestClient(app) as client:
                            r = client.get("/ready")
                            assert r.status_code == 503
                            assert r.json()["status"] == "not_ready"
                            assert "Database not initialized" in r.json()["error"]

    def test_production_readiness_fails_503_when_database_unhealthy(self) -> None:
        """In production, /ready returns 503 if database health check fails."""
        prod_settings = Settings(
            workflow_env="production",
            mcp_auth_token="valid-secret-token",
            database_url="postgresql+asyncpg://app_user:strong_prod_pass_987@localhost:5432/workflow_intelligence",
        )
        with patch("workflow_intelligence.server.get_settings", return_value=prod_settings):
            mcp = create_server()
            app = create_unified_http_app(mcp)
            with patch("workflow_intelligence.persistence.database.init_db", AsyncMock()):
                with patch("workflow_intelligence.persistence.database.is_db_initialized", return_value=True):
                    with patch("workflow_intelligence.persistence.database.check_db_health", AsyncMock(return_value=False)):
                        with TestClient(app) as client:
                            r = client.get("/ready")
                            assert r.status_code == 503
                            assert r.json()["status"] == "not_ready"
                            assert "Database connectivity check failed" in r.json()["error"]

    def test_production_readiness_succeeds_200_when_database_ready(self) -> None:
        """In production, /ready returns 200 when database is initialized and healthy."""
        prod_settings = Settings(
            workflow_env="production",
            mcp_auth_token="valid-secret-token",
            database_url="postgresql+asyncpg://app_user:strong_prod_pass_987@localhost:5432/workflow_intelligence",
        )
        with patch("workflow_intelligence.server.get_settings", return_value=prod_settings):
            mcp = create_server()
            app = create_unified_http_app(mcp)
            with patch("workflow_intelligence.persistence.database.init_db", AsyncMock()):
                with patch("workflow_intelligence.persistence.database.is_db_initialized", return_value=True):
                    with patch("workflow_intelligence.persistence.database.check_db_health", AsyncMock(return_value=True)):
                        with TestClient(app) as client:
                            r = client.get("/ready")
                            assert r.status_code == 200
                            assert r.json()["status"] == "ready"
                            assert r.json()["database_healthy"] is True

    def test_development_readiness_probe_allows_offline_demo_mode(self) -> None:
        """In development/demo mode, /ready returns 200 even if database is offline."""
        dev_settings = Settings(workflow_env="development")
        with patch("workflow_intelligence.server.get_settings", return_value=dev_settings):
            mcp = create_server()
            app = create_unified_http_app(mcp)
            with patch("workflow_intelligence.persistence.database.is_db_initialized", return_value=False):
                with patch("workflow_intelligence.persistence.database.check_db_health", AsyncMock(return_value=False)):
                    with TestClient(app) as client:
                        r = client.get("/ready")
                        assert r.status_code == 200
                        assert r.json()["status"] == "ready"
                        assert r.json()["database_healthy"] is False


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


class TestPlaceholderCredentialRejection:
    """Tests verifying template placeholders cannot be used as production credentials."""

    @pytest.mark.parametrize(
        "placeholder_token",
        [
            "replace_with_strong_random_token_minimum_32_chars",
            "changeme_token_12345",
            "placeholder_auth_token",
            "your_token_here_1234",
            "short",
        ],
    )
    def test_production_network_startup_fails_with_placeholder_token(
        self, placeholder_token: str
    ) -> None:
        """In production, unchanged template tokens are rejected at startup."""
        prod_settings = Settings(
            workflow_env="production",
            mcp_auth_token=placeholder_token,
            database_url="postgresql+asyncpg://app_user:strong_prod_pass_987@localhost:5432/workflow_intelligence",
        )
        with patch("workflow_intelligence.server.get_settings", return_value=prod_settings):
            with patch("sys.argv", ["server", "--transport", "streamable-http"]):
                with pytest.raises(RuntimeError) as exc_info:
                    main()
                assert "MCP_AUTH_TOKEN" in str(exc_info.value)

    @pytest.mark.parametrize(
        "placeholder_pw",
        [
            "replace_with_secure_production_password_minimum_16_chars",
            "changeme",
            "postgres",
            "password",
            "admin",
            "root",
            "short",
        ],
    )
    def test_production_startup_fails_with_placeholder_postgres_password(
        self, placeholder_pw: str
    ) -> None:
        """In production, default or template postgres passwords are rejected at startup."""
        prod_settings = Settings(
            workflow_env="production",
            mcp_auth_token="strong_production_token_minimum_32_chars_ok",
            postgres_password=placeholder_pw,
            database_url=f"postgresql+asyncpg://app_user:{placeholder_pw}@localhost:5432/workflow_intelligence",
        )
        with patch("workflow_intelligence.server.get_settings", return_value=prod_settings):
            with patch("sys.argv", ["server", "--transport", "stdio"]):
                with pytest.raises(RuntimeError) as exc_info:
                    main()
                assert "database credentials" in str(exc_info.value) or "POSTGRES_PASSWORD" in str(exc_info.value)


class TestDuckDBQueryTimeoutAndCancellation:
    """Tests verifying DuckDB query timeout and cancellation via QUERY_TIMEOUT_MS."""

    def test_duckdb_adapter_wires_query_timeout_ms_from_settings(self) -> None:
        """DuckDBAdapter initializes query_timeout_ms from settings by default."""
        custom_settings = Settings(query_timeout_ms=12345)
        with patch("workflow_intelligence.config.get_settings", return_value=custom_settings):
            from workflow_intelligence.adapters.analytics.duckdb_adapter import DuckDBAdapter

            adapter = DuckDBAdapter(":memory:")
            assert adapter._query_timeout_ms == 12345

    def test_duckdb_query_timeout_interrupts_execution_and_recovers(self) -> None:
        """Long-running queries are interrupted when exceeding timeout, and connection recovers."""
        from workflow_intelligence.adapters.analytics.duckdb_adapter import (
            DuckDBAdapter,
            DuckDBAdapterTimeoutError,
        )

        adapter = DuckDBAdapter(":memory:", query_timeout_ms=50)

        # Heavy recursive Cartesian query
        heavy_sql = "WITH RECURSIVE t(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM t) SELECT count(*) FROM t, t AS t2"
        with pytest.raises(DuckDBAdapterTimeoutError) as exc_info:
            adapter.execute_query(heavy_sql, time_limit_ms=50)

        assert "exceeded execution time limit" in str(exc_info.value)

        # Verify that subsequent query executes cleanly without deadlock or connection corruption
        res = adapter.execute_query("SELECT 42")
        assert res == [(42,)]
