"""File storage adapter for loading event logs from local files."""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pandas as pd

from workflow_intelligence.security.file_validator import (
    SecurityError,
    validate_safe_path,
)

try:  # pm4py is optional; only needed for OCEL files
    import pm4py
except ImportError:  # pragma: no cover - pm4py optional
    pm4py = None


class FileStorageError(Exception):
    """Raised when file storage operations fail."""


# File extensions supported by the loader.
SUPPORTED_EXTENSIONS = {".csv", ".json", ".parquet", ".pq", ".ocel", ".jsonocel"}


def _resolve_and_validate_path(source_uri: str) -> Path:
    """Resolve a source URI to a safe local path and enforce security boundaries.

    Args:
        source_uri: File path or URI to resolve.

    Returns:
        Resolved and canonicalized Path object.

    Raises:
        FileStorageError: If path traversal, sensitive file access, unauthorized directory,
            unsupported format, or size limits are violated, or if file does not exist.
    """
    try:
        return validate_safe_path(source_uri)
    except SecurityError as exc:
        raise FileStorageError(str(exc)) from exc


def _load_ocel(path: Path) -> pd.DataFrame:
    """Load an OCEL (Object-Centric Event Log) file into a flat DataFrame.

    Uses pm4py to read the OCEL file and produces an extended table where
    each row represents an event with its related objects. Object ID
    columns (e.g. ``ocel:type:order``) contain lists of object IDs; the
    loader takes the first object ID as the case identifier.

    Args:
        path: Path to the OCEL file (.ocel or .jsonocel).

    Returns:
        A Pandas DataFrame with columns including ``ocel:eid``,
        ``ocel:activity``, ``ocel:timestamp``, and object columns like
        ``ocel:type:order``.

    Raises:
        FileStorageError: If pm4py is not installed or the OCEL file cannot
            be read.
    """
    if pm4py is None:  # pragma: no cover - pm4py optional
        raise FileStorageError(
            "pm4py is required to read OCEL files. Install it with: "
            "pip install pm4py>=2.7"
        )

    try:
        ocel = pm4py.read_ocel(str(path))
    except Exception as exc:  # noqa: BLE001 - wrap pm4py read error
        raise FileStorageError(
            "Failed to read OCEL file '" + str(path) + "': " + str(exc)
        ) from exc

    try:
        df = ocel.get_extended_table()
    except Exception as exc:  # noqa: BLE001 - wrap conversion error
        raise FileStorageError(
            "Failed to convert OCEL to event table '" + str(path) + "': " + str(exc)
        ) from exc

    if df is None or len(df) == 0:
        raise FileStorageError("OCEL file '" + str(path) + "' contains no events.")

    # Object ID columns in the extended table are named like
    # ``ocel:type:{object_type}`` and contain lists of object IDs. Flatten
    # them to a single object ID (first element) so callers can use them as
    # case identifiers.
    object_cols = [c for c in df.columns if c.startswith("ocel:type:")]
    for col in object_cols:
        df[col] = df[col].apply(
            lambda lst: lst[0] if isinstance(lst, list) and len(lst) > 0 else None
        )

    return cast(pd.DataFrame, df)


def _ocel_column_map(df: pd.DataFrame) -> dict[str, str]:
    """Map OCEL extended-table columns to standard event-log column names.

    Returns a mapping from standard column name (case_id, activity,
    timestamp) to the OCEL column name that should be used for it.

    Args:
        df: The OCEL extended-table DataFrame.

    Returns:
        Dictionary mapping standard column names to OCEL column names.
    """
    columns = set(df.columns)
    mapping: dict[str, str] = {}

    # Case ID: use the first object column found (e.g. ocel:type:order).
    object_cols = sorted(c for c in columns if c.startswith("ocel:type:"))
    if object_cols:
        mapping["case_id"] = object_cols[0]

    # Activity: prefer ocel:activity.
    if "ocel:activity" in columns:
        mapping["activity"] = "ocel:activity"

    # Timestamp: prefer ocel:timestamp.
    if "ocel:timestamp" in columns:
        mapping["timestamp"] = "ocel:timestamp"

    return mapping


def get_event_log_columns(source_uri: str) -> list[str]:
    """Return column names of an event log without enforcing specific names.

    Loads only the header (for CSV) or a zero-row frame (for JSON/Parquet)
    so callers can inspect available columns before requesting validation.

    Args:
        source_uri: Path to the event log file.

    Returns:
        List of column names in file order.

    Raises:
        FileStorageError: If the file format is unsupported or the file
            cannot be read.
    """
    path = _resolve_and_validate_path(source_uri)
    suffix = path.suffix.lower()

    try:
        if suffix == ".csv":
            df = pd.read_csv(path, nrows=0)
        elif suffix == ".json":
            df = pd.read_json(path)
            if len(df) > 0:
                df = df.iloc[:0]
        elif suffix in (".parquet", ".pq"):
            df = pd.read_parquet(path)
        elif suffix in (".ocel", ".jsonocel"):
            # For OCEL, load the full file (small overhead) and return
            # extended-table column names.
            df = _load_ocel(path)
        else:
            raise FileStorageError(
                "Unsupported file format: '"
                + suffix
                + "'. Supported: .csv, .json, .parquet, .ocel, .jsonocel"
            )
    except FileStorageError:
        raise
    except Exception as exc:  # noqa: BLE001 - wrap any read error
        raise FileStorageError(
            "Failed to read event log '" + source_uri + "': " + str(exc)
        ) from exc

    return list(df.columns)


