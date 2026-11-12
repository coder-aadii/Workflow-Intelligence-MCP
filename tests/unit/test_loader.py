"""Unit tests for the FileStorageLoader adapter."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from workflow_intelligence.adapters.file_storage.loader import (
    FileStorageError,
    get_event_log_columns,
    list_supported_files,
    load_event_log,
)


def _has_parquet_engine() -> bool:
    try:
        import pyarrow  # noqa: F401  # type: ignore

        return True
    except ImportError:
        pass
    try:
        import fastparquet  # noqa: F401  # type: ignore

        return True
    except ImportError:
        return False


requires_parquet = pytest.mark.skipif(
    not _has_parquet_engine(),
    reason="No parquet engine (pyarrow/fastparquet) installed",
)


@pytest.fixture
def csv_log(tmp_path: Path) -> str:
    df = pd.DataFrame(
        [
            {"case_id": "C1", "activity": "A", "timestamp": "2026-01-01T09:00:00"},
            {"case_id": "C1", "activity": "B", "timestamp": "2026-01-01T10:00:00"},
        ]
    )
    p = tmp_path / "events.csv"
    df.to_csv(p, index=False)
    return str(p)


@pytest.fixture
def json_log(tmp_path: Path) -> str:
    df = pd.DataFrame(
        [
            {"case_id": "C1", "activity": "A", "timestamp": "2026-01-01T09:00:00"},
            {"case_id": "C2", "activity": "B", "timestamp": "2026-01-01T10:00:00"},
        ]
    )
    p = tmp_path / "events.json"
    df.to_json(p, orient="records")
    return str(p)


@pytest.fixture
def parquet_log(tmp_path: Path) -> str:
    df = pd.DataFrame(
        [
            {"case_id": "C1", "activity": "A", "timestamp": "2026-01-01T09:00:00"},
            {"case_id": "C2", "activity": "B", "timestamp": "2026-01-01T10:00:00"},
        ]
    )
    p = tmp_path / "events.parquet"
    df.to_parquet(p, index=False)
    return str(p)


class TestLoadEventLog:
    """Tests for load_event_log across formats."""

    def test_load_csv(self, csv_log: str) -> None:
        df = load_event_log(csv_log, "case_id", "activity", "timestamp")
        assert len(df) == 2
        assert list(df.columns) == ["case_id", "activity", "timestamp"]

    def test_load_json(self, json_log: str) -> None:
        df = load_event_log(json_log, "case_id", "activity", "timestamp")
        assert len(df) == 2

    @requires_parquet
    def test_load_parquet(self, parquet_log: str) -> None:
        df = load_event_log(parquet_log, "case_id", "activity", "timestamp")
        assert len(df) == 2

    def test_load_missing_file(self) -> None:
        with pytest.raises(FileStorageError):
            load_event_log("/nonexistent/path.csv", "case_id", "activity", "timestamp")

    def test_load_missing_columns(self, csv_log: str) -> None:
        with pytest.raises(FileStorageError):
            load_event_log(csv_log, "nonexistent_col", "activity", "timestamp")

    def test_load_unsupported_format(self, tmp_path: Path) -> None:
        p = tmp_path / "events.txt"
        p.write_text("case_id,activity,timestamp\n")
        with pytest.raises(FileStorageError):
            load_event_log(str(p), "case_id", "activity", "timestamp")

    def test_path_traversal_rejected(self) -> None:
        """Paths containing parent-directory references are rejected."""
        with pytest.raises(FileStorageError):
            load_event_log("../../../etc/passwd", "a", "b", "c")


class TestGetEventLogColumns:
    """Tests for get_event_log_columns header inspection."""

    def test_get_csv_columns(self, csv_log: str) -> None:
        cols = get_event_log_columns(csv_log)
        assert cols == ["case_id", "activity", "timestamp"]

    def test_get_json_columns(self, json_log: str) -> None:
        cols = get_event_log_columns(json_log)
        assert "case_id" in cols
        assert "activity" in cols

    @requires_parquet
    def test_get_parquet_columns(self, parquet_log: str) -> None:
        cols = get_event_log_columns(parquet_log)
        assert "case_id" in cols

    def test_get_columns_unsupported_format(self, tmp_path: Path) -> None:
        p = tmp_path / "events.txt"
        p.write_text("case_id,activity\n")
        with pytest.raises(FileStorageError):
            get_event_log_columns(str(p))


class TestListSupportedFiles:
    """Tests for list_supported_files."""

    def test_list_supported_files(self, tmp_path: Path) -> None:
        (tmp_path / "a.csv").write_text("x\n")
        (tmp_path / "b.json").write_text("[]\n")
        (tmp_path / "c.parquet").write_bytes(b"")
        (tmp_path / "d.txt").write_text("ignore\n")
        files = list_supported_files(tmp_path)
        names = {Path(f).name for f in files}
        assert "a.csv" in names
        assert "b.json" in names
        assert "c.parquet" in names
        assert "d.txt" not in names

    def test_list_nonexistent_directory(self) -> None:
        assert list_supported_files("/nonexistent/dir") == []
