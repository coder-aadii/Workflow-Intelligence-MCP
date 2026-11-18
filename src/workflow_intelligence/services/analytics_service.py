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

import math
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
# Keywords that are never permitted in custom expressions (prevents injection / escape).
_DENIED_KEYWORDS = {
    # DDL / DML / Admin statements
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
    "SET",
    "SHOW",
    "DESCRIBE",
    "EXPLAIN",
    "EXPORT",
    "IMPORT",
    "CHECKPOINT",
    # Clauses disallowed in scalar metric formulas
    "FROM",
    "WHERE",
    "JOIN",
    "GROUP",
    "ORDER",
    "HAVING",
    "LIMIT",
    "OFFSET",
    "WITH",
    "INTO",
    "VALUES",
    "TABLE",
    "DATABASE",
    "SCHEMA",
    "VIEW",
    "INDEX",
    "TRIGGER",
    "FUNCTION",
    "PROCEDURE",
    "DECLARE",
    # Potentially dangerous DuckDB functions
    "SYSTEM",
    "GETENV",
    "CURRENT_SETTING",
    "READ_CSV",
    "READ_PARQUET",
    "READ_JSON",
    "WRITE_CSV",
    "TO_CSV",
    "QUERY",
    "VERSION",
}
# SQL operators and punctuation permitted in custom expressions.
_ALLOWED_PUNCTUATION = set("()+-*/%.,=<>!")
_ALLOWED_CHARS = set(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_ ()+-*/%.,=<>!\t\n\r"
)

# Regex to strip aggregation wrappers (SUM(col) -> col) for per-row stats.
_STRIP_AGGREGATION_RE = re.compile(
    r"\b(?:SUM|AVG|COUNT|MIN|MAX|STDDEV)\s*\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*\)"
)


def _validate_custom_expression(expression: str) -> None:
    """Validate that a custom metric expression only uses safe SQL.

    Every single character and SQL token is strictly validated. Rejects semicolons,
    comments (-- or /* */ or #), unexpected punctuation, unclosed strings, unbalanced
    parentheses, unsupported operators, subqueries, and unauthorized keywords or functions.
    """
    if not expression or not expression.strip():
        raise AnalyticsServiceError("Custom expression must not be empty.")

    # 1. Reject comments, semicolons, and escape sequences upfront
    if ";" in expression:
        raise AnalyticsServiceError("Semicolons are forbidden in custom expressions.")
    if (
        "--" in expression
        or "/*" in expression
        or "*/" in expression
        or "#" in expression
    ):
        raise AnalyticsServiceError("SQL comments are forbidden in custom expressions.")
    if "\\" in expression or "`" in expression or "\x00" in expression:
        raise AnalyticsServiceError(
            "Disallowed escape or control characters in custom expression."
        )

    # Reject unsupported compound operators
    for op in ("<<", ">>", "->", "||", "::", ".."):
        if op in expression:
            raise AnalyticsServiceError(
                f"Unsupported operator in custom expression: {op!r}."
            )

    # 2. Validate string literal quotes and mask them out
    # Odd number of quotes indicates unclosed literal
    if expression.count("'") % 2 != 0:
        raise AnalyticsServiceError(
            "Unclosed or malformed string literal in custom expression."
        )

    # Replace valid SQL string literals (allowing escaped single quotes '') with dummy token
    sanitized = re.sub(r"'(?:''|[^'])*'", "__LITERAL__", expression)
    if "'" in sanitized:
        raise AnalyticsServiceError("Malformed string literal in custom expression.")

    # 3. Character-level validation: every remaining character must belong to allowed set
    for char in sanitized:
        if char not in _ALLOWED_CHARS:
            raise AnalyticsServiceError(
                f"Disallowed character or operator in custom expression: {char!r}. "
                "Only standard arithmetic operators, comparisons, and aggregations are permitted."
            )

    # 4. Check balanced parentheses
    depth = 0
    for char in sanitized:
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth < 0:
                raise AnalyticsServiceError(
                    "Unbalanced parentheses in custom expression."
                )
    if depth != 0:
        raise AnalyticsServiceError("Unbalanced parentheses in custom expression.")

    # 5. Full tokenization without leaving uninspected characters
    tokens = re.findall(
        r"[A-Za-z_][A-Za-z0-9_]*|\d+(?:\.\d+)?|[\(\)\+\-\*/\%,=<>!]",
        sanitized,
    )
    # Ensure all non-whitespace characters were tokenized (no gaps or ignored syntax)
    reconstructed = re.sub(r"\s+", "", sanitized)
    if "".join(tokens) != reconstructed:
        raise AnalyticsServiceError(
            "Custom expression contains unsupported or malformed syntax."
        )

    # 6. Validate every token
    for token in tokens:
        if token == "__LITERAL__":
            continue
        upper = token.upper()
        if upper in _DENIED_KEYWORDS:
            raise AnalyticsServiceError(
                f"Disallowed SQL keyword in custom expression: {token!r}. "
                "Only SUM, AVG, COUNT, MIN, MAX, STDDEV aggregations and column "
                "references are allowed."
            )
        if upper.startswith(("READ_", "WRITE_", "DUCKDB_", "PRAGMA_")):
            raise AnalyticsServiceError(
                f"Disallowed function call in custom expression: {token!r}."
            )
        if upper in _ALLOWED_AGGREGATIONS or upper in _ALLOWED_KEYWORDS:
            continue
        if re.fullmatch(r"\d+(?:\.\d+)?", token):
            continue
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", token):
            # Column identifier
            continue
        if token in _ALLOWED_PUNCTUATION:
            continue
        raise AnalyticsServiceError(
            f"Unsupported token in custom expression: {token!r}. "
            "Only SUM, AVG, COUNT, MIN, MAX, STDDEV aggregations and column "
            "references are allowed."
        )


