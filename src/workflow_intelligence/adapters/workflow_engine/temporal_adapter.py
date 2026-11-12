"""Temporal workflow engine adapter with real and offline mock implementations.

Provides:
- ``TemporalAdapter``: real integration via the ``temporalio`` SDK
  (used when Temporal is reachable).
- ``MockTemporalAdapter``: offline mock that replays synthetic Temporal
  history from JSON fixtures. Used by default in tests and local dev.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from temporalio.client import Client

from workflow_intelligence.adapters.workflow_engine.base import (
    BaseWorkflowEngineAdapter,
    WorkflowEngineAdapterError,
)


class TemporalAdapter(BaseWorkflowEngineAdapter):
    """Real Temporal.io adapter backed by the temporalio SDK.

    Connects to a Temporal service and performs live workflow queries,
    signal sends, and history retrieval.
    """

    def __init__(
        self,
        target_address: str = "localhost:7233",
        namespace: str = "default",
    ) -> None:
        self._target_address = target_address
        self._namespace = namespace
        self._client: Client | None = None

    async def _ensure_client(self) -> Client:
        """Lazily establish the Temporal client connection."""
        if self._client is None:
            self._client = await Client.connect(
                self._target_address, namespace=self._namespace
            )
        return self._client

    async def close(self) -> None:
        """Close the Temporal client connection if open."""
        if self._client is not None:
            try:
                await self._client.close()  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001 - best effort close
                pass
            self._client = None

    async def get_workflow_history(self, workflow_id: str) -> list[dict[str, Any]]:
        """Return the execution history for a Temporal workflow."""
        client = await self._ensure_client()
        try:
            history = await client.fetch_history(  # type: ignore[attr-defined]
                workflow_id
            )
            return [
                event.to_dict() if hasattr(event, "to_dict") else event
                for event in history
            ]
        except Exception as exc:  # noqa: BLE001 - wrap Temporal errors
            raise WorkflowEngineAdapterError(
                f"Failed to fetch history for workflow {workflow_id}: {exc}"
            ) from exc

    async def send_update_signal(
        self, workflow_id: str, signal_name: str, payload: dict[str, Any]
    ) -> bool:
        """Send a signal to an in-flight Temporal workflow."""
        client = await self._ensure_client()
        try:
            handle = client.get_workflow_handle(workflow_id)
            await handle.signal(signal_name, payload)
            return True
        except Exception as exc:  # noqa: BLE001 - wrap Temporal errors
            raise WorkflowEngineAdapterError(
                f"Failed to send signal {signal_name} to {workflow_id}: {exc}"
            ) from exc

    async def query_workflow_state(self, workflow_id: str) -> dict[str, Any]:
        """Return the current state of a Temporal workflow."""
        client = await self._ensure_client()
        try:
            desc = await client.get_workflow_handle(workflow_id).describe()
            return {
                "workflow_id": desc.workflow_id,  # type: ignore[attr-defined]
                "status": str(desc.status),
                "start_time": str(desc.start_time),
                "execution_time": str(desc.execution_time),
            }
        except Exception as exc:  # noqa: BLE001 - wrap Temporal errors
            raise WorkflowEngineAdapterError(
                f"Failed to query state for workflow {workflow_id}: {exc}"
            ) from exc


class MockTemporalAdapter(BaseWorkflowEngineAdapter):
    """Offline mock Temporal adapter that replays synthetic history.

    Reads history fixtures from a JSON file mapping ``workflow_id`` to a
    list of history events. When no fixture exists for a workflow id, a
    deterministic synthetic history is generated.

    This adapter never makes network calls and is safe for unit and
    integration tests.
    """

    def __init__(self, fixture_path: str | Path | None = None) -> None:
        self._fixture_path = Path(fixture_path) if fixture_path else None
        self._fixtures: dict[str, list[dict[str, Any]]] = {}
        self._signals: dict[str, list[tuple[str, dict[str, Any]]]] = {}
        self._states: dict[str, dict[str, Any]] = {}
        self._load_fixtures()

    def _load_fixtures(self) -> None:
        """Load synthetic history fixtures from the configured JSON file."""
        if self._fixture_path is None or not self._fixture_path.exists():
            return
        try:
            data = json.loads(self._fixture_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise WorkflowEngineAdapterError(
                f"Failed to read Temporal fixture file {self._fixture_path}: {exc}"
            ) from exc
        if not isinstance(data, dict):
            raise WorkflowEngineAdapterError(
                "Temporal fixture file must contain a JSON object mapping "
                "workflow ids to history event lists."
            )
        for key, value in data.items():
            if isinstance(value, list):
                self._fixtures[str(key)] = value

    def _synthetic_history(self, workflow_id: str) -> list[dict[str, Any]]:
        """Generate a deterministic synthetic history for a workflow."""
        return [
            {
                "event_id": 1,
                "event_type": "WorkflowExecutionStarted",
                "workflow_id": workflow_id,
                "timestamp": "2026-01-01T09:00:00Z",
            },
            {
                "event_id": 2,
                "event_type": "TaskScheduled",
                "workflow_id": workflow_id,
                "timestamp": "2026-01-01T09:00:05Z",
            },
            {
                "event_id": 3,
                "event_type": "WorkflowExecutionCompleted",
                "workflow_id": workflow_id,
                "timestamp": "2026-01-01T09:05:00Z",
            },
        ]

    def set_state(self, workflow_id: str, state: dict[str, Any]) -> None:
        """Override the returned state for a workflow (test helper)."""
        self._states[workflow_id] = state

    async def get_workflow_history(self, workflow_id: str) -> list[dict[str, Any]]:
        """Return the (fixture or synthetic) history for a workflow."""
        if workflow_id in self._fixtures:
            return list(self._fixtures[workflow_id])
        return self._synthetic_history(workflow_id)

    async def send_update_signal(
        self, workflow_id: str, signal_name: str, payload: dict[str, Any]
    ) -> bool:
        """Record a signal delivery (mock, no network)."""
        self._signals.setdefault(workflow_id, []).append((signal_name, dict(payload)))
        return True

    async def query_workflow_state(self, workflow_id: str) -> dict[str, Any]:
        """Return the current (mock) state of a workflow."""
        if workflow_id in self._states:
            return dict(self._states[workflow_id])
        return {
            "workflow_id": workflow_id,
            "status": "COMPLETED",
            "start_time": "2026-01-01T09:00:00Z",
            "execution_time": "2026-01-01T09:00:00Z",
            "mock": True,
        }
