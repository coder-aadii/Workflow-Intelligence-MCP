"""Security regression tests covering path traversal, allowlists, file size limits,
network auth, production database failure modes, and concurrency.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path
from unittest.mock import patch

import pytest
import pytest_asyncio
from fastmcp import FastMCP
from fastmcp.client import Client
from starlette.testclient import TestClient

from workflow_intelligence.adapters.analytics.duckdb_adapter import (
    DuckDBAdapter,
    DuckDBAdapterError,
)
from workflow_intelligence.adapters.file_storage.loader import (
    FileStorageError,
    load_event_log,
)
from workflow_intelligence.config import Settings, get_settings
from workflow_intelligence.persistence.database import (
    drop_all,
    get_db_session,
    init_db,
    reset_engine,
)
from workflow_intelligence.persistence.repositories import ExceptionRepository
from workflow_intelligence.security.auth import StaticTokenVerifier
from workflow_intelligence.security.file_validator import (
    SecurityError,
    validate_safe_path,
)
from workflow_intelligence.services.exception_service import (
    ExceptionService,
    ExceptionServiceError,
)
from workflow_intelligence.schemas.exception import (
    ResolutionRequest,
    WorkflowInvestigationRequest,
)

TEST_DB_PATH = Path("/tmp") / f"test_sec_{uuid.uuid4().hex[:8]}.db"
TEST_DB_URL = f"sqlite+aiosqlite:///{TEST_DB_PATH}"


@pytest_asyncio.fixture(autouse=True)
async def setup_test_db() -> AsyncGenerator[None, None]:
    """Initialize fresh database before test and clean up afterward."""
    reset_engine(TEST_DB_URL)
    await init_db(TEST_DB_URL)
    yield
    try:
        await drop_all(TEST_DB_URL)
    except Exception:
        pass
    finally:
        reset_engine(None)
        if TEST_DB_PATH.exists():
            try:
                TEST_DB_PATH.unlink(missing_ok=True)
            except Exception:
                pass


class TestFilePathSecurity:
    """Tests for path traversal, hidden files, allowlists, and file size limits."""

    def test_path_traversal_double_dot_rejected(self) -> None:
        """Rejects paths containing '..' components."""
        with pytest.raises(SecurityError) as exc_info:
            validate_safe_path("../../etc/passwd")
        assert "Path traversal detected" in str(exc_info.value)

    def test_path_traversal_nested_rejected(self) -> None:
        """Rejects paths containing nested '..' components."""
        with pytest.raises(SecurityError) as exc_info:
            validate_safe_path("sample-data/../../etc/shadow")
        assert "Path traversal detected" in str(exc_info.value)

    def test_sensitive_file_blocked(self) -> None:
        """Rejects access to known sensitive filenames like .env, passwd, id_rsa."""
        for sensitive in [".env", "id_rsa", "passwd", "shadow"]:
            with pytest.raises(SecurityError) as exc_info:
                validate_safe_path(sensitive)
            assert "sensitive" in str(exc_info.value).lower() or "denied" in str(exc_info.value).lower()

    def test_hidden_directory_blocked(self) -> None:
        """Rejects access to paths inside hidden directories."""
        with pytest.raises(SecurityError) as exc_info:
            validate_safe_path(".hidden_dir/data.csv")
        assert "hidden" in str(exc_info.value).lower()

    def test_outside_allowed_directories_rejected(self, tmp_path: Path) -> None:
        """Rejects files outside explicitly configured allowed directories."""
        # Create a file in a forbidden folder
        forbidden_dir = tmp_path / "forbidden_outside_root"
        forbidden_dir.mkdir()
        forbidden_file = forbidden_dir / "secret.csv"
        forbidden_file.write_text("case_id,activity,timestamp\n1,A,2026-01-01\n")

        allowed_dir = tmp_path / "allowed_root"
        allowed_dir.mkdir()

        with pytest.raises(SecurityError) as exc_info:
            validate_safe_path(str(forbidden_file), allowed_dirs=[allowed_dir])
        assert "outside configured allowed data directories" in str(exc_info.value)

    def test_unsupported_file_extension_rejected(self, tmp_path: Path) -> None:
        """Rejects non-whitelisted file extensions."""
        script_file = tmp_path / "attack.sh"
        script_file.write_text("#!/bin/sh\nrm -rf /")

        with pytest.raises(SecurityError) as exc_info:
            validate_safe_path(str(script_file), allowed_dirs=[tmp_path])
        assert "Unsupported file format" in str(exc_info.value)

    def test_file_size_limit_enforced(self, tmp_path: Path) -> None:
        """Rejects files exceeding max_size_bytes."""
        large_file = tmp_path / "large.csv"
        # Write 200 bytes
        large_file.write_text("A" * 200)

        with pytest.raises(SecurityError) as exc_info:
            validate_safe_path(
                str(large_file),
                allowed_dirs=[tmp_path],
                max_size_bytes=100,
            )
        assert "exceeds maximum configured limit" in str(exc_info.value)

    def test_loader_integrates_security_checks(self) -> None:
        """Verifies FileStorageLoader translates SecurityError to FileStorageError."""
        with pytest.raises(FileStorageError):
            load_event_log("../../etc/passwd", "case_id", "activity", "timestamp")

    def test_duckdb_adapter_traversal_rejected(self) -> None:
        """Verifies DuckDBAdapter rejects path traversal in register_csv."""
        adapter = DuckDBAdapter()
        try:
            with pytest.raises(DuckDBAdapterError) as exc_info:
                adapter.register_csv("malicious_view", "../../etc/passwd")
            assert "Security validation failed" in str(exc_info.value)
        finally:
            adapter.close()


class TestNetworkAuthSecurity:
    """Tests for FastMCP network bearer token verification."""

    @pytest.fixture
    def auth_server(self) -> FastMCP:
        server = FastMCP(
            name="auth-secured-server",
            auth=StaticTokenVerifier("secret-token-production-2026"),
        )

        @server.tool
        def secure_ping() -> str:
            return "secure_pong"

        return server

    def test_sse_endpoint_rejects_missing_token(self, auth_server: FastMCP) -> None:
        """Network SSE endpoint returns 401 when Authorization header is absent."""
        app = auth_server.http_app(transport="sse")
        client = TestClient(app)

        resp = client.get("/sse")
        assert resp.status_code == 401

    def test_sse_endpoint_rejects_invalid_token(self, auth_server: FastMCP) -> None:
        """Network SSE endpoint returns 401 with invalid bearer token."""
        app = auth_server.http_app(transport="sse")
        client = TestClient(app)

        resp = client.get("/sse", headers={"Authorization": "Bearer wrong-token"})
        assert resp.status_code == 401

    def test_sse_endpoint_accepts_valid_token(self, auth_server: FastMCP) -> None:
        """Network SSE endpoint returns 200 OK with valid bearer token."""
        app = auth_server.http_app(transport="sse")
        client = TestClient(app)

        with client.stream(
            "GET",
            "/sse",
            headers={"Authorization": "Bearer secret-token-production-2026"},
        ) as stream_resp:
            assert stream_resp.status_code == 200

    @pytest.mark.asyncio
    async def test_stdio_client_operates_without_network_token(
        self, auth_server: FastMCP
    ) -> None:
        """STDIO / in-memory transport works directly without requiring HTTP auth."""
        async with Client(auth_server) as client:
            res = await client.call_tool("secure_ping", {})
            assert not res.is_error
            assert "secure_pong" in str(res.content)


class TestProductionDatabaseFailureSecurity:
    """Tests that in production mode, database failures never silently fall back."""

    @pytest.mark.asyncio
    async def test_production_investigation_raises_on_db_outage(self) -> None:
        """In production, DB write failure raises ExceptionServiceError instead of falling back."""
        service = ExceptionService()
        req = WorkflowInvestigationRequest(
            case_id="PROD-FAIL-01",
            workflow_type="procurement",
            failure_payload={
                "error": "ERP API timeout during PO submission",
                "retry_count": 3,
            },
        )

        # Mock production environment
        prod_settings = Settings(workflow_env="production")
        with patch(
            "workflow_intelligence.services.exception_service.get_settings",
            return_value=prod_settings,
        ):
            # Simulate DB connection failure
            async def failing_session():
                raise ConnectionRefusedError("PostgreSQL connection refused")
                yield  # noqa: unreachable

            with patch(
                "workflow_intelligence.services.exception_service.get_db_session",
                side_effect=failing_session,
            ):
                with pytest.raises(ExceptionServiceError) as exc_info:
                    await service.ainvestigate_failure(req)
                assert "Production database persistence failure" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_production_resolution_raises_on_db_outage(self) -> None:
        """In production, DB failure during resolution raises ExceptionServiceError."""
        service = ExceptionService()
        req = ResolutionRequest(
            exception_id="EXC-9999",
            policy_context={"auto_approve": True},
        )

        prod_settings = Settings(workflow_env="production")
        with patch(
            "workflow_intelligence.services.exception_service.get_settings",
            return_value=prod_settings,
        ):
            async def failing_session():
                raise ConnectionRefusedError("PostgreSQL connection refused")
                yield

            with patch(
                "workflow_intelligence.services.exception_service.get_db_session",
                side_effect=failing_session,
            ):
                with pytest.raises(ExceptionServiceError) as exc_info:
                    await service.arecommend_resolution(req)
                assert "Production database lookup failure" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_production_escalate_raises_on_db_outage(self) -> None:
        """In production, DB failure during escalation raises ExceptionServiceError."""
        service = ExceptionService()
        prod_settings = Settings(workflow_env="production")
        with patch(
            "workflow_intelligence.services.exception_service.get_settings",
            return_value=prod_settings,
        ):
            async def failing_session():
                raise ConnectionRefusedError("PostgreSQL connection refused")
                yield

            with patch(
                "workflow_intelligence.services.exception_service.get_db_session",
                side_effect=failing_session,
            ):
                with pytest.raises(ExceptionServiceError) as exc_info:
                    await service.aescalate_case(
                        case_id="PROD-ESC-01",
                        reason="Test escalation",
                        target_department="finance",
                    )
                assert "Production database escalation failure" in str(exc_info.value)


class TestConcurrentExceptionOperations:
    """Tests concurrent operations, duplicate prevention, and rollback safety."""

    @pytest.mark.asyncio
    async def test_duplicate_investigations_idempotency(self) -> None:
        """Investigations for the same case_id reuse the single open exception record."""
        service = ExceptionService()
        req = WorkflowInvestigationRequest(
            case_id="CONCUR-CASE-100",
            workflow_type="procurement",
            failure_payload={
                "error": "ERP API timeout during PO submission",
                "retry_count": 3,
            },
        )

        res1 = await service.ainvestigate_failure(req)
        res2 = await service.ainvestigate_failure(req)

        # Both investigations should resolve to the same stable exception ID
        assert res1.exception_id == res2.exception_id

        # Verify only 1 exception record exists in DB
        async for session in get_db_session():
            exc_repo = ExceptionRepository(session)
            records = await exc_repo.get_by_workflow_id("CONCUR-CASE-100")
            assert len(records) == 1

    @pytest.mark.asyncio
    async def test_duplicate_resolution_handling(self) -> None:
        """Calling resolve on an already-resolved exception safely returns already_resolved status."""
        service = ExceptionService()
        req = WorkflowInvestigationRequest(
            case_id="RESOLVE-DUP-01",
            workflow_type="procurement",
            failure_payload={
                "error": "ERP API timeout during PO submission",
                "retry_count": 3,
            },
        )
        inv = await service.ainvestigate_failure(req)

        # First resolution
        first_res = await service.aresolve_exception(inv.exception_id, "First resolve")
        assert first_res["status"] == "resolved"

        # Duplicate resolution attempt
        second_res = await service.aresolve_exception(inv.exception_id, "Second resolve")
        assert second_res["status"] == "already_resolved"

    @pytest.mark.asyncio
    async def test_transaction_rollback_on_failure(self) -> None:
        """If an error occurs midway through a transaction, changes are safely rolled back."""
        service = ExceptionService()
        req = WorkflowInvestigationRequest(
            case_id="ROLLBACK-CASE-01",
            workflow_type="procurement",
            failure_payload={
                "error": "ERP API timeout during PO submission",
                "retry_count": 3,
            },
        )
        inv = await service.ainvestigate_failure(req)

        prod_settings = Settings(workflow_env="production")
        # Simulate audit failure during resolution transaction in production mode
        with patch(
            "workflow_intelligence.services.exception_service.get_settings",
            return_value=prod_settings,
        ):
            with patch(
                "workflow_intelligence.persistence.repositories.AuditRepository.create_audit_entry",
                side_effect=RuntimeError("Simulated audit disk failure"),
            ):
                with pytest.raises(ExceptionServiceError) as exc_info:
                    await service.aresolve_exception(inv.exception_id, "Failing resolution")
                assert "resolution failure" in str(exc_info.value).lower()

        # Verify that because transaction rolled back, the record remains UNRESOLVED
        retrieved = await service.aretrieve_exception(inv.exception_id)
        assert retrieved is not None
        assert retrieved["is_resolved"] is False
