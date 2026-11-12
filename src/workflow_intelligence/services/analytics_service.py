"""Deterministic analytics service for KPI computation and dimensional attribution.

All aggregations are computed by DuckDB through :class:`DuckDBAdapter`; no
LLM is ever used to derive numbers. The service provides:

- A metric calculation library (cost-per-order, expedited fees, order counts,
  expedited share, extensible to cycle time / rework / compliance).
- Period-over-period comparison with absolute and relative deltas.
- Dimension ranking that isolates the top variance contributors.
- Deterministic statistical significance indicators (standard deviation and
  z-score bounds).
- Dynamic runtime metric registration for custom business metrics (e.g. an
  Amul ``spoilage_rate``) with SQL-injection-safe expression validation.
"""

from __future__ import annotations

import re
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

# Aggregation functions permitted in custom metric expressions.
_ALLOWED_AGGREGATIONS = {"SUM", "AVG", "COUNT", "MIN", "MAX", "STDDEV"}
# SQL keywords permitted alongside aggregations in custom expressions.
_ALLOWED_KEYWORDS = {
    "CASE",
    "WHEN",
    "THEN",
    "ELSE",
    "END",
    "AND",
    "OR",
    "NOT",
    "IS",
    "NULL",
    "TRUE",
    "FALSE",
}
# Keywords that are never permitted in custom expressions (prevents injection).
_DENIED_KEYWORDS = {
    "SELECT",
    "INSERT",
    "UPDATE",
    "DELETE",
    "DROP",
    "CREATE",
    "ALTER",
    "TRUNCATE",
    "EXEC",
    "EXECUTE",
    "UNION",
    "MERGE",
    "CALL",
    "PRAGMA",
    "COPY",
    "ATTACH",
    "DETACH",
    "LOAD",
    "INSTALL",
}
# SQL operators and punctuation permitted in custom expressions.
_ALLOWED_PUNCTUATION = set("()+-*/%.,=<>!")

# Regex to strip aggregation wrappers (SUM(col) -> col) for per-row stats.
_STRIP_AGGREGATION_RE = re.compile(
    r"\b(?:SUM|AVG|COUNT|MIN|MAX|STDDEV)\s*\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*\)"
)


