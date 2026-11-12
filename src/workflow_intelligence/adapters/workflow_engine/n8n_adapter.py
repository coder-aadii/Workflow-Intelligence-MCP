"""n8n webhook adapter with real HTTP dispatch and offline mock.

Provides:
- ``N8NAdapter``: asynchronous ``httpx`` dispatcher with retry logic that
  sends webhooks to a real n8n instance.
- ``MockN8NAdapter``: in-memory event dispatch log used during offline
  testing (no network calls).
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, cast

import httpx

from workflow_intelligence.adapters.workflow_engine.base import (
    BaseWorkflowEngineAdapter,
    WorkflowEngineAdapterError,
)


class N8NAdapterError(WorkflowEngineAdapterError):
    """Raised when an n8n adapter operation fails."""


class N8NAdapter(BaseWorkflowEngineAdapter):
    """Real n8n adapter that dispatches webhooks over HTTP with retries.

    Uses :class:`httpx.AsyncClient` and retries on transient failures with
    a bounded exponential backoff.
    """

    def __init__(
        self,
        base_url: str = "http://localhost:5678",
        timeout_seconds: float = 10.0,
        max_retries: int = 3,
        backoff_base: float = 0.5,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Return a lazily-created async HTTP client."""
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout_seconds)
        return self._client

    async def close(self) -> None:
        """Close the underlying HTTP client if open."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _dispatch_with_retry(
        self, url: str, payload: dict[str, Any]
    ) -> httpx.Response:
        """Dispatch a webhook with exponential-backoff retries.

        Args:
            url: Full webhook URL.
            payload: JSON-serializable payload.

        Returns:
            The successful HTTP response.

        Raises:
            N8NAdapterError: If all retries are exhausted.
        """
        last_error: Exception | None = None
        for attempt in range(self._max_retries):
            try:
                client = await self._get_client()
                response = await client.post(url, json=payload)
                if response.is_success:
                    return response
                # Retry on server errors; client errors are not retried.
                if 400 <= response.status_code < 500:
                    raise N8NAdapterError(
                        f"n8n webhook rejected payload (HTTP {response.status_code}): "
                        f"{response.text}"
                    )
                last_error = N8NAdapterError(
                    f"n8n webhook returned HTTP {response.status_code}"
                )
            except N8NAdapterError:
                raise
            except (httpx.RequestError, httpx.HTTPStatusError) as exc:
                last_error = exc
            if attempt < self._max_retries - 1:
                await asyncio.sleep(self._backoff_base * (2**attempt))
        raise N8NAdapterError(
            "n8n webhook dispatch failed after "
            f"{self._max_retries} retries: {last_error}"
        ) from last_error

    async def dispatch_webhook(self, webhook_id: str, payload: dict[str, Any]) -> bool:
        """Dispatch a webhook to n8n.

        Args:
            webhook_id: n8n webhook identifier.
            payload: JSON-serializable webhook payload.

        Returns:
            True if the webhook was dispatched successfully.

        Raises:
            N8NAdapterError: If dispatch fails after all retries.
        """
        url = f"{self._base_url}/webhook/{webhook_id}"
        await self._dispatch_with_retry(url, payload)
        return True

    async def get_execution_log(self, execution_id: str) -> dict[str, Any]:
        """Return the execution log for a given n8n execution."""
        client = await self._get_client()
        url = f"{self._base_url}/executions/{execution_id}"
        try:
            response = await client.get(url)
            if response.is_success:
                return cast(dict[str, Any], response.json())
            raise N8NAdapterError(
                f"n8n execution log unavailable (HTTP {response.status_code}): "
                f"{response.text}"
            )
        except N8NAdapterError:
            raise
        except (httpx.RequestError, httpx.HTTPStatusError) as exc:
            raise N8NAdapterError(
                f"Failed to fetch execution log {execution_id}: {exc}"
            ) from exc

    async def get_workflow_history(self, workflow_id: str) -> list[dict[str, Any]]:
        """n8n does not expose workflow history; returns empty list."""
        return []

    async def query_workflow_state(self, workflow_id: str) -> dict[str, Any]:
        """n8n does not expose workflow state; returns unknown state."""
        return {"workflow_id": workflow_id, "status": "unknown", "engine": "n8n"}


class MockN8NAdapter(BaseWorkflowEngineAdapter):
    """Offline mock n8n adapter with an in-memory event dispatch log.

    Records every dispatched webhook and exposes them via
    ``get_execution_log``. Safe for unit and integration tests.
    """

    def __init__(self) -> None:
        self._dispatches: dict[str, list[dict[str, Any]]] = {}
        self._signals: dict[str, list[dict[str, Any]]] = {}
        self._execution_logs: dict[str, dict[str, Any]] = {}
        self._fail_next: bool = False

    def set_fail_next(self, fail: bool = True) -> None:
        """Force the next dispatch to fail (test helper)."""
        self._fail_next = fail

    def register_execution_log(self, execution_id: str, log: dict[str, Any]) -> None:
        """Register a synthetic execution log (test helper)."""
        self._execution_logs[execution_id] = dict(log)

    async def dispatch_webhook(self, webhook_id: str, payload: dict[str, Any]) -> bool:
        """Record a webhook dispatch in the in-memory log."""
        if self._fail_next:
            self._fail_next = False
            raise N8NAdapterError("Mock n8n dispatch failure (injected).")
        entry = {
            "webhook_id": webhook_id,
            "payload": dict(payload),
            "dispatched_at": time.time(),
        }
        self._dispatches.setdefault(webhook_id, []).append(entry)
        self._execution_logs[webhook_id] = {
            "execution_id": webhook_id,
            "status": "success",
            "payload": dict(payload),
        }
        return True

    async def send_update_signal(
        self, workflow_id: str, signal_name: str, payload: dict[str, Any]
    ) -> bool:
        """Record a signal in the in-memory mock (n8n has no native signals)."""
        entry = {
            "workflow_id": workflow_id,
            "signal_name": signal_name,
            "payload": dict(payload),
            "sent_at": time.time(),
        }
        self._signals.setdefault(workflow_id, []).append(entry)
        return True

    async def get_execution_log(self, execution_id: str) -> dict[str, Any]:
        """Return the recorded execution log, or raise if not found."""
        if execution_id not in self._execution_logs:
            raise N8NAdapterError(f"n8n execution log not found: {execution_id}")
        return dict(self._execution_logs[execution_id])

    async def get_workflow_history(self, workflow_id: str) -> list[dict[str, Any]]:
        """Return recorded dispatches for a webhook id as history."""
        return list(self._dispatches.get(workflow_id, []))

    async def query_workflow_state(self, workflow_id: str) -> dict[str, Any]:
        """Return the recorded mock state for a webhook id."""
        log = self._execution_logs.get(workflow_id)
        if log is None:
            return {
                "workflow_id": workflow_id,
                "status": "unknown",
                "engine": "n8n-mock",
            }
        return {
            "workflow_id": workflow_id,
            "status": log.get("status", "unknown"),
            "engine": "n8n-mock",
            "payload": log.get("payload"),
        }
