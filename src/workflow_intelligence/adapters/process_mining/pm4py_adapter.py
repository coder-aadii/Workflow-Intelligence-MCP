"""PM4Py process mining adapter for event log analysis."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

try:
    import pm4py
    from pm4py.algo.discovery.dfg import algorithm as dfg_algorithm
    from pm4py.objects.conversion.log import converter as log_converter

    _PM4PY_AVAILABLE = True
except ImportError:  # pragma: no cover - pm4py optional in environments without it
    pm4py = None
    log_converter = None
    dfg_algorithm = None
    _PM4PY_AVAILABLE = False


class PM4PyAdapterError(Exception):
    """Raised when PM4Py operations fail."""


class PM4PyAdapter:
    """Adapter wrapping PM4Py process mining functions.

    Provides:
    - Event log formatting for PM4Py
    - Directly-Follows Graph (DFG) discovery
    - Performance DFG discovery with durations
    """

    def __init__(self) -> None:
        if not _PM4PY_AVAILABLE:
            raise PM4PyAdapterError(
                "pm4py is not installed. Install it with: pip install pm4py>=2.7.11"
            )

    def format_event_log(
        self,
        df: pd.DataFrame,
        case_id_col: str,
        activity_col: str,
        timestamp_col: str,
        timestamp_format: str | None = None,
    ) -> pd.DataFrame:
        """Format a DataFrame as a PM4Py-compatible event log.

        Renames columns to pm4py standard names, validates data integrity,
        converts timestamps, and sorts by case and timestamp for deterministic
        processing.

        Args:
            df: Raw event log DataFrame.
            case_id_col: Column name for case identifiers.
            activity_col: Column name for activity names.
            timestamp_col: Column name for event timestamps.
            timestamp_format: Optional timestamp format or epoch specification
                ('epoch', 'epoch_ms', 'epoch_s').

        Returns:
            PM4Py-formatted DataFrame.

        Raises:
            PM4PyAdapterError: If columns are duplicated, required columns contain
                nulls, or timestamps are invalid/unparseable or numeric without epoch config.
        """
        if len({case_id_col, activity_col, timestamp_col}) != 3:
            raise PM4PyAdapterError(
                f"Columns must be distinct for case_id, activity, and timestamp. "
                f"Received duplicate mappings: case_id='{case_id_col}', "
                f"activity='{activity_col}', timestamp='{timestamp_col}'."
            )

        if len(df) == 0:
            return pd.DataFrame(
                columns=["case:concept:name", "concept:name", "time:timestamp"]
            )

        # Validate case_id values
        case_series = df[case_id_col]
        null_cases = (
            case_series.isna()
            | (case_series.astype(str).str.strip().isin({"", "nan", "None", "NULL"}))
        )
        if null_cases.any():
            null_count = int(null_cases.sum())
            raise PM4PyAdapterError(
                f"Case ID column '{case_id_col}' contains {null_count} null or empty values "
                f"out of {len(df)} events."
            )

        # Validate activity values
        act_series = df[activity_col]
        null_acts = (
            act_series.isna()
            | (act_series.astype(str).str.strip().isin({"", "nan", "None", "NULL"}))
        )
        if null_acts.any():
            null_count = int(null_acts.sum())
            raise PM4PyAdapterError(
                f"Activity column '{activity_col}' contains {null_count} null or empty values "
                f"out of {len(df)} events."
            )

        # Validate timestamps and reject accidental numeric coercion
        ts_series = df[timestamp_col]
        if pd.api.types.is_numeric_dtype(ts_series):
            if not timestamp_format:
                raise PM4PyAdapterError(
                    f"Timestamp column '{timestamp_col}' has numeric dtype ({ts_series.dtype}). "
                    "Automatic numeric-to-datetime coercion is rejected by default to prevent treating "
                    "metrics or IDs as timestamps. Configure timestamp_format='epoch' (or 'epoch_s', 'epoch_ms') "
                    "if this column represents epoch timestamps."
                )

        try:
            if timestamp_format in ("epoch", "epoch_s", "s"):
                converted_ts = pd.to_datetime(ts_series, unit="s", errors="coerce")
            elif timestamp_format in ("epoch_ms", "ms"):
                converted_ts = pd.to_datetime(ts_series, unit="ms", errors="coerce")
            elif timestamp_format in ("epoch_ns", "ns"):
                converted_ts = pd.to_datetime(ts_series, unit="ns", errors="coerce")
            elif timestamp_format:
                converted_ts = pd.to_datetime(
                    ts_series, format=timestamp_format, errors="coerce"
                )
            else:
                converted_ts = pd.to_datetime(ts_series, errors="coerce")
        except Exception as exc:
            raise PM4PyAdapterError(
                f"Failed to parse timestamp column '{timestamp_col}': {exc}"
            ) from exc

        null_ts = converted_ts.isna()
        if null_ts.any():
            null_count = int(null_ts.sum())
            raise PM4PyAdapterError(
                f"Timestamp column '{timestamp_col}' contains {null_count} invalid or unparseable "
                f"datetime values out of {len(df)} events."
            )

        formatted = df.copy()
        formatted = formatted.rename(
            columns={
                case_id_col: "case:concept:name",
                activity_col: "concept:name",
                timestamp_col: "time:timestamp",
            }
        )
        formatted["case:concept:name"] = formatted["case:concept:name"].astype(str)
        formatted["concept:name"] = formatted["concept:name"].astype(str)
        formatted["time:timestamp"] = converted_ts
        formatted = formatted.sort_values(
            ["case:concept:name", "time:timestamp"], kind="mergesort"
        )
        return formatted

    def discover_dfg(self, df: pd.DataFrame) -> dict[tuple[str, str], int]:
        """Discover the Directly-Follows Graph from an event log.

        Args:
            df: PM4Py-formatted event log DataFrame.

        Returns:
            Dictionary mapping (source_activity, target_activity)
            to transition count.
        """
        log = log_converter.apply(df)
        dfg = dfg_algorithm.apply(log)
        return {(str(k[0]), str(k[1])): int(v) for k, v in dfg.items()}

    def discover_performance_dfg(
        self, df: pd.DataFrame
    ) -> dict[tuple[str, str], dict[str, Any]]:
        """Discover the performance DFG with duration statistics.

        Computes mean, median, and population standard deviation from individual
        case transition durations using vectorized adjacent difference computation.

        Args:
            df: PM4Py-formatted event log DataFrame.

        Returns:
            Dictionary mapping (source_activity, target_activity)
            to duration stats containing mean, median, std_dev (in hours).
        """
        if df.empty or len(df) < 2:
            return {}

        log = log_converter.apply(df)
        perf_dfg = dfg_algorithm.apply(log, variant=dfg_algorithm.Variants.PERFORMANCE)

        # Vectorized calculation of transition durations preserving within-case sequence
        sorted_df = df.sort_values(
            ["case:concept:name", "time:timestamp"], kind="mergesort"
        )
        time_s = pd.to_datetime(sorted_df["time:timestamp"])
        dur_hours = time_s.diff().dt.total_seconds() / 3600.0
        case_arr = sorted_df["case:concept:name"].astype(str).to_numpy()
        act_arr = sorted_df["concept:name"].astype(str).to_numpy()
        dur_arr = dur_hours.to_numpy()

        # Valid transitions occur between consecutive rows within the same case
        mask = case_arr[1:] == case_arr[:-1]
        src_valid = act_arr[:-1][mask]
        tgt_valid = act_arr[1:][mask]
        dur_valid = dur_arr[1:][mask]

        stats_map: dict[tuple[str, str], tuple[float, float, float]] = {}
        if len(src_valid) > 0:
            trans_df = pd.DataFrame(
                {"src": src_valid, "tgt": tgt_valid, "dur": dur_valid}
            )
            agg_df = trans_df.groupby(["src", "tgt"])["dur"].agg(
                mean="mean",
                median="median",
                std=lambda x: float(np.std(x, ddof=0)),
            )
            for (s, t), row in agg_df.iterrows():
                stats_map[(str(s), str(t))] = (
                    float(row["mean"]),
                    float(row["median"]),
                    float(row["std"]),
                )

        result: dict[tuple[str, str], dict[str, Any]] = {}
        for (source, target), mean_sec in perf_dfg.items():
            key = (str(source), str(target))
            mean_hrs = float(mean_sec) / 3600.0

            if key in stats_map:
                s_mean, s_median, s_std = stats_map[key]
                result[key] = {
                    "mean_duration_hours": s_mean,
                    "median_duration_hours": s_median,
                    "std_dev_hours": s_std,
                }
            else:
                result[key] = {
                    "mean_duration_hours": mean_hrs,
                    "median_duration_hours": mean_hrs,
                    "std_dev_hours": 0.0,
                }
        return result

    def discover_variants(self, df: pd.DataFrame) -> dict[str, int]:
        """Discover process variants and their frequencies.

        Computes variants from case traces directly since pm4py 2.7.x
        does not expose a standalone variants discovery function.

        Args:
            df: PM4Py-formatted event log DataFrame.

        Returns:
            Dictionary mapping variant string (activities joined by ' -> ')
            to count.
        """
        variants: dict[str, int] = {}
        for _, case_group in df.groupby("case:concept:name"):
            trace = tuple(
                case_group.sort_values("time:timestamp")["concept:name"].tolist()
            )
            variant = " -> ".join(trace)
            variants[variant] = variants.get(variant, 0) + 1
        return variants