_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def validate_sql_identifier(identifier: str, field_name: str = "identifier") -> str:
    """Validate that a string is a safe SQL identifier."""
    if not isinstance(identifier, str) or not _IDENTIFIER_PATTERN.fullmatch(identifier):
        raise AnalyticsServiceError(
            f"Invalid SQL {field_name}: {identifier!r}. "
            "Identifiers must start with a letter or underscore and contain only alphanumeric characters and underscores."
        )
    return identifier


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
        validate_sql_identifier(table_name, "table_name")
        self._ensure_registered(table_name)
        expression = self._expression(metric_name)
        sql = f'SELECT {expression} FROM "{table_name}"'
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
        validate_sql_identifier(table_name, "table_name")
        self._ensure_registered(table_name)
        sql = f'SELECT {expression} FROM "{table_name}"'
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

    def get_table_columns(self, table_name: str) -> list[str]:
        """Return the column names of a registered table."""
        validate_sql_identifier(table_name, "table_name")
        self._ensure_registered(table_name)
        rows = self._adapter.execute_query(
            "SELECT column_name FROM information_schema.columns "
            f"WHERE table_name = '{table_name}' ORDER BY ordinal_position"
        )
        return [str(r[0]) for r in rows]

    def _ensure_registered(self, table_name: str) -> None:
        validate_sql_identifier(table_name, "table_name")
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
        validate_sql_identifier(table_name, "table_name")
        validate_sql_identifier(timeframe_column, "timeframe_column")
        current = self.compute_metric(
            table_name, metric_name, f'"{timeframe_column}" = ?', [current_timeframe]
        )
        baseline = self.compute_metric(
            table_name, metric_name, f'"{timeframe_column}" = ?', [baseline_timeframe]
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
        validate_sql_identifier(table_name, "table_name")
        validate_sql_identifier(dimension_col, "dimension")
        validate_sql_identifier(timeframe_column, "timeframe_column")
        expression = self._expression(metric_name)
        sql = (
            f'SELECT "{dimension_col}", {expression} FROM "{table_name}" '
            f'WHERE "{timeframe_column}" = ? GROUP BY "{dimension_col}"'
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
        validate_sql_identifier(table_name, "table_name")
        validate_sql_identifier(dimension_col, "dimension")
        validate_sql_identifier(timeframe_column, "timeframe_column")
        sql = (
            f'SELECT "{dimension_col}", COUNT(*) FROM "{table_name}" '
            f'WHERE "{timeframe_column}" = ? GROUP BY "{dimension_col}"'
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
        per-dimension signed delta between current and baseline periods, then
        expressing each dimension's signed delta as a net contribution of the
        total net delta (allowing negative and >100% values), along with its
        gross absolute-change share.

        Args:
            table_name: DuckDB table/view to query.
            metric_name: Registered metric name.
            dimension_col: Column to segment by (e.g. ``"department"``).
            current_timeframe: Current period identifier.
            baseline_timeframe: Baseline period identifier.
            timeframe_column: Column used for period filtering.
            top_n: Maximum number of attributions to return.

        Returns:
            Dimension attributions sorted by absolute delta descending.
        """
        validate_sql_identifier(table_name, "table_name")
        validate_sql_identifier(dimension_col, "dimension")
        validate_sql_identifier(timeframe_column, "timeframe_column")
        self._ensure_registered(table_name)

        available_cols = self.get_table_columns(table_name)
        if available_cols:
            if dimension_col not in available_cols:
                raise AnalyticsServiceError(
                    f"Dimension column '{dimension_col}' not found in '{table_name}'. "
                    f"Available columns: {available_cols}."
                )
            if timeframe_column not in available_cols:
                raise AnalyticsServiceError(
                    f"Timeframe column '{timeframe_column}' not found in '{table_name}'. "
                    f"Available columns: {available_cols}."
                )
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
        total_net_delta = sum(deltas.values())
        total_abs_delta = sum(abs(delta) for delta in deltas.values())

        attributions: list[DimensionAttribution] = []
        for dim, delta in deltas.items():
            if abs(total_net_delta) >= 1e-9:
                net_contribution = (delta / total_net_delta) * 100.0
            else:
                net_contribution = 0.0
            gross_share = (
                (abs(delta) / total_abs_delta * 100.0) if total_abs_delta > 0 else 0.0
            )
            attributions.append(
                DimensionAttribution(
                    segment_name=f"{dimension_col} = {dim}",
                    contribution_percentage=net_contribution,
                    record_count=count_map.get(dim, 0),
                    delta=delta,
                    gross_share_percentage=gross_share,
                )
            )

        attributions.sort(key=lambda a: abs(a.delta), reverse=True)
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
        validate_sql_identifier(table_name, "table_name")
        validate_sql_identifier(timeframe_column, "timeframe_column")
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
            f'SELECT AVG({stats_expr}), STDDEV({stats_expr}), COUNT(*) '
            f'FROM "{table_name}" WHERE "{timeframe_column}" = ?'
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

    def infer_dimension_column(
        self, table_name: str, timeframe_column: str = "timeframe"
    ) -> str:
        """Infer a categorical dimension column from DuckDB table metadata.

        Queries ``information_schema.columns`` and evaluates candidate
        VARCHAR/TEXT columns, filtering out timeframe columns and high-cardinality
        row identifiers (e.g. 100% unique primary keys) to segment deviations
        intelligently.

        Args:
            table_name: DuckDB table/view to inspect.
            timeframe_column: The timeframe column to exclude from dimension candidates.

        Returns:
            The name of a categorical column suitable for dimensional
            attribution.

        Raises:
            AnalyticsServiceError: If no suitable categorical column can be found.
        """
        validate_sql_identifier(table_name, "table_name")
        validate_sql_identifier(timeframe_column, "timeframe_column")
        self._ensure_registered(table_name)
        sql = (
            "SELECT column_name FROM information_schema.columns "
            f"WHERE table_name = '{table_name}' "
            "AND (data_type ILIKE 'varchar%' OR data_type ILIKE 'text%') "
            "ORDER BY ordinal_position"
        )
        try:
            rows = self._adapter.execute_query(sql)
        except DuckDBAdapterError:
            rows = []

        excluded_names = {
            timeframe_column.lower(),
            "timeframe",
            "quarter",
            "period",
            "date",
            "month",
            "year",
        }
        text_cols = [
            str(col_name)
            for (col_name,) in rows
            if col_name and str(col_name).lower() not in excluded_names
        ]

        if not text_cols:
            all_cols = self._adapter.execute_query(
                f"SELECT column_name FROM information_schema.columns WHERE table_name = '{table_name}'"
            )
            col_names = [str(r[0]) for r in all_cols]
            if "department" in col_names:
                return "department"
            raise AnalyticsServiceError(
                f"No suitable categorical dimension column found in '{table_name}'. "
                f"Available columns: {col_names}. Please specify 'dimension' explicitly."
            )

        scored_candidates: list[tuple[int, int, str]] = []
        for col in text_cols:
            try:
                card_row = self._adapter.execute_query(
                    f'SELECT COUNT(DISTINCT "{col}"), COUNT(*) FROM {table_name}'
                )
                distinct_cnt = (
                    int(card_row[0][0])
                    if card_row and card_row[0][0] is not None
                    else 0
                )
                total_cnt = (
                    int(card_row[0][1])
                    if card_row and card_row[0][1] is not None
                    else 0
                )
            except Exception:
                distinct_cnt = 0
                total_cnt = 0

            col_lower = col.lower()
            is_id_name = (
                col_lower in ("id", "row_id", "uuid", "guid", "pk")
                or col_lower.endswith(("_id", "_uuid", "_guid", "_pk"))
            )
            is_unique_pk = total_cnt > 1 and distinct_cnt == total_cnt

            if is_unique_pk:
                priority = 3
            elif is_id_name and distinct_cnt > max(20, int(total_cnt * 0.5)):
                priority = 3
            elif is_id_name:
                priority = 1
            elif 1 < distinct_cnt <= max(50, int(total_cnt * 0.5)):
                priority = 0
            else:
                priority = 2

            scored_candidates.append((priority, distinct_cnt, col))

        scored_candidates.sort(key=lambda x: (x[0], x[1]))
        best = scored_candidates[0]
        if best[0] == 3 and "department" in text_cols:
            return "department"
        return best[2]

    def classify_metric_type(
        self, metric_name: str, custom_expression: str | None = None
    ) -> str:
        """Classify a metric as 'sum', 'mean', or 'unsupported' for two-sample testing.

        Args:
            metric_name: Name of the registered or custom metric.
            custom_expression: Optional custom SQL aggregation expression.

        Returns:
            'sum', 'mean', or 'unsupported'.
        """
        expr = ""
        if custom_expression:
            expr = custom_expression.strip()
        else:
            try:
                spec = self._spec(metric_name)
                expr = spec.get("expression", "").strip()
            except Exception:
                return "unsupported"

        expr_upper = expr.upper()

        # Ratios, percentages, counts, or multi-term compound expressions cannot
        # assume simple two-sample normal distribution variance models.
        if "/" in expr or "%" in expr or "COUNT(" in expr_upper or "MIN(" in expr_upper or "MAX(" in expr_upper:
            return "unsupported"

        if expr_upper.startswith("SUM(") and expr_upper.endswith(")"):
            return "sum"

        if expr_upper.startswith("AVG(") and expr_upper.endswith(")"):
            return "mean"

        return "unsupported"

    def compute_two_sample_significance(
        self,
        metric_name: str,
        observed_delta: float,
        current_stats: dict[str, float],
        baseline_stats: dict[str, float],
        custom_expression: str | None = None,
    ) -> dict[str, Any]:
        """Compute two-sample statistical significance using Welch's t/z methods.

        Accounts for both current and baseline variances and sample sizes.
        Restricted to supported sum and mean metrics.

        Args:
            metric_name: Registered or custom metric name.
            observed_delta: Difference between current and baseline values.
            current_stats: Dict with 'mean', 'std_dev', 'count' for current period.
            baseline_stats: Dict with 'mean', 'std_dev', 'count' for baseline period.
            custom_expression: Optional custom SQL expression.

        Returns:
            Dictionary with keys:
            - z_score: float | None
            - significant: bool
            - is_testable: bool
            - metric_type: str ("sum", "mean", "unsupported")
            - standard_error: float | None
            - note: str | None
        """
        metric_type = self.classify_metric_type(metric_name, custom_expression)
        n_curr = current_stats.get("count", 0.0)
        n_base = baseline_stats.get("count", 0.0)
        s_curr = current_stats.get("std_dev", 0.0)
        s_base = baseline_stats.get("std_dev", 0.0)

        if metric_type == "unsupported":
            return {
                "z_score": None,
                "significant": False,
                "is_testable": False,
                "metric_type": metric_type,
                "standard_error": None,
                "note": (
                    f"Metric '{metric_name}' is classified as '{metric_type}' (e.g. ratio, percentage, count). "
                    "Two-sample normal variance assumptions are unsupported; "
                    "reporting descriptive changes without significance claims."
                ),
            }

        if n_curr == 0 or n_base == 0:
            return {
                "z_score": None,
                "significant": False,
                "is_testable": False,
                "metric_type": metric_type,
                "standard_error": None,
                "note": f"Empty sample in comparative periods (current: {int(n_curr)}, baseline: {int(n_base)}).",
            }

        if n_curr < 2 or n_base < 2:
            return {
                "z_score": None,
                "significant": False,
                "is_testable": False,
                "metric_type": metric_type,
                "standard_error": None,
                "note": f"Sample size too small (current: {int(n_curr)}, baseline: {int(n_base)}) to reliably estimate variance.",
            }

        # Check for zero variance
        if s_curr == 0.0 and s_base == 0.0:
            if observed_delta == 0.0:
                return {
                    "z_score": 0.0,
                    "significant": False,
                    "is_testable": True,
                    "metric_type": metric_type,
                    "standard_error": 0.0,
                    "note": "Zero variance with zero delta (no change observed).",
                }
            else:
                return {
                    "z_score": None,
                    "significant": False,
                    "is_testable": False,
                    "metric_type": metric_type,
                    "standard_error": 0.0,
                    "note": (
                        "Zero within-period variance (constant values across records within periods); "
                        "standard error cannot be computed from sample variance."
                    ),
                }

        # Calculate Standard Error based on metric type
        if metric_type == "mean":
            se_squared = (s_curr ** 2) / n_curr + (s_base ** 2) / n_base
        elif metric_type == "sum":
            se_squared = n_curr * (s_curr ** 2) + n_base * (s_base ** 2)
        else:
            se_squared = 0.0

        if se_squared <= 0.0:
            return {
                "z_score": None,
                "significant": False,
                "is_testable": False,
                "metric_type": metric_type,
                "standard_error": 0.0,
                "note": "Standard error is non-positive; cannot compute test statistic.",
            }

        se = math.sqrt(se_squared)
        z = abs(observed_delta) / se
        is_sig = bool(z > _Z_SCORE_THRESHOLD)

        return {
            "z_score": z,
            "significant": is_sig,
            "is_testable": True,
            "metric_type": metric_type,
            "standard_error": se,
            "note": None,
        }

    @staticmethod
    def significance_for_delta(
        current: float, baseline: float, std_dev: float
    ) -> dict[str, float | bool]:
        """Compute a heuristic z-score and significance indicator for an observed delta.

        Computes the ratio of the delta to the within-period standard deviation.
        For rigorous two-sample hypothesis testing accounting for both period
        variances and sample sizes, use :meth:`compute_two_sample_significance`.

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
