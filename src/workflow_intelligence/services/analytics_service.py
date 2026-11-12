"""Deterministic analytics service for KPI computation and dimensional attribution.

All aggregations are computed by DuckDB through :class:`DuckDBAdapter`; no
LLM is ever used to derive numbers. The service provides:

- A metric calculation library (cost-per-order, expedited fees, order counts,
  expedited share, extensible to cycle time / rework / compliance).
- Period-over-period comparison with absolute and relative deltas.
- Dimension ranking that isolates the top variance contributors.
- Deterministic statistical significance indicators (standard deviation and
  z-score bounds).
"""

from __future__ import annotations

from typing import Any

from workflow_intelligence.adapters.analytics.duckdb_adapter import (
    DuckDBAdapter,
    DuckDBAdapterError,
)
from workflow_intelligence.schemas.decision import (
    DimensionAttribution,
    MetricComparison,
)


class AnalyticsServiceError(Exception):
    """Raised when analytics service operations fail."""


DEFAULT_SOURCE_URI = "sample-data/procurement_costs.csv"
DEFAULT_TABLE = "procurement_costs"
DEFAULT_CURRENT_QUARTER = "Q3"
DEFAULT_BASELINE_QUARTER = "Q2"

# Metric registry: every expression is evaluated by DuckDB. "column" is the
# source column used for standard-deviation / significance calculations.
METRIC_REGISTRY: dict[str, dict[str, str]] = {
    "processing_cost": {
        "column": "processing_cost",
        "expression": "SUM(processing_cost)",
        "description": "Total processing cost.",
    },
    "avg_processing_cost": {
        "column": "processing_cost",
        "expression": "AVG(processing_cost)",
        "description": "Average processing cost per order (cost-per-order).",
    },
    "total_expedited_fee": {
        "column": "expedited_fee",
        "expression": "SUM(expedited_fee)",
        "description": "Total expedited shipping fees.",
    },
    "avg_expedited_fee": {
        "column": "expedited_fee",
        "expression": "AVG(expedited_fee)",
        "description": "Average expedited fee per order.",
    },
    "order_count": {
        "column": "order_id",
        "expression": "COUNT(order_id)",
        "description": "Number of orders.",
    },
    "expedited_share_percentage": {
        "column": "processing_cost",
        "expression": (
            "100.0 * SUM(CASE WHEN shipping_method = 'Expedited' "
            "THEN 1 ELSE 0 END) / COUNT(*)"
        ),
        "description": "Share of orders shipped via expedited delivery.",
    },
}

_Z_SCORE_THRESHOLD = 1.96  # 95% confidence boundary


