"""Integration tests for the persistence layer and workflow engine adapters.

All tests run completely offline: the database tests use a temporary SQLite
file (via SQLAlchemy async), and the adapter tests use the in-memory mock
implementations. No live PostgreSQL, Redis, Temporal, or n8n instance is
required.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from workflow_intelligence.adapters.cache.redis_adapter import InMemoryCacheAdapter
from workflow_intelligence.adapters.workflow_engine import (
    MockN8NAdapter,
    MockTemporalAdapter,
    N8NAdapterError,
)
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

TEST_DB_URL = "sqlite+aiosqlite:////tmp/workflow_intelligence_test.db"


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """Provide an async SQLAlchemy session over a fresh temp SQLite DB."""
    reset_engine(TEST_DB_URL)
    await init_db(TEST_DB_URL)
    async for session in get_db_session():
        yield session
    await drop_all(TEST_DB_URL)


class TestDatabasePersistence:
    """Tests for SQLAlchemy models, session engine, and repository CRUD."""

    async def test_workflow_instance_crud(self, db_session: AsyncSession) -> None:
        repo = WorkflowRepository(db_session)

        wf = await repo.create_workflow_instance(
            workflow_type="purchase_order_creation",
            status="pending",
            metadata={"case_id": "PO-001"},
        )
        assert wf.id is not None
        assert wf.workflow_type == "purchase_order_creation"
        assert wf.status == "pending"
        assert wf.metadata_json == {"case_id": "PO-001"}

        # Get by id
        fetched = await repo.get(wf.id)
        assert fetched is not None
        assert fetched.workflow_type == "purchase_order_creation"

        # Update status
        updated = await repo.update_status(wf.id, "running")
        assert updated is not None
        assert updated.status == "running"

        # Complete
        completed = await repo.complete_workflow(wf.id)
        assert completed is not None
        assert completed.status == "completed"
        assert completed.end_time is not None

        # List by type and status
        by_type = await repo.get_by_workflow_type("purchase_order_creation")
        assert len(by_type) == 1
        by_status = await repo.get_by_status("completed")
        assert len(by_status) == 1

        # Delete
        deleted = await repo.delete(wf.id)
        assert deleted is True
        assert await repo.get(wf.id) is None

    async def test_exception_record_crud_and_fk(self, db_session: AsyncSession) -> None:
        wf_repo = WorkflowRepository(db_session)
        exc_repo = ExceptionRepository(db_session)

        wf = await wf_repo.create_workflow_instance(
            workflow_type="purchase_order_creation"
        )

        exc = await exc_repo.create_exception(
            workflow_id=wf.id,
            category="MASTER_DATA_DISCREPANCY",
            root_cause="Vendor name typo with matching tax ID.",
            payload={"vendor": "ABC Technologies"},
        )
        assert exc.id is not None
        assert exc.workflow_id == wf.id
        assert exc.category == "MASTER_DATA_DISCREPANCY"
        assert exc.is_resolved is False
        assert exc.resolved_at is None

        # Fetch by workflow id
        by_wf = await exc_repo.get_by_workflow_id(wf.id)
        assert len(by_wf) == 1
        assert by_wf[0].id == exc.id

        # Fetch by category
        by_cat = await exc_repo.get_by_category("MASTER_DATA_DISCREPANCY")
        assert len(by_cat) == 1

        # Unresolved list
        unresolved = await exc_repo.get_unresolved()
        assert len(unresolved) == 1

        # Mark resolved
        resolved = await exc_repo.mark_resolved(exc.id)
        assert resolved is not None
        assert resolved.is_resolved is True
        assert resolved.resolved_at is not None

        unresolved = await exc_repo.get_unresolved()
        assert len(unresolved) == 0

        # FK integrity: deleting the workflow cascades to exceptions
        await wf_repo.delete(wf.id)
        by_wf = await exc_repo.get_by_workflow_id(wf.id)
        assert len(by_wf) == 0

    async def test_audit_log_crud(self, db_session: AsyncSession) -> None:
        repo = AuditRepository(db_session)

        entry = await repo.create_audit_entry(
            action_name="exception_escalate_case",
            actor="system",
            details={"case_id": "PO-001", "department": "Finance"},
            correlation_id="corr-123",
        )
        assert entry.id is not None
        assert entry.action_name == "exception_escalate_case"
        assert entry.actor == "system"
        assert entry.correlation_id == "corr-123"

        by_corr = await repo.get_by_correlation_id("corr-123")
        assert len(by_corr) == 1
        assert by_corr[0].id == entry.id

        by_action = await repo.get_by_action("exception_escalate_case")
        assert len(by_action) == 1


class TestTemporalAdapter:
    """Tests for the mock Temporal workflow engine adapter."""

    @pytest.fixture
    def adapter(self) -> MockTemporalAdapter:
        return MockTemporalAdapter()

    async def test_get_workflow_history_synthetic(
        self, adapter: MockTemporalAdapter
    ) -> None:
        history = await adapter.get_workflow_history("wf-001")
        assert isinstance(history, list)
        assert len(history) == 3
        assert history[0]["event_type"] == "WorkflowExecutionStarted"

    async def test_send_update_signal_and_query_state(
        self, adapter: MockTemporalAdapter
    ) -> None:
        sent = await adapter.send_update_signal(
            "wf-001", "approve", {"approver": "manager"}
        )
        assert sent is True

        state = await adapter.query_workflow_state("wf-001")
        assert state["workflow_id"] == "wf-001"
        assert state["mock"] is True

        # Override state and verify it is returned
        adapter.set_state("wf-001", {"status": "RUNNING", "custom": True})
        state = await adapter.query_workflow_state("wf-001")
        assert state["status"] == "RUNNING"
        assert state["custom"] is True

    async def test_fixture_loading(self, tmp_path: Path) -> None:
        fixture = {
            "wf-fixture": [
                {"event_id": 1, "event_type": "CustomEvent", "detail": "fixture-data"}
            ]
        }
        fixture_path = tmp_path / "temporal_fixture.json"
        fixture_path.write_text(json.dumps(fixture), encoding="utf-8")

        adapter = MockTemporalAdapter(fixture_path=str(fixture_path))
        history = await adapter.get_workflow_history("wf-fixture")
        assert len(history) == 1
        assert history[0]["detail"] == "fixture-data"

        # Unknown workflow still gets synthetic history
        history = await adapter.get_workflow_history("wf-unknown")
        assert len(history) == 3


class TestN8NAdapter:
    """Tests for the mock n8n webhook adapter."""

    @pytest.fixture
    def adapter(self) -> MockN8NAdapter:
        return MockN8NAdapter()

    async def test_dispatch_webhook_and_execution_log(
        self, adapter: MockN8NAdapter
    ) -> None:
        payload = {"case_id": "PO-001", "action": "escalate"}
        sent = await adapter.dispatch_webhook("webhook-001", payload)
        assert sent is True

        log = await adapter.get_execution_log("webhook-001")
        assert log["execution_id"] == "webhook-001"
        assert log["status"] == "success"
        assert log["payload"] == payload

        # Missing execution log raises
        with pytest.raises(N8NAdapterError):
            await adapter.get_execution_log("does-not-exist")

    async def test_injected_failure(self, adapter: MockN8NAdapter) -> None:
        adapter.set_fail_next(True)
        with pytest.raises(N8NAdapterError):
            await adapter.dispatch_webhook("webhook-002", {})

        # After failure, subsequent dispatch works again
        sent = await adapter.dispatch_webhook("webhook-002", {"retry": True})
        assert sent is True


class TestInMemoryCache:
    """Tests for the in-memory cache adapter (Redis mock)."""

    @pytest.fixture
    def cache(self) -> InMemoryCacheAdapter:
        return InMemoryCacheAdapter()

    async def test_set_get_delete_exists(self, cache: InMemoryCacheAdapter) -> None:
        assert await cache.exists("key-1") is False
        assert await cache.get("key-1") is None

        await cache.set("key-1", "value-1", ttl_seconds=60)
        assert await cache.exists("key-1") is True
        assert await cache.get("key-1") == "value-1"

        await cache.delete("key-1")
        assert await cache.exists("key-1") is False
        assert await cache.get("key-1") is None

    async def test_ttl_expiration(self, cache: InMemoryCacheAdapter) -> None:
        await cache.set("short", "gone", ttl_seconds=0)
        # TTL of 0 means it expires essentially immediately.
        await asyncio.sleep(0.01)
        assert await cache.exists("short") is False
        assert await cache.get("short") is None

    async def test_clear_and_size(self, cache: InMemoryCacheAdapter) -> None:
        await cache.set("a", "1", ttl_seconds=60)
        await cache.set("b", "2", ttl_seconds=60)
        assert cache.size() == 2
        cache.clear()
        assert cache.size() == 0
