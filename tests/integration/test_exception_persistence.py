"""Integration tests for Persistent Exception Workflow lifecycle.

Validates:
1. End-to-end lifecycle: Investigate -> Persist -> Retrieve -> Recommend -> Resolve/Escalate -> Audit
2. Persistence survives service restarts
3. Concurrency & duplicate prevention (duplicate resolution, duplicate escalation)
4. Sensitive payload redaction in persisted records
5. Standalone fallback when database is unavailable
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from workflow_intelligence.persistence.database import (
    drop_all,
    get_db_session,
    init_db,
    reset_engine,
)
from workflow_intelligence.persistence.repositories import (
    AuditRepository,
    ExceptionRepository,
    WorkflowRepository,
)
from workflow_intelligence.schemas.common import SeverityLevel
from workflow_intelligence.schemas.exception import (
    ResolutionRequest,
    WorkflowInvestigationRequest,
)
from workflow_intelligence.services.exception_service import (
    ExceptionService,
    sanitize_payload,
)

TEST_DB_PATH = Path("/tmp") / f"test_exception_lifecycle_{uuid.uuid4().hex[:8]}.db"
TEST_DB_URL = f"sqlite+aiosqlite:///{TEST_DB_PATH}"


@pytest_asyncio.fixture(autouse=True)
async def setup_test_database() -> AsyncGenerator[None, None]:
    """Initialize fresh database before test and clean up afterward."""
    reset_engine(TEST_DB_URL)
    await init_db(TEST_DB_URL)
    yield
    try:
        await drop_all(TEST_DB_URL)
    finally:
        reset_engine(None)
        if TEST_DB_PATH.exists():
            TEST_DB_PATH.unlink(missing_ok=True)


class TestPersistentExceptionLifecycle:
    """Test full persistent exception lifecycle with database."""

    async def test_full_investigate_persist_resolve_audit_lifecycle(self) -> None:
        service = ExceptionService()

        # 1. Investigate & Persist
        req = WorkflowInvestigationRequest(
            case_id="PO-TEST-001",
            workflow_type="purchase_order_creation",
            failure_payload={
                "error": "vendor_name_mismatch",
                "vendor_name": "Acme Widgets Corp",
                "tax_id": "US-999999",
                "tax_id_match": True,
                "api_key": "super_secret_token",
            },
        )
        inv_result = await service.ainvestigate_failure(req)
        assert inv_result.case_id == "PO-TEST-001"
        assert inv_result.exception_id.startswith("EXC-")
        assert inv_result.category.value == "MASTER_DATA_DISCREPANCY"

        # 2. Retrieve from database
        persisted = await service.aretrieve_exception(inv_result.exception_id)
        assert persisted is not None
        assert persisted["id"] == inv_result.exception_id
        assert persisted["workflow_id"] == "PO-TEST-001"
        assert persisted["category"] == "MASTER_DATA_DISCREPANCY"
        assert persisted["is_resolved"] is False
        # Verify sensitive payload was redacted
        assert persisted["payload"]["api_key"] == "********"

        # 3. Recommend & Auto-Resolve
        res_req = ResolutionRequest(
            exception_id=inv_result.exception_id,
            policy_context={"auto_approve": True, "approval_tier": "manager"},
        )
        plan = await service.arecommend_resolution(res_req)
        assert plan.auto_resolve is True
        assert plan.exception_id == inv_result.exception_id

        # 4. Verify exception is marked resolved in database
        after_resolve = await service.aretrieve_exception(inv_result.exception_id)
        assert after_resolve is not None
        assert after_resolve["is_resolved"] is True
        assert after_resolve["resolved_at"] is not None

        # 5. Verify audit history was recorded in database
        async for session in get_db_session():
            audit_repo = AuditRepository(session)
            entries = await audit_repo.get_by_correlation_id("PO-TEST-001")
            actions = [e.action_name for e in entries]
            assert "exception_investigate_workflow" in actions
            assert "exception_resolve" in actions

    async def test_persistence_survives_service_restart(self) -> None:
        """Data created by Service instance 1 must be accessible by Service instance 2."""
        service_instance_1 = ExceptionService()

        req = WorkflowInvestigationRequest(
            case_id="PO-RESTART-002",
            workflow_type="invoice_matching",
            failure_payload={
                "error": "duplicate_sequence",
                "po_number": "PO-1002",
                "regenerate": True,
            },
        )
        res_1 = await service_instance_1.ainvestigate_failure(req)
        saved_exc_id = res_1.exception_id

        # Simulate service shutdown & fresh restart
        del service_instance_1
        service_instance_2 = ExceptionService()

        # Retrieve using service instance 2 from persistent database
        record = await service_instance_2.aretrieve_exception(saved_exc_id)
        assert record is not None
        assert record["id"] == saved_exc_id
        assert record["workflow_id"] == "PO-RESTART-002"
        assert record["category"] == "CONCURRENCY_CONFLICT"
        assert record["is_resolved"] is False

    async def test_prevent_duplicate_open_exceptions(self) -> None:
        """Investigating the same case again with an open exception reuses the exception ID."""
        service = ExceptionService()
        req = WorkflowInvestigationRequest(
            case_id="PO-DUP-003",
            workflow_type="purchase_order_creation",
            failure_payload={
                "error": "timeout",
                "api": "erp_endpoint",
                "retry": True,
            },
        )
        first_result = await service.ainvestigate_failure(req)
        second_result = await service.ainvestigate_failure(req)

        # Idempotent: must reuse existing open exception ID
        assert first_result.exception_id == second_result.exception_id

        # Database should only have 1 open exception record for this workflow
        async for session in get_db_session():
            exc_repo = ExceptionRepository(session)
            records = await exc_repo.get_by_workflow_id("PO-DUP-003")
            assert len(records) == 1

    async def test_prevent_duplicate_resolution(self) -> None:
        """Resolving an already resolved exception returns already_resolved status."""
        service = ExceptionService()
        req = WorkflowInvestigationRequest(
            case_id="PO-RESOLVE-004",
            workflow_type="purchase_order_creation",
            failure_payload={
                "error": "duplicate_po",
                "sequence": "conflict",
            },
        )
        inv = await service.ainvestigate_failure(req)

        # First resolution
        plan1 = await service.arecommend_resolution(
            ResolutionRequest(
                exception_id=inv.exception_id,
                policy_context={"auto_approve": True},
            )
        )
        assert plan1.auto_resolve is True

        # Second resolution attempt
        plan2 = await service.arecommend_resolution(
            ResolutionRequest(
                exception_id=inv.exception_id,
                policy_context={"auto_approve": True},
            )
        )
        assert plan2.auto_resolve is False
        assert "already been resolved" in plan2.remediation_action

    async def test_prevent_duplicate_escalation(self) -> None:
        """Escalating the same case to the same department reuses existing ticket ID."""
        service = ExceptionService()

        esc1 = await service.aescalate_case(
            case_id="PO-ESC-005",
            reason="Missing manager approval signature",
            target_department="Finance",
        )
        assert esc1.ticket_id.startswith("TKT-")

        esc2 = await service.aescalate_case(
            case_id="PO-ESC-005",
            reason="Missing manager approval signature",
            target_department="Finance",
        )
        # Reuses existing ticket ID and notifies duplicate
        assert esc2.ticket_id == esc1.ticket_id
        assert "Duplicate escalation suppressed" in esc2.audit_narrative

    async def test_standalone_in_memory_fallback(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When database operations fail or are disconnected, in-memory mode works smoothly."""
        service = ExceptionService()

        async def _mock_failing_session() -> AsyncGenerator[AsyncSession, None]:
            raise ConnectionRefusedError("Simulated database outage")
            if False:  # pragma: no cover
                yield None  # type: ignore[unreachable]

        monkeypatch.setattr(
            "workflow_intelligence.services.exception_service.get_db_session",
            _mock_failing_session,
        )

        req = WorkflowInvestigationRequest(
            case_id="PO-OFFLINE-006",
            workflow_type="purchase_order_creation",
            failure_payload={
                "error": "approval_timeout",
                "signature": "missing",
            },
        )
        inv = await service.ainvestigate_failure(req)
        assert inv.case_id == "PO-OFFLINE-006"
        assert inv.exception_id.startswith("EXC-")

        # Escalate offline
        esc = await service.aescalate_case(
            case_id="PO-OFFLINE-006",
            reason="Offline escalation test",
            target_department="Procurement",
        )
        assert esc.ticket_id.startswith("TKT-")
        assert esc.priority == SeverityLevel.MEDIUM