class AnalyticsService:
    """Deterministic KPI analytics backed by DuckDB."""

    def __init__(
        self,
        adapter: DuckDBAdapter | None = None,
        source_uri: str = DEFAULT_SOURCE_URI,
        table_name: str = DEFAULT_TABLE,
    ) -> None:
        self._adapter = adapter or DuckDBAdapter(":memory:")
        self._source_uri = source_uri
        self._table_name = table_name
        self._registered = False

    @property
    def adapter(self) -> DuckDBAdapter:
        return self._adapter

    @property
    def source_uri(self) -> str:
        return self._source_uri

    @property
    def table_name(self) -> str:
        return self._table_name

    def register_source(self) -> None:
        """Register the configured source CSV as a DuckDB view (idempotent)."""
        if self._registered:
            return
        try:
            self._adapter.register_csv(self._table_name, self._source_uri)
        except DuckDBAdapterError as exc:
            raise AnalyticsServiceError(str(exc)) from exc
        self._registered = True

    def use_source(self, source_uri: str, table_name: str) -> None:
        """Switch the active source file and table, resetting registration.

        Args:
            source_uri: Path to the CSV file to register.
            table_name: Logical table name to expose the data under.
        """
        self._source_uri = source_uri
        self._table_name = table_name
        self._registered = False

    def _spec(self, metric_name: str) -> dict[str, str]:
        spec = METRIC_REGISTRY.get(metric_name)
        if spec is None:
            raise AnalyticsServiceError(
                f"Unknown metric '{metric_name}'. "
                f"Available: {', '.join(sorted(METRIC_REGISTRY))}."
            )
        return spec

    def _expression(self, metric_name: str) -> str:
        return self._spec(metric_name)["expression"]

    def compute_metric(
        self,
        table_name: str,
        metric_name: str,
        where_sql: str = "",
        parameters: list[Any] | tuple[Any, ...] | None = None,
    ) -> float:
        """Compute a metric value for an optional filtered subset.

        Args:
            table_name: DuckDB table/view to query.
            metric_name: Registered metric name.
            where_sql: Optional WHERE clause using ``?`` placeholders.
            parameters: Parameters bound to the WHERE clause.

        Returns:
            The metric value as a float (0.0 when no rows match).

        Raises:
            AnalyticsServiceError: If the metric is unknown or the query fails.
        """
        self._ensure_registered(table_name)
        expression = self._expression(metric_name)
        sql = f"SELECT {expression} FROM {table_name}"
        if where_sql:
            sql += f" WHERE {where_sql}"
        try:
            rows = self._adapter.execute_query(
                sql, parameters=parameters, time_limit_ms=5000
            )
        except DuckDBAdapterError as exc:
            raise AnalyticsServiceError(str(exc)) from exc
        if not rows or rows[0][0] is None:
            return 0.0
        return float(rows[0][0])

    def _ensure_registered(self, table_name: str) -> None:
        if table_name == self._table_name and not self._registered:
            self.register_source()

    def compare_periods(
        self,
        table_name: str,
        metric_name: str,
        current_quarter: str,
        baseline_quarter: str,
    ) -> MetricComparison:
        """Compute a period-over-period comparison for a metric.

        Args:
            table_name: DuckDB table/view to query.
            metric_name: Registered metric name.
            current_quarter: Current period identifier (e.g. ``"Q3"``).
            baseline_quarter: Baseline period identifier (e.g. ``"Q2"``).

        Returns:
            A :class:`MetricComparison` with baseline, current, absolute and
            relative variance.
        """
        current = self.compute_metric(
            table_name, metric_name, "quarter = ?", [current_quarter]
        )
        baseline = self.compute_metric(
            table_name, metric_name, "quarter = ?", [baseline_quarter]
        )
        absolute_variance = current - baseline
        relative = (absolute_variance / baseline * 100.0) if baseline != 0 else 0.0
        return MetricComparison(
            metric_name=metric_name,
            baseline_value=baseline,
            current_value=current,
            absolute_variance=absolute_variance,
            relative_variance_percentage=relative,
        )

    def _per_dimension(
        self,
        table_name: str,
        metric_name: str,
        dimension_col: str,
        quarter: str,
    ) -> dict[str, float]:
        expression = self._expression(metric_name)
        sql = (
            f"SELECT {dimension_col}, {expression} FROM {table_name} "
            f"WHERE quarter = ? GROUP BY {dimension_col}"
        )
        rows = self._adapter.execute_query(
            sql, parameters=[quarter], time_limit_ms=5000
        )
        return {
            str(dim): float(value) if value is not None else 0.0 for dim, value in rows
        }

    def _dimension_counts(
        self, table_name: str, dimension_col: str, quarter: str
    ) -> dict[str, int]:
        sql = (
            f"SELECT {dimension_col}, COUNT(*) FROM {table_name} "
            f"WHERE quarter = ? GROUP BY {dimension_col}"
        )
        rows = self._adapter.execute_query(
            sql, parameters=[quarter], time_limit_ms=5000
        )
        return {str(dim): int(count) for dim, count in rows}

    def rank_dimensions(
        self,
        table_name: str,
        metric_name: str,
        dimension_col: str,
        current_quarter: str,
        baseline_quarter: str,
        top_n: int = 5,
    ) -> list[DimensionAttribution]:
        """Rank dimensions by their contribution to the period-over-period delta.

        Attributes the change to specific dimension values by computing the
        per-dimension delta between current and baseline periods, then
        expressing each dimension's absolute delta as a share of the total
        absolute delta across all dimensions.

        Args:
            table_name: DuckDB table/view to query.
            metric_name: Registered metric name.
            dimension_col: Column to segment by (e.g. ``"department"``).
            current_quarter: Current period identifier.
            baseline_quarter: Baseline period identifier.
            top_n: Maximum number of attributions to return.

        Returns:
            Dimension attributions sorted by contribution percentage
            descending.
        """
        self._ensure_registered(table_name)
        current_map = self._per_dimension(
            table_name, metric_name, dimension_col, current_quarter
        )
        baseline_map = self._per_dimension(
            table_name, metric_name, dimension_col, baseline_quarter
        )
        count_map = self._dimension_counts(table_name, dimension_col, current_quarter)

        all_dims = set(current_map) | set(baseline_map)
        deltas = {
            dim: current_map.get(dim, 0.0) - baseline_map.get(dim, 0.0)
            for dim in all_dims
        }
        total_abs = sum(abs(delta) for delta in deltas.values())

        attributions: list[DimensionAttribution] = []
        for dim, delta in deltas.items():
            contribution = (abs(delta) / total_abs * 100.0) if total_abs != 0 else 0.0
            attributions.append(
                DimensionAttribution(
                    segment_name=f"{dimension_col} = {dim}",
                    contribution_percentage=contribution,
                    record_count=count_map.get(dim, 0),
                )
            )

        attributions.sort(key=lambda a: a.contribution_percentage, reverse=True)
        return attributions[:top_n]

    def metric_stats(
        self, table_name: str, metric_name: str, quarter: str
    ) -> dict[str, float]:
        """Return mean, standard deviation, and record count for a quarter.

        Used to derive statistical significance indicators for a deviation.

        Args:
            table_name: DuckDB table/view to query.
            metric_name: Registered metric name.
            quarter: Quarter identifier.

        Returns:
            Dictionary with keys ``mean``, ``std_dev``, ``count``.
        """
        self._ensure_registered(table_name)
        spec = self._spec(metric_name)
        column = spec["column"]
        sql = (
            f"SELECT AVG({column}), STDDEV({column}), COUNT(*) "
            f"FROM {table_name} WHERE quarter = ?"
        )
        try:
            rows = self._adapter.execute_query(
                sql, parameters=[quarter], time_limit_ms=5000
            )
        except DuckDBAdapterError as exc:
            raise AnalyticsServiceError(str(exc)) from exc
        if not rows:
            return {"mean": 0.0, "std_dev": 0.0, "count": 0}
        mean, std_dev, count = rows[0]
        return {
            "mean": float(mean) if mean is not None else 0.0,
            "std_dev": float(std_dev) if std_dev is not None else 0.0,
            "count": float(count) if count is not None else 0.0,
        }

    @staticmethod
    def significance_for_delta(
        current: float, baseline: float, std_dev: float
    ) -> dict[str, float | bool]:
        """Compute a z-score and significance flag for an observed delta.

        Args:
            current: Current period value.
            baseline: Baseline period value.
            std_dev: Standard deviation of the underlying values.

        Returns:
            Dictionary with ``z_score`` and ``significant`` (bool).
        """
        delta = abs(current - baseline)
        denominator = std_dev if std_dev != 0 else 1.0
        z_score = delta / denominator
        return {
            "z_score": z_score,
            "significant": bool(z_score > _Z_SCORE_THRESHOLD),
        }