def _validate_custom_expression(expression: str) -> None:
    """Validate that a custom metric expression only uses safe SQL.

    Allows aggregation functions (SUM, AVG, COUNT, MIN, MAX, STDDEV), column
    identifiers, numeric literals, string literals, SQL operators, and a
    narrow set of keywords. Rejects anything that could be used for SQL
    injection (subqueries, UNION, DROP, INSERT, DELETE, UPDATE, EXEC, etc.).
    """
    if not expression or not expression.strip():
        raise AnalyticsServiceError("Custom expression must not be empty.")
    # Mask string literals so their contents are not scanned as tokens.
    sanitized = re.sub(r"'[^']*'", "''", expression)
    # Tokenize: identifiers, numbers, operators/punctuation.
    tokens = re.findall(
        r"[A-Za-z_][A-Za-z0-9_]*|\d+(?:\.\d+)?|[\(\)\+\-\*/\%,=<>!]",
        sanitized,
    )
    for token in tokens:
        upper = token.upper()
        if upper in _DENIED_KEYWORDS:
            raise AnalyticsServiceError(
                f"Disallowed SQL keyword in custom expression: {token!r}. "
                "Only SUM, AVG, COUNT, MIN, MAX, STDDEV aggregations and column "
                "references are allowed."
            )
        if upper in _ALLOWED_AGGREGATIONS or upper in _ALLOWED_KEYWORDS:
            continue
        if re.fullmatch(r"\d+(?:\.\d+)?", token):
            continue
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", token):
            # Column identifier - allowed.
            continue
        if token in _ALLOWED_PUNCTUATION:
            continue
        raise AnalyticsServiceError(
            f"Unsupported token in custom expression: {token!r}. "
            "Only SUM, AVG, COUNT, MIN, MAX, STDDEV aggregations and column "
            "references are allowed."
        )


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
        # Custom metrics registered at runtime (name -> spec).
        self._custom_metrics: dict[str, dict[str, str]] = {}

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

    def register_custom_metric(self, name: str, expression: str, column: str) -> None:
        """Register a custom metric at runtime from a DuckDB SQL expression.

        The expression is validated to contain only safe aggregation functions
        (SUM, AVG, COUNT, MIN, MAX, STDDEV) and column references, preventing
        SQL injection.

        Args:
            name: Metric identifier (e.g. ``"spoilage_rate"``).
            expression: DuckDB SQL aggregation expression, e.g.
                ``"100.0 * SUM(spoiled) / SUM(total)"``.
            column: Source column used for standard-deviation / significance
                calculations.

        Raises:
            AnalyticsServiceError: If the expression fails safety validation.
        """
        _validate_custom_expression(expression)
        self._custom_metrics[name] = {
            "column": column,
            "expression": expression,
            "description": f"Custom metric: {name}",
        }

    def _spec(self, metric_name: str) -> dict[str, str]:
        spec = METRIC_REGISTRY.get(metric_name)
        if spec is None:
            spec = self._custom_metrics.get(metric_name)
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
            metric_name: Registered metric name (built-in or custom).
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

    def compute_dynamic_metric(
        self,
        table_name: str,
        expression: str,
        where_sql: str = "",
        parameters: list[Any] | tuple[Any, ...] | None = None,
    ) -> float:
        """Evaluate an arbitrary deterministic SQL expression across a view.

        The expression is validated for SQL-injection safety before execution.

        Args:
            table_name: DuckDB table/view to query.
            expression: Safe DuckDB SQL aggregation expression.
            where_sql: Optional WHERE clause using ``?`` placeholders.
            parameters: Parameters bound to the WHERE clause.

        Returns:
            The metric value as a float (0.0 when no rows match).
        """
        _validate_custom_expression(expression)
        self._ensure_registered(table_name)
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
        timeframe_column: str,
        current_timeframe: str,
        baseline_timeframe: str,
    ) -> MetricComparison:
        """Compute a period-over-period comparison for a metric.

        Args:
            table_name: DuckDB table/view to query.
            metric_name: Registered metric name.
            timeframe_column: Column used for period filtering.
            current_timeframe: Current period identifier.
            baseline_timeframe: Baseline period identifier.

        Returns:
            A :class:`MetricComparison` with baseline, current, absolute and
            relative variance.
        """
        current = self.compute_metric(
            table_name, metric_name, f"{timeframe_column} = ?", [current_timeframe]
        )
        baseline = self.compute_metric(
            table_name, metric_name, f"{timeframe_column} = ?", [baseline_timeframe]
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
        timeframe_column: str,
        timeframe: str,
    ) -> dict[str, float]:
        expression = self._expression(metric_name)
        sql = (
            f"SELECT {dimension_col}, {expression} FROM {table_name} "
            f"WHERE {timeframe_column} = ? GROUP BY {dimension_col}"
        )
        rows = self._adapter.execute_query(
            sql, parameters=[timeframe], time_limit_ms=5000
        )
        return {
            str(dim): float(value) if value is not None else 0.0 for dim, value in rows
        }

    def _dimension_counts(
        self,
        table_name: str,
        dimension_col: str,
        timeframe_column: str,
        timeframe: str,
    ) -> dict[str, int]:
        sql = (
            f"SELECT {dimension_col}, COUNT(*) FROM {table_name} "
            f"WHERE {timeframe_column} = ? GROUP BY {dimension_col}"
        )
        rows = self._adapter.execute_query(
            sql, parameters=[timeframe], time_limit_ms=5000
        )
        return {str(dim): int(count) for dim, count in rows}

    def rank_dimensions(
        self,
        table_name: str,
        metric_name: str,
        dimension_col: str,
        current_timeframe: str,
        baseline_timeframe: str,
        timeframe_column: str = "timeframe",
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
            current_timeframe: Current period identifier.
            baseline_timeframe: Baseline period identifier.
            timeframe_column: Column used for period filtering.
            top_n: Maximum number of attributions to return.

        Returns:
            Dimension attributions sorted by contribution percentage
            descending.
        """
        self._ensure_registered(table_name)
        current_map = self._per_dimension(
            table_name, metric_name, dimension_col, timeframe_column, current_timeframe
        )
        baseline_map = self._per_dimension(
            table_name, metric_name, dimension_col, timeframe_column, baseline_timeframe
        )
        count_map = self._dimension_counts(
            table_name, dimension_col, timeframe_column, current_timeframe
        )

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

    def _strip_aggregations(self, expression: str) -> str:
        """Replace aggregation wrappers (SUM(col) -> col) for per-row stats.

        Custom metric expressions like ``100.0 * SUM(a) / SUM(b)`` are
        aggregate expressions; to compute per-row standard deviation we
        strip the aggregation wrappers, yielding
        ``100.0 * a / b``.
        """
        return _STRIP_AGGREGATION_RE.sub(r"\1", expression)

    def metric_stats(
        self,
        table_name: str,
        metric_name: str,
        timeframe_column: str,
        timeframe: str,
    ) -> dict[str, float]:
        """Return mean, standard deviation, and record count for a period.

        Used to derive statistical significance indicators for a deviation.

        For built-in metrics, stats are computed over the source column.
        For custom metrics (dynamic expressions), stats are computed over
        the per-row expression value with aggregation wrappers stripped.

        Args:
            table_name: DuckDB table/view to query.
            metric_name: Registered metric name.
            timeframe_column: Column used for period filtering.
            timeframe: Period identifier.

        Returns:
            Dictionary with keys ``mean``, ``std_dev``, ``count``.
        """
        self._ensure_registered(table_name)
        spec = self._spec(metric_name)
        is_custom = metric_name in self._custom_metrics
        column = spec["column"]
        if is_custom:
            # Per-row expression value with aggregation wrappers stripped.
            stats_expr = self._strip_aggregations(spec["expression"])
        else:
            stats_expr = column
        sql = (
            f"SELECT AVG({stats_expr}), STDDEV({stats_expr}), COUNT(*) "
            f"FROM {table_name} WHERE {timeframe_column} = ?"
        )
        try:
            rows = self._adapter.execute_query(
                sql, parameters=[timeframe], time_limit_ms=5000
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

    def infer_dimension_column(self, table_name: str) -> str:
        """Infer a categorical dimension column from DuckDB table metadata.

        Queries ``information_schema.columns`` and returns the first
        VARCHAR/TEXT column that is not the timeframe column, so callers
        can segment deviations without hardcoding ``"department"``.

        Args:
            table_name: DuckDB table/view to inspect.

        Returns:
            The name of a categorical column suitable for dimensional
            attribution, or ``"department"`` as a fallback.
        """
        self._ensure_registered(table_name)
        sql = (
            "SELECT column_name FROM information_schema.columns "
            f"WHERE table_name = '{table_name}' "
            "AND (data_type ILIKE 'varchar%' OR data_type ILIKE 'text%') "
            "ORDER BY ordinal_position"
        )
        try:
            rows = self._adapter.execute_query(sql, time_limit_ms=5000)
        except DuckDBAdapterError:
            rows = []
        for (col_name,) in rows:
            if col_name and col_name.lower() not in {"timeframe", "quarter"}:
                return str(col_name)
        return "department"

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
