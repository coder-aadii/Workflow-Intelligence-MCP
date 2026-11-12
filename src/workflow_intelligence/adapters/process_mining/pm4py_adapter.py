"""PM4Py process mining adapter for event log analysis."""

from __future__ import annotations

from typing import Any

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
    ) -> pd.DataFrame:
        """Format a DataFrame as a PM4Py-compatible event log.

        Renames columns to pm4py standard names, converts timestamps,
        and sorts by case and timestamp for deterministic processing.

        Args:
            df: Raw event log DataFrame.
            case_id_col: Column name for case identifiers.
            activity_col: Column name for activity names.
            timestamp_col: Column name for event timestamps.

        Returns:
            PM4Py-formatted DataFrame.
        """
        formatted = df.copy()
        formatted = formatted.rename(
            columns={
                case_id_col: "case:concept:name",
                activity_col: "concept:name",
                timestamp_col: "time:timestamp",
            }
        )
        formatted["time:timestamp"] = pd.to_datetime(formatted["time:timestamp"])
        formatted = formatted.sort_values(["case:concept:name", "time:timestamp"])
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

        Args:
            df: PM4Py-formatted event log DataFrame.

        Returns:
            Dictionary mapping (source_activity, target_activity)
            to duration stats containing mean, median, std_dev (in hours).
        """
        log = log_converter.apply(df)
        perf_dfg = dfg_algorithm.apply(log, variant=dfg_algorithm.Variants.PERFORMANCE)

        result: dict[tuple[str, str], dict[str, Any]] = {}
        for (source, target), mean_sec in perf_dfg.items():
            # PM4Py performance DFG returns mean transition duration in seconds
            # as a float value directly
            mean_sec_val = float(mean_sec)
            result[(str(source), str(target))] = {
                "mean_duration_hours": mean_sec_val / 3600.0,
                "median_duration_hours": mean_sec_val / 3600.0,
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
