"""File storage adapter for loading event logs from local files."""

from __future__ import annotations

from pathlib import Path

import pandas as pd


class FileStorageError(Exception):
    """Raised when file storage operations fail."""


def _resolve_and_validate_path(source_uri: str) -> Path:
    """Resolve a source URI to a safe local path and prevent path traversal.

    Args:
        source_uri: File path or URI to resolve.

    Returns:
        Resolved Path object.

    Raises:
        FileStorageError: If the path contains parent-directory references,
            escapes the allowed root, or does not exist.
    """
    # Security: check for parent-directory traversal on the RAW input path
    # BEFORE calling .resolve(), which canonicalizes ".." components and
    # would otherwise hide them from .parts inspection.
    if ".." in Path(source_uri).parts:
        raise FileStorageError(
            "Path traversal detected: '"
            + source_uri
            + "' contains parent-directory references."
        )

    resolved = Path(source_uri).expanduser().resolve()

    if not resolved.exists():
        raise FileStorageError(f"Event log file not found: {resolved}")

    return resolved


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
        else:
            raise FileStorageError(
                "Unsupported file format: '"
                + suffix
                + "'. Supported: .csv, .json, .parquet"
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

    Supports CSV, JSON, and Parquet formats. Validates that the required
    columns exist in the loaded data.

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
        else:
            raise FileStorageError(
                "Unsupported file format: '"
                + suffix
                + "'. Supported: .csv, .json, .parquet"
            )
    except FileStorageError:
        raise
    except Exception as exc:  # noqa: BLE001 - wrap any read error
        raise FileStorageError(
            "Failed to read event log '" + source_uri + "': " + str(exc)
        ) from exc

    required_cols = {case_id_col, activity_col, timestamp_col}
    missing = required_cols - set(df.columns)
    if missing:
        raise FileStorageError(
            "Missing required columns in '" + source_uri + "': " + str(sorted(missing))
        )

    return df


def list_supported_files(directory: str | Path) -> list[str]:
    """List supported event log files in a directory.

    Args:
        directory: Directory path to scan.

    Returns:
        Sorted list of file paths as strings.
    """
    dir_path = Path(directory)
    if not dir_path.exists():
        return []

    supported = {".csv", ".json", ".parquet", ".pq"}
    files = sorted(str(p) for p in dir_path.iterdir() if p.suffix.lower() in supported)
    return files
