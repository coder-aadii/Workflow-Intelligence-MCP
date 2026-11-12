"""Abstract base interface for workflow engine adapters.

Defines the contract that every workflow orchestrator adapter (Temporal,
n8n, etc.) must satisfy. Concrete adapters implement real integrations
and/or offline mocks used during testing.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class WorkflowEngineAdapterError(Exception):
    """Raised when a workflow engine adapter operation fails."""


class BaseWorkflowEngineAdapter(ABC):
    """Abstract interface for interacting with a workflow engine.

    Implementations must provide:
    - ``get_workflow_history``: the execution history for a workflow.
    - ``send_update_signal``: send a signal to an in-flight workflow.
    - ``query_workflow_state``: the current state of a workflow.
    """

    @abstractmethod
    async def get_workflow_history(self, workflow_id: str) -> list[dict[str, Any]]:
        """Return the execution history for a workflow.

        Args:
            workflow_id: Unique identifier of the workflow.

        Returns:
            A list of history events, each as a dictionary.

        Raises:
            WorkflowEngineAdapterError: If the workflow cannot be found or
                the engine is unreachable.
        """

    @abstractmethod
    async def send_update_signal(
        self, workflow_id: str, signal_name: str, payload: dict[str, Any]
    ) -> bool:
        """Send a signal to an in-flight workflow.

        Args:
            workflow_id: Unique identifier of the workflow.
            signal_name: Name of the signal to send.
            payload: Signal payload.

        Returns:
            True if the signal was delivered, False otherwise.

        Raises:
            WorkflowEngineAdapterError: If the signal cannot be delivered.
        """

    @abstractmethod
    async def query_workflow_state(self, workflow_id: str) -> dict[str, Any]:
        """Return the current state of a workflow.

        Args:
            workflow_id: Unique identifier of the workflow.

        Returns:
            A dictionary describing the workflow state.

        Raises:
            WorkflowEngineAdapterError: If the workflow cannot be found or
                the engine is unreachable.
        """
