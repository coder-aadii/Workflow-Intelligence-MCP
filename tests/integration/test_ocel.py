"""Integration tests for OCEL (Object-Centric Event Log) support.

Verifies that the loader can read OCEL files (.ocel, .jsonocel) using pm4py,
and that all process discovery tools work against OCEL data with zero
hardcoded assumptions.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from workflow_intelligence.adapters.file_storage.loader import (
    SUPPORTED_EXTENSIONS,
    _load_ocel,
    _ocel_column_map,
    get_event_log_columns,
    list_supported_files,
    load_event_log,
)
from workflow_intelligence.schemas.process import (
    AutomationScoreRequest,
    BottleneckRequest,
    ProcessAnalyzeRequest,
)
from workflow_intelligence.services.process_service import ProcessService

OCEL_FILE = Path(__file__).resolve().parents[2] / "sample-data" / "test_ocel.jsonocel"


class TestOcelLoader:
    """Tests for the OCEL file loader."""

    def test_ocel_supported_extension(self) -> None:
        """OCEL extensions are in the supported set."""
        assert ".ocel" in SUPPORTED_EXTENSIONS
        assert ".jsonocel" in SUPPORTED_EXTENSIONS

    def test_load_ocel_returns_dataframe(self) -> None:
        """_load_ocel returns a non-empty DataFrame."""
        df = _load_ocel(OCEL_FILE)
        assert isinstance(df, pd.DataFrame)
        assert len(df) == 5
        assert "ocel:activity" in df.columns
        assert "ocel:timestamp" in df.columns

    def test_load_ocel_flattens_object_lists(self) -> None:
        """Object ID list columns are flattened to single values."""
        df = _load_ocel(OCEL_FILE)
        object_cols = [c for c in df.columns if c.startswith("ocel:type:")]
        assert len(object_cols) == 1
        # Values should be scalars, not lists
        for val in df[object_cols[0]]:
            assert not isinstance(val, list)

    def test_ocel_column_map(self) -> None:
        """_ocel_column_map maps standard names to OCEL columns."""
        df = _load_ocel(OCEL_FILE)
        mapping = _ocel_column_map(df)
        assert mapping["activity"] == "ocel:activity"
        assert mapping["timestamp"] == "ocel:timestamp"
        assert "case_id" in mapping

    def test_get_event_log_columns_ocel(self) -> None:
        """get_event_log_columns works for OCEL files."""
        cols = get_event_log_columns(str(OCEL_FILE))
        assert "ocel:activity" in cols
        assert "ocel:timestamp" in cols

    def test_load_event_log_ocel_auto_map(self) -> None:
        """load_event_log auto-maps default column names to OCEL columns."""
        df = load_event_log(
            str(OCEL_FILE),
            case_id_col="case_id",
            activity_col="activity",
            timestamp_col="timestamp",
        )
        assert len(df) == 5
        assert "ocel:type:order" in df.columns  # case_id mapped to ocel:type:order
        assert "ocel:activity" in df.columns
        assert "ocel:timestamp" in df.columns

    def test_load_event_log_ocel_explicit_columns(self) -> None:
        """load_event_log works with explicit OCEL column names."""
        df = load_event_log(
            str(OCEL_FILE),
            case_id_col="ocel:type:order",
            activity_col="ocel:activity",
            timestamp_col="ocel:timestamp",
        )
        assert len(df) == 5
        assert df["ocel:type:order"].iloc[0] == "order1"
        assert df["ocel:activity"].iloc[0] == "Receive Order"

    def test_list_supported_files_includes_ocel(self) -> None:
        """list_supported_files finds OCEL files in sample-data."""
        files = list_supported_files(
            str(Path(__file__).resolve().parents[2] / "sample-data")
        )
        ocel_files = [f for f in files if f.endswith((".ocel", ".jsonocel"))]
        assert len(ocel_files) >= 1


class TestOcelProcessDiscovery:
    """Tests for process discovery on OCEL data."""

    def test_analyze_business_process_ocel(self) -> None:
        """Process discovery works on OCEL event logs."""
        svc = ProcessService()
        result = svc.analyze_business_process(
            ProcessAnalyzeRequest(
                source_uri=str(OCEL_FILE),
                case_id_col="ocel:type:order",
                activity_col="ocel:activity",
                timestamp_col="ocel:timestamp",
            )
        )
        assert result.success is True
        assert result.total_cases == 2
        assert result.total_events == 5
        assert result.variant_count >= 1
        assert "Receive Order" in result.unique_activities
        assert "Ship Order" in result.unique_activities

    def test_identify_bottlenecks_ocel_auto_detect(self) -> None:
        """Bottleneck detection works on OCEL with auto column detection."""
        svc = ProcessService()
        report = svc.identify_bottlenecks(
            BottleneckRequest(source_uri=str(OCEL_FILE), threshold_std_dev=2.0)
        )
        assert report.success is True
        assert report.total_stages_analyzed >= 1

    def test_score_automation_opportunities_ocel(self) -> None:
        """Automation scoring works on OCEL event logs."""
        svc = ProcessService()
        score = svc.score_automation_opportunities(
            AutomationScoreRequest(source_uri=str(OCEL_FILE), min_frequency=1)
        )
        assert score.success is True
        assert len(score.candidates) >= 1
        assert all(c.rank >= 1 for c in score.candidates)