def load_event_log(
    source_uri: str,
    case_id_col: str,
    activity_col: str,
    timestamp_col: str,
) -> pd.DataFrame:
    """Load an event log from a local file into a Pandas DataFrame.

    Supports CSV, JSON, Parquet, and OCEL formats. Validates that the
    required columns exist in the loaded data.

    For OCEL files (.ocel, .jsonocel), the loader uses pm4py to produce an
    extended table where each row is an event-object pair. The default
    column mapping is ``case_id_col=ocel:oid``,
    ``activity_col=ocel:activity``, ``timestamp_col=ocel:timestamp``.

    Args:
        source_uri: Path to the event log file.
        case_id_col: Column name for case identifiers.
        activity_col: Column name for activity names.
        timestamp_col: Column name for event timestamps.

    Returns:
        A Pandas DataFrame containing the event log.

    Raises:
        FileStorageError: If the file format is unsupported, the file is missing,
            or required columns are absent.
    """
    path = _resolve_and_validate_path(source_uri)
    suffix = path.suffix.lower()

    try:
        if suffix == ".csv":
            df = pd.read_csv(path)
        elif suffix == ".json":
            df = pd.read_json(path)
        elif suffix in (".parquet", ".pq"):
            df = pd.read_parquet(path)
        elif suffix in (".ocel", ".jsonocel"):
            df = _load_ocel(path)
            # For OCEL, if the caller passed default column names, try to
            # auto-map them to OCEL extended-table columns.
            ocel_map = _ocel_column_map(df)
            if case_id_col == "case_id" and "case_id" in ocel_map:
                case_id_col = ocel_map["case_id"]
            if activity_col == "activity" and "activity" in ocel_map:
                activity_col = ocel_map["activity"]
            if timestamp_col == "timestamp" and "timestamp" in ocel_map:
                timestamp_col = ocel_map["timestamp"]
        else:
            raise FileStorageError(
                "Unsupported file format: '"
                + suffix
                + "'. Supported: .csv, .json, .parquet, .ocel, .jsonocel"
            )
    except FileStorageError:
        raise
    except Exception as exc:  # noqa: BLE001 - wrap any read error
        raise FileStorageError(
            "Failed to read event log '" + source_uri + "': " + str(exc)
        ) from exc

    if len({case_id_col, activity_col, timestamp_col}) != 3:
        raise FileStorageError(
            f"Columns must be distinct for case_id, activity, and timestamp. "
            f"Received duplicate or overlapping mappings: case_id='{case_id_col}', "
            f"activity='{activity_col}', timestamp='{timestamp_col}'. "
            f"Available columns: {list(df.columns)}"
        )

    required_cols = {case_id_col, activity_col, timestamp_col}
    missing = required_cols - set(df.columns)
    if missing:
        raise FileStorageError(
            f"Missing required columns in '{source_uri}': {sorted(missing)}. "
            f"Available columns: {list(df.columns)}"
        )

    return df


def list_supported_files(directory: str | Path) -> list[str]:
    """List supported event log files in a directory.

    Args:
        directory: Directory path to scan.

    Returns:
        Sorted list of file paths as strings.
    """
    from workflow_intelligence.security.file_validator import (
        BLOCKED_FILENAMES,
        get_default_allowed_directories,
    )

    raw_path = Path(directory)
    if ".." in raw_path.parts:
        raise SecurityError("Directory traversal detected: contains '..'.")

    resolved_dir = raw_path.expanduser().resolve()
    if not resolved_dir.exists() or not resolved_dir.is_dir():
        return []

    # Verify against allowed directories
    allowed_dirs = get_default_allowed_directories()
    is_allowed = any(
        resolved_dir.is_relative_to(d.resolve()) for d in allowed_dirs
    )
    if not is_allowed:
        raise SecurityError(
            f"Access denied: Directory '{resolved_dir}' is outside allowed directories."
        )

    for part in resolved_dir.parts:
        if part.startswith(".") and part != ".":
            raise SecurityError(f"Access denied: Hidden directory '{part}'.")

    files = [
        str(p)
        for p in sorted(resolved_dir.iterdir())
        if p.is_file()
        and p.suffix.lower() in SUPPORTED_EXTENSIONS
        and not p.name.startswith(".")
        and p.name.lower() not in BLOCKED_FILENAMES
    ]
    return files
