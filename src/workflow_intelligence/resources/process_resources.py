"""MCP Resources for process discovery data.

Exposes:

- ``workflow://process-maps/{process_name}`` (``application/json``): a
  dynamic Directly-Follows Graph (nodes, edges, average duration) computed
  from the configured event log. The ``{process_name}`` is resolved
  dynamically against a configured data directory (or the package
  ``sample-data/`` directory), so any CSV/Parquet event log can be mined
  without hardcoding process names.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from workflow_intelligence.adapters.file_storage.loader import list_supported_files
from workflow_intelligence.schemas.process import ProcessAnalyzeRequest


def _resolve_process_dataset(
    process_name: str, data_dir: str | Path | None = None
) -> Path | None:
    """Resolve a process name to a dataset file path.

    Resolution order (first match wins):
    1. Exact stem match (case-insensitive), e.g. ``procurement`` →
       ``procurement.csv`` or ``procurement.parquet``.
    2. Exact filename match (case-insensitive with extension).
    3. Stem starts with ``<process_name>_`` and contains ``event``/``log``,
       e.g. ``procurement`` → ``procurement_events.csv``.
    4. Stem starts with ``<process_name>_`` (general fallback).
    5. ``process_name`` is a substring of the stem (last resort).

    Args:
        process_name: Process identifier to resolve.
        data_dir: Directory to search for event log files. Defaults to
            the ``SAMPLE_DATA_DIR`` environment variable, then
            ``sample-data/`` relative to the project root.

    Returns:
        Resolved file path, or ``None`` if no match is found.
    """
    if data_dir is None:
        data_dir = Path(
            os.environ.get(
                "SAMPLE_DATA_DIR",
                str(Path(__file__).resolve().parents[3] / "sample-data"),
            )
        )
    else:
        data_dir = Path(data_dir)

    if not data_dir.is_dir():
        return None

    files = list_supported_files(data_dir)
    name_lower = process_name.lower()

    def _is_event_log(path_str: str) -> bool:
        stem = Path(path_str).stem.lower()
        return "event" in stem or "log" in stem

    # 1. Exact stem match.
    for f in files:
        if Path(f).stem.lower() == name_lower:
            return Path(f)

    # 2. Exact filename match.
    for f in files:
        if Path(f).name.lower() == f"{name_lower}.csv":
            return Path(f)
        if Path(f).name.lower() == f"{name_lower}.parquet":
            return Path(f)

    # 3. Stem starts with <process_name>_ and contains event/log.
    candidates = [
        f
        for f in files
        if Path(f).stem.lower().startswith(name_lower + "_") and _is_event_log(f)
    ]
    if candidates:
        return Path(sorted(candidates)[0])

    # 4. Stem starts with <process_name>_ (general fallback).
    candidates = [f for f in files if Path(f).stem.lower().startswith(name_lower + "_")]
    if candidates:
        return Path(sorted(candidates)[0])

    # 5. process_name is a substring of the stem (last resort).
    candidates = [f for f in files if name_lower in Path(f).stem.lower()]
    if candidates:
        # Prefer event logs.
        event_logs = [f for f in candidates if _is_event_log(f)]
        if event_logs:
            return Path(sorted(event_logs)[0])
        return Path(sorted(candidates)[0])

    return None


def _build_graph(result: Any, process_name: str) -> dict[str, Any]:
    """Build a process map graph dict from a process analysis result."""
    nodes = [
        {"id": activity, "label": activity} for activity in result.unique_activities
    ]
    edges = [
        {
            "source": transition.source_activity,
            "target": transition.target_activity,
            "count": transition.transition_count,
            "avg_duration_hours": transition.mean_duration_hours,
        }
        for transition in result.transitions
    ]
    return {
        "process_name": process_name,
        "nodes": nodes,
        "edges": edges,
        "total_cases": result.total_cases,
        "total_events": result.total_events,
        "variant_count": result.variant_count,
    }


def register_process_resources(
    mcp: Any,
    process_service: Any,
    default_source_uri: str,
    data_dir: str | Path | None = None,
) -> None:
    """Register process-map resources with the FastMCP server.

    The ``workflow://process-maps/{process_name}}`` resource resolves
    ``{process_name}`` dynamically against the configured data directory,
    so any registered event log can be mined without hardcoding process
    names.

    Args:
        mcp: FastMCP server instance.
        process_service: :class:`ProcessService` used to mine the event log.
        default_source_uri: Default event log used when computing maps.
        data_dir: Directory to search for event log files. Defaults to
            ``sample-data/`` relative to the project root.
    """

    @mcp.resource(  # type: ignore[untyped-decorator]
        "workflow://process-maps/{process_name}",
        name="Process Map",
        mime_type="application/json",
        description=(
            "Dynamic Directly-Follows Graph (nodes, edges, average "
            "duration) for a business process, resolved from the "
            "configured data directory."
        ),
    )
    async def read_process_map(process_name: str) -> str:
        """Return the process map JSON for ``process_name``.

        Resolves ``{process_name}`` to a matching CSV/Parquet event log in
        the configured data directory and mines its Directly-Follows Graph.
        Returns an empty graph with an error note when no match is found.
        """
        dataset_path = _resolve_process_dataset(process_name, data_dir)
        if dataset_path is None:
            graph: dict[str, Any] = {
                "process_name": process_name,
                "nodes": [],
                "edges": [],
                "total_cases": 0,
                "total_events": 0,
                "note": f"Event log not found for process '{process_name}'.",
            }
            return json.dumps(graph)

        # Use the resolved dataset's columns for mining, falling back to
        # conventional names when auto-detection is not available.
        from workflow_intelligence.adapters.file_storage.loader import (
            get_event_log_columns,
        )

        columns = get_event_log_columns(str(dataset_path))
        col_lower = {c.lower(): c for c in columns}

        def pick(*candidates: str) -> str:
            for cand in candidates:
                if cand.lower() in col_lower:
                    return col_lower[cand.lower()]
            return candidates[0]

        case_id_col = pick("case_id", "caseid", "case id", "id")
        activity_col = pick("activity", "act", "action")
        timestamp_col = pick("timestamp", "time", "date", "ts")

        request = ProcessAnalyzeRequest(
            source_uri=str(dataset_path),
            case_id_col=case_id_col,
            activity_col=activity_col,
            timestamp_col=timestamp_col,
        )
        result = process_service.analyze_business_process(request)
        return json.dumps(_build_graph(result, process_name))
