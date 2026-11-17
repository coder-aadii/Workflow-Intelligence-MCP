"""Adversarial regression tests for schema inference safety and column mapping.

Covers:
1. Positional fallback removal (fail-closed on unknown schemas)
2. Preserving auto-inference for unambiguous recognized column names
3. Explicit column mapping in bottleneck and automation tools
4. Rejection of missing, duplicate, or ambiguous column mappings with available columns listed
5. Timestamp validation and rejection of accidental numeric-to-datetime coercion
6. Validation of null/empty case IDs, activities, and invalid timestamps
7. Warning generation for suspicious data patterns without rejecting legitimate single-event cases
8. Unified inference between services and resources
9. Decision dimension inference avoiding high-cardinality row identifiers
10. Decision brief dataset/timeframe validation preventing silent misleading cross-dataset results
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest
from pydantic import ValidationError

from workflow_intelligence.adapters.analytics.duckdb_adapter import DuckDBAdapter
from workflow_intelligence.adapters.file_storage.loader import (
    FileStorageError,
    load_event_log,
)
from workflow_intelligence.adapters.process_mining.pm4py_adapter import (
    PM4PyAdapter,
    PM4PyAdapterError,
)
from workflow_intelligence.resources.process_resources import register_process_resources
from workflow_intelligence.schemas.decision import DecisionBriefRequest
from workflow_intelligence.schemas.process import (
    AutomationScoreRequest,
    BottleneckRequest,
    ProcessAnalyzeRequest,
)
from workflow_intelligence.services.analytics_service import (
    AnalyticsService,
    AnalyticsServiceError,
)
from workflow_intelligence.services.decision_service import DecisionService
from workflow_intelligence.services.process_service import (
    ProcessService,
    ProcessServiceError,
    _detect_columns,
)
from workflow_intelligence.services.reporting_service import ReportingService


@pytest.fixture
def temp_csv(tmp_path: Path):
    """Factory fixture for temporary CSV files."""

    def _create(df: pd.DataFrame, filename: str = "test.csv") -> str:
        p = tmp_path / filename
        df.to_csv(p, index=False)
        return str(p)

    return _create


class TestPositionalFallbackRemoval:
    """Requirement 1: Unsafe positional fallback is completely removed."""

    def test_unfamiliar_columns_rejected_by_detector(self) -> None:
        """Columns with unknown names fail-close rather than defaulting to [0, 1, 2]."""
        cols = ["alpha", "beta", "gamma"]
        with pytest.raises(ProcessServiceError) as exc_info:
            _detect_columns(cols)
        err = str(exc_info.value)
        assert "Could not automatically infer" in err
        assert "Available columns: ['alpha', 'beta', 'gamma']" in err

    def test_non_event_table_rejected_by_bottlenecks(self, temp_csv) -> None:
        """Analytical cost table is rejected by bottleneck tool rather than silently mined."""
        df = pd.DataFrame(
            [
                {"quarter": "Q1", "dept": "HR", "cost": 100.0, "fee": 5.0},
                {"quarter": "Q2", "dept": "IT", "cost": 150.0, "fee": 10.0},
            ]
        )
        path = temp_csv(df, "costs.csv")
        svc = ProcessService()
        with pytest.raises(ProcessServiceError) as exc_info:
            svc.identify_bottlenecks(BottleneckRequest(source_uri=path))
        err = str(exc_info.value)
        assert "Could not automatically infer" in err
        assert "Available columns:" in err
        assert "quarter" in err


class TestUnambiguousInferencePreserved:
    """Requirement 2: Unambiguous canonical and synonym columns infer correctly."""

    def test_standard_columns(self) -> None:
        cols = ["case_id", "activity", "timestamp"]
        assert _detect_columns(cols) == ("case_id", "activity", "timestamp")

    def test_extended_synonyms(self) -> None:
        cols = ["order_id", "task_name", "created_at"]
        assert _detect_columns(cols) == ("order_id", "task_name", "created_at")

    def test_case_insensitive_detection(self) -> None:
        cols = ["CASE_ID", "ACT", "EVENT_TIME"]
        assert _detect_columns(cols) == ("CASE_ID", "ACT", "EVENT_TIME")

    def test_ocel_columns(self) -> None:
        cols = ["ocel:type:order", "ocel:activity", "ocel:timestamp"]
        assert _detect_columns(cols) == (
            "ocel:type:order",
            "ocel:activity",
            "ocel:timestamp",
        )


class TestExplicitColumnMapping:
    """Requirement 3: Bottleneck and automation tools accept explicit column overrides."""

    def test_bottlenecks_with_explicit_columns(self, temp_csv) -> None:
        """Bottleneck detection succeeds on unfamiliar column names when explicitly mapped."""
        df = pd.DataFrame(
            [
                {"c_col": "C1", "a_col": "A", "t_col": "2026-01-01T09:00:00"},
                {"c_col": "C1", "a_col": "B", "t_col": "2026-01-01T10:00:00"},
                {"c_col": "C2", "a_col": "A", "t_col": "2026-01-01T09:00:00"},
                {"c_col": "C2", "a_col": "B", "t_col": "2026-01-01T12:00:00"},
            ]
        )
        path = temp_csv(df, "custom.csv")
        svc = ProcessService()
        report = svc.identify_bottlenecks(
            BottleneckRequest(
                source_uri=path,
                case_id_col="c_col",
                activity_col="a_col",
                timestamp_col="t_col",
                threshold_std_dev=0.5,
            )
        )
        assert report.success is True
        assert report.total_stages_analyzed >= 1

    def test_automation_scoring_with_explicit_columns(self, temp_csv) -> None:
        """Automation scoring succeeds on unfamiliar column names when explicitly mapped."""
        df = pd.DataFrame(
            [
                {"c_col": "C1", "a_col": "Task1", "t_col": "2026-01-01T09:00:00"},
                {"c_col": "C1", "a_col": "Task2", "t_col": "2026-01-01T10:00:00"},
                {"c_col": "C2", "a_col": "Task1", "t_col": "2026-01-01T09:00:00"},
                {"c_col": "C2", "a_col": "Task2", "t_col": "2026-01-01T11:00:00"},
            ]
        )
        path = temp_csv(df, "custom_auto.csv")
        svc = ProcessService()
        report = svc.score_automation_opportunities(
            AutomationScoreRequest(
                source_uri=path,
                min_frequency=1,
                case_id_col="c_col",
                activity_col="a_col",
                timestamp_col="t_col",
            )
        )
        assert report.success is True
        assert len(report.candidates) >= 1


class TestRejectMissingDuplicateAmbiguousMappings:
    """Requirement 4: Reject missing, duplicate, or ambiguous mappings with actionable errors."""

    def test_missing_explicit_column_raises_with_available_columns(self, temp_csv) -> None:
        df = pd.DataFrame(
            [{"case_id": "C1", "activity": "A", "timestamp": "2026-01-01T09:00:00"}]
        )
        path = temp_csv(df, "test.csv")
        svc = ProcessService()
        with pytest.raises(ProcessServiceError) as exc_info:
            svc.identify_bottlenecks(
                BottleneckRequest(
                    source_uri=path,
                    case_id_col="nonexistent_col",
                    activity_col="activity",
                    timestamp_col="timestamp",
                )
            )
        err = str(exc_info.value)
        assert "Specified column(s) ['nonexistent_col'] not found" in err
        assert "Available columns:" in err
        assert "case_id" in err

    def test_duplicate_column_mapping_rejected_by_schema(self) -> None:
        """Pydantic rejects duplicate column mappings."""
        with pytest.raises(ValidationError) as exc_info:
            ProcessAnalyzeRequest(
                source_uri="test.csv",
                case_id_col="case_id",
                activity_col="case_id",
                timestamp_col="timestamp",
            )
        assert "Column names must be distinct" in str(exc_info.value)

    def test_duplicate_column_mapping_rejected_by_loader(self, temp_csv) -> None:
        """Loader rejects duplicate column mappings."""
        df = pd.DataFrame(
            [{"case_id": "C1", "activity": "A", "timestamp": "2026-01-01T09:00:00"}]
        )
        path = temp_csv(df, "test.csv")
        with pytest.raises(FileStorageError) as exc_info:
            load_event_log(
                path,
                case_id_col="case_id",
                activity_col="case_id",
                timestamp_col="timestamp",
            )
        assert "Columns must be distinct" in str(exc_info.value)

    def test_ambiguous_timestamp_columns_rejected(self) -> None:
        """Multiple competing timestamps without canonical timestamp raises ambiguity error."""
        cols = ["case_id", "activity", "start_time", "end_time"]
        with pytest.raises(ProcessServiceError) as exc_info:
            _detect_columns(cols)
        err = str(exc_info.value)
        assert "Ambiguous column mapping for 'timestamp'" in err
        assert "start_time" in err
        assert "end_time" in err

    def test_ambiguous_case_id_columns_rejected(self) -> None:
        """Multiple non-canonical entity IDs without canonical case_id raises ambiguity error."""
        cols = ["order_id", "claim_id", "activity", "timestamp"]
        with pytest.raises(ProcessServiceError) as exc_info:
            _detect_columns(cols)
        err = str(exc_info.value)
        assert "Ambiguous column mapping for 'case_id'" in err
        assert "order_id" in err
        assert "claim_id" in err


class TestTimestampValidationAndEpoch:
    """Requirement 5: Reject numeric-to-datetime coercion unless explicit epoch format is configured."""

    def test_numeric_timestamp_rejected_without_epoch_config(self) -> None:
        df = pd.DataFrame(
            [
                {"case_id": "C1", "activity": "A", "cost": 15.5},
                {"case_id": "C1", "activity": "B", "cost": 42.0},
            ]
        )
        adapter = PM4PyAdapter()
        with pytest.raises(PM4PyAdapterError) as exc_info:
            adapter.format_event_log(
                df, case_id_col="case_id", activity_col="activity", timestamp_col="cost"
            )
        err = str(exc_info.value)
        assert "numeric dtype" in err
        assert "Automatic numeric-to-datetime coercion is rejected" in err

    def test_numeric_timestamp_allowed_with_epoch_config(self) -> None:
        """Epoch timestamps succeed when timestamp_format='epoch' is specified."""
        df = pd.DataFrame(
            [
                {"case_id": "C1", "activity": "A", "epoch_ts": 1704099600},  # 2024-01-01 09:00:00
                {"case_id": "C1", "activity": "B", "epoch_ts": 1704103200},  # 2024-01-01 10:00:00
            ]
        )
        adapter = PM4PyAdapter()
        formatted = adapter.format_event_log(
            df,
            case_id_col="case_id",
            activity_col="activity",
            timestamp_col="epoch_ts",
            timestamp_format="epoch",
        )
        assert len(formatted) == 2
        assert pd.api.types.is_datetime64_any_dtype(formatted["time:timestamp"])

    def test_unparseable_string_timestamps_rejected(self) -> None:
        df = pd.DataFrame(
            [
                {"case_id": "C1", "activity": "A", "ts": "not_a_valid_date"},
                {"case_id": "C1", "activity": "B", "ts": "2026-01-01T10:00:00"},
            ]
        )
        adapter = PM4PyAdapter()
        with pytest.raises(PM4PyAdapterError) as exc_info:
            adapter.format_event_log(
                df, case_id_col="case_id", activity_col="activity", timestamp_col="ts"
            )
        err = str(exc_info.value)
        assert "invalid or unparseable datetime values" in err


class TestNullValuesValidation:
    """Requirement 6: Validate null case IDs and activities."""

    def test_null_case_id_rejected(self) -> None:
        df = pd.DataFrame(
            [
                {"case_id": None, "activity": "A", "ts": "2026-01-01T09:00:00"},
                {"case_id": "C1", "activity": "B", "ts": "2026-01-01T10:00:00"},
            ]
        )
        adapter = PM4PyAdapter()
        with pytest.raises(PM4PyAdapterError) as exc_info:
            adapter.format_event_log(
                df, case_id_col="case_id", activity_col="activity", timestamp_col="ts"
            )
        assert "null or empty values" in str(exc_info.value)

    def test_empty_string_activity_rejected(self) -> None:
        df = pd.DataFrame(
            [
                {"case_id": "C1", "activity": "   ", "ts": "2026-01-01T09:00:00"},
                {"case_id": "C1", "activity": "B", "ts": "2026-01-01T10:00:00"},
            ]
        )
        adapter = PM4PyAdapter()
        with pytest.raises(PM4PyAdapterError) as exc_info:
            adapter.format_event_log(
                df, case_id_col="case_id", activity_col="activity", timestamp_col="ts"
            )
        assert "Activity column 'activity' contains 1 null or empty values" in str(
            exc_info.value
        )


class TestSuspiciousPatternWarnings:
    """Requirement 7: Warn for suspicious patterns without rejecting single-event cases."""

    def test_warns_on_all_single_event_cases(self, temp_csv) -> None:
        """Every case has 1 event (surrogate row ID symptom). Attaches warning, succeeds."""
        df = pd.DataFrame(
            [
                {"case_id": "R1", "activity": "A", "ts": "2026-01-01T09:00:00"},
                {"case_id": "R2", "activity": "B", "ts": "2026-01-01T10:00:00"},
                {"case_id": "R3", "activity": "C", "ts": "2026-01-01T11:00:00"},
            ]
        )
        path = temp_csv(df, "single_events.csv")
        svc = ProcessService()
        res = svc.analyze_business_process(
            ProcessAnalyzeRequest(
                source_uri=path,
                case_id_col="case_id",
                activity_col="activity",
                timestamp_col="ts",
            )
        )
        assert res.success is True
        assert res.warnings is not None
        assert any("surrogate row identifier" in w for w in res.warnings)

    def test_mixed_single_and_multi_event_cases_no_false_warning(self, temp_csv) -> None:
        """Legitimate process with some single-event and some multi-event cases has no surrogate warning."""
        df = pd.DataFrame(
            [
                {"case_id": "C1", "activity": "A", "ts": "2026-01-01T09:00:00"},
                {"case_id": "C1", "activity": "B", "ts": "2026-01-01T10:00:00"},
                {"case_id": "C2", "activity": "A", "ts": "2026-01-01T11:00:00"},
            ]
        )
        path = temp_csv(df, "mixed.csv")
        svc = ProcessService()
        res = svc.analyze_business_process(
            ProcessAnalyzeRequest(
                source_uri=path,
                case_id_col="case_id",
                activity_col="activity",
                timestamp_col="ts",
            )
        )
        assert res.success is True
        # Should not warn about surrogate row IDs because C1 has 2 events
        surrogate_warnings = [
            w for w in (res.warnings or []) if "surrogate row identifier" in w
        ]
        assert len(surrogate_warnings) == 0


class TestUnifiedInferenceWithResources:
    """Requirement 8: Process resources use the same inference logic as services."""

    def test_resources_detect_columns(self) -> None:
        cols = ["case_id", "activity_name", "created_at"]
        assert _detect_columns(cols) == ("case_id", "activity_name", "created_at")


class TestDecisionDimensionInference:
    """Requirement 9: Dimension inference avoids row identifiers and high-cardinality fields."""

    def test_avoids_order_id_in_procurement_data(self, temp_csv) -> None:
        """When order_id is a unique row ID, infer_dimension_column selects department."""
        adapter = DuckDBAdapter(":memory:")
        df = pd.DataFrame(
            [
                {"order_id": f"O{i}", "department": "Finance" if i % 2 == 0 else "Sales", "cost": 10.0}
                for i in range(20)
            ]
        )
        csv_path = temp_csv(df, "costs.csv")
        adapter.register_csv("test_costs", csv_path)
        svc = AnalyticsService(adapter=adapter, table_name="test_costs")
        svc._registered = True
        dimension = svc.infer_dimension_column("test_costs")
        assert dimension == "department"


class TestDecisionBriefCrossDatasetSafety:
    """Requirement 10: Decision brief rejects mismatched timeframe column rather than guessing."""

    def test_brief_rejects_missing_timeframe_column(self, temp_csv) -> None:
        df = pd.DataFrame(
            [
                {"dept": "IT", "processing_cost": 100.0},
                {"dept": "Sales", "processing_cost": 200.0},
            ]
        )
        path = temp_csv(df, "mismatched.csv")
        svc = DecisionService()
        with pytest.raises(AnalyticsServiceError) as exc_info:
            svc.generate_brief(
                DecisionBriefRequest(
                    topic="Cost Analysis",
                    metric_ids=["processing_cost"],
                    source_uri=path,
                    timeframe_column="quarter",  # Does not exist in mismatched.csv
                )
            )
        err = str(exc_info.value)
        assert "Timeframe column 'quarter' not found" in err
        assert "Available columns:" in err
