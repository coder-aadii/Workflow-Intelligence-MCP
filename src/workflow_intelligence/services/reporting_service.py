"""Markdown decision-brief engine built exclusively from verified metrics.

The ReportingService combines deterministic :class:`AnalyticsService` outputs
into a structured :class:`DecisionBrief`. Every figure in the brief traces
back to a DuckDB-computed value over a registered source file, and the
executive summary follows a fixed template:

1. Executive Summary
2. Observed Variance
3. Primary Contributing Drivers
4. Provenance Footnotes
5. Recommended Interventions

No LLM is used to draft numbers; the narrative is assembled from verified
statistics only.
"""

from __future__ import annotations

from workflow_intelligence.adapters.analytics.duckdb_adapter import DuckDBAdapter
from workflow_intelligence.schemas.decision import (
    DecisionBrief,
    DecisionBriefRequest,
    DimensionAttribution,
    MetricComparison,
)
from workflow_intelligence.services.analytics_service import (
    DEFAULT_BASELINE_QUARTER,
    DEFAULT_CURRENT_QUARTER,
    DEFAULT_SOURCE_URI,
    DEFAULT_TABLE,
    AnalyticsService,
)


class ReportingService:
    """Builds evidence-backed decision briefs from verified KPI metrics."""

    def __init__(self, analytics: AnalyticsService | None = None) -> None:
        self._analytics = analytics or AnalyticsService()

    def generate_brief(
        self,
        request: DecisionBriefRequest,
        source_uri: str = DEFAULT_SOURCE_URI,
        table_name: str = DEFAULT_TABLE,
        timeframe_column: str = "quarter",
        current_quarter: str = DEFAULT_CURRENT_QUARTER,
        baseline_quarter: str = DEFAULT_BASELINE_QUARTER,
    ) -> DecisionBrief:
        """Generate a structured decision brief for a topic and metric set.

        Args:
            request: Decision brief request with topic and metric ids.
            source_uri: Source CSV file registered as a DuckDB view.
            table_name: Logical table name for the source data.
            timeframe_column: Column used for period filtering.
            current_quarter: Current period identifier.
            baseline_quarter: Baseline period identifier.

        Returns:
            A populated :class:`DecisionBrief` with findings, attributions,
            provenance footnotes, and recommendations.

        Raises:
            AnalyticsServiceError: If any metric id is unknown or the source
                cannot be registered.
        """
        self._analytics.use_source(source_uri, table_name)

        # Validate every metric id against the registry before computing
        # anything, so we never fabricate a metric that does not exist.
        for metric_id in request.metric_ids:
            self._analytics._spec(metric_id)  # raises if unknown

        findings: list[MetricComparison] = []
        for metric_id in request.metric_ids:
            comparison = self._analytics.compare_periods(
                table_name,
                metric_id,
                timeframe_column,
                current_quarter,
                baseline_quarter,
            )
            findings.append(comparison)

        attributions: list[DimensionAttribution] = []
        if findings:
            primary_metric = request.metric_ids[0]
            # Dynamically infer the dimension column instead of hardcoding "department".
            dimension_col = self._analytics.infer_dimension_column(table_name)
            attributions = self._analytics.rank_dimensions(
                table_name,
                primary_metric,
                dimension_col,
                current_quarter,
                baseline_quarter,
                timeframe_column=timeframe_column,
            )

        file_hash = DuckDBAdapter.file_hash(source_uri)
        file_size = DuckDBAdapter.file_size(source_uri)
        provenance_links = [
            f"source={source_uri}",
            f"sha256={file_hash}",
            f"size_bytes={file_size}",
            f"table={table_name}",
            f"current_period={current_quarter}",
            f"baseline_period={baseline_quarter}",
        ]

        executive_summary = self._build_executive_summary(
            request.topic, findings, attributions, current_quarter, baseline_quarter
        )
        recommendation_lines = self._recommendation_lines(findings, attributions)

        return DecisionBrief(
            topic=request.topic,
            executive_summary=executive_summary,
            findings=findings,
            attributions=attributions,
            provenance_links=provenance_links,
            recommendations=recommendation_lines,
        )

    def _build_executive_summary(
        self,
        topic: str,
        findings: list[MetricComparison],
        attributions: list[DimensionAttribution],
        current_quarter: str,
        baseline_quarter: str,
    ) -> str:
        """Render the markdown decision brief from verified numbers only."""
        lines: list[str] = []
        lines.append(f"# Decision Brief: {topic}")
        lines.append("")
        lines.append("## Executive Summary")
        if not findings:
            lines.append(
                "No metrics were provided. Supply at least one registered "
                "metric id to generate a brief."
            )
        else:
            delta_lines = []
            for f in findings:
                direction = "increase" if f.absolute_variance >= 0 else "decrease"
                delta_lines.append(
                    f"- **{f.metric_name}**: {f.baseline_value} "
                    f"({baseline_quarter}) -> {f.current_value} "
                    f"({current_quarter}), "
                    f"{direction} of {abs(f.absolute_variance)} "
                    f"({f.relative_variance_percentage:.2f}%)."
                )
            lines.extend(delta_lines)
        lines.append("")
        lines.append("## Observed Variance")
        if not findings:
            lines.append("No variance data available.")
        else:
            for f in findings:
                lines.append(
                    f"- {f.metric_name}: absolute variance = "
                    f"{f.absolute_variance}, relative variance = "
                    f"{f.relative_variance_percentage:.2f}%."
                )
        lines.append("")
        lines.append("## Primary Contributing Drivers")
        if not attributions:
            lines.append("No dimensional drivers identified.")
        else:
            for a in attributions:
                lines.append(
                    f"- {a.segment_name}: {a.contribution_percentage:.2f}% "
                    f"of the variance ({a.record_count} records)."
                )
        lines.append("")
        lines.append("## Provenance Footnotes")
        lines.append("- All figures computed deterministically by DuckDB.")
        lines.append(
            "- No LLM-generated numbers; narrative derived from verified statistics."
        )
        lines.append("")
        lines.append("## Recommended Interventions")
        recommendation_lines = self._recommendation_lines(findings, attributions)
        if not recommendation_lines:
            lines.append("- No specific interventions indicated by the current data.")
        else:
            lines.extend(recommendation_lines)
        return "\n".join(lines)

    @staticmethod
    def _recommendation_lines(
        findings: list[MetricComparison],
        attributions: list[DimensionAttribution],
    ) -> list[str]:
        """Derive actionable recommendations from verified variance."""
        lines: list[str] = []
        if not findings:
            return lines
        top = max(findings, key=lambda f: abs(f.relative_variance_percentage))
        if abs(top.relative_variance_percentage) >= 50.0:
            driver = (
                attributions[0].segment_name if attributions else "the leading segment"
            )
            lines.append(
                f"Investigate {driver}, the largest contributor to the "
                f"{top.metric_name} variance "
                f"({top.relative_variance_percentage:.2f}%)."
            )
        if attributions:
            top_driver = attributions[0]
            lines.append(
                f"Prioritize {top_driver.segment_name} which accounts for "
                f"{top_driver.contribution_percentage:.2f}% of the observed variance."
            )
        return lines
