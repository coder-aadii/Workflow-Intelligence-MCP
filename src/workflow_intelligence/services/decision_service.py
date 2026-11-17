"""Decision service orchestrating deterministic KPI analysis and brief generation.

Coordinates :class:`AnalyticsService` (verified metrics) and
:class:`ReportingService` (structured briefs). All numbers flow through
DuckDB; the service never asks an LLM to compute a figure.

The service follows a universal contract: every call carries an explicit
``source_uri`` and timeframe column/values, so it works against any
enterprise dataset with zero hardcoded procurement assumptions.
"""

from __future__ import annotations

from pathlib import Path

from workflow_intelligence.schemas.decision import (
    DecisionBrief,
    DecisionBriefRequest,
    DeviationExplanation,
    KPICalculationRequest,
    KPIExplanationRequest,
    PerformanceAnalysisResult,
)
from workflow_intelligence.services.analytics_service import (
    DEFAULT_BASELINE_QUARTER,
    DEFAULT_CURRENT_QUARTER,
    DEFAULT_SOURCE_URI,
    DEFAULT_TABLE,
    AnalyticsService,
)
from workflow_intelligence.services.reporting_service import ReportingService


class DecisionServiceError(Exception):
    """Raised when decision service operations fail."""


def _table_name_from_uri(source_uri: str) -> str:
    """Derive a safe DuckDB table name from a source URI.

    Sanitizes the file stem so it can be used as a SQL identifier.
    """
    stem = Path(source_uri).stem
    safe = "".join(c if c.isalnum() or c == "_" else "_" for c in stem)
    if not safe or safe[0].isdigit():
        safe = "t_" + safe
    return safe


class DecisionService:
    """Application service for the decision_intelligence tool suite."""

    def __init__(
        self,
        analytics: AnalyticsService | None = None,
        reporting: ReportingService | None = None,
    ) -> None:
        self._analytics = analytics or AnalyticsService()
        self._reporting = reporting or ReportingService(analytics=self._analytics)

    def analyze_kpi_performance(
        self,
        request: KPICalculationRequest,
    ) -> PerformanceAnalysisResult:
        """Compute period-over-period KPI performance for any dataset.

        Args:
            request: KPI calculation request carrying source URI, timeframe
                column, current/baseline periods, and optional custom
                expression.

        Returns:
            A :class:`PerformanceAnalysisResult` with baseline, current, and
            delta percentage.
        """
        table_name = _table_name_from_uri(request.source_uri)
        self._analytics.use_source(request.source_uri, table_name)
        # Register a dynamic metric if a custom expression is supplied.
        if request.custom_expression is not None:
            self._analytics.register_custom_metric(
                request.metric_name,
                request.custom_expression,
                column=request.metric_name,
            )
        comparison = self._analytics.compare_periods(
            table_name,
            request.metric_name,
            request.timeframe_column,
            request.timeframe_current,
            request.timeframe_baseline,
        )
        return PerformanceAnalysisResult(
            metric_name=comparison.metric_name,
            baseline_value=comparison.baseline_value,
            current_value=comparison.current_value,
            delta_percentage=comparison.relative_variance_percentage,
        )

    def explain_kpi_deviation(
        self,
        request: KPIExplanationRequest,
    ) -> DeviationExplanation:
        """Explain a KPI deviation using verified dimensional drivers.

        Attributes the observed delta to the top contributing dimension values
        and attaches deterministic statistical significance indicators.
        Dynamically infers the segmentation dimension when none is provided.

        Args:
            request: KPI explanation request carrying source URI, timeframe
                column, current/baseline periods, observed delta, optional
                dimension, and optional custom expression.

        Returns:
            A :class:`DeviationExplanation` with top contributors, significance
            indicators, and a number-only narrative.
        """
        table_name = _table_name_from_uri(request.source_uri)
        self._analytics.use_source(request.source_uri, table_name)
        # Register a dynamic metric if a custom expression is supplied.
        if request.custom_expression is not None:
            self._analytics.register_custom_metric(
                request.metric_name,
                request.custom_expression,
                column=request.metric_name,
            )

        # Dynamically infer the dimension column when none is provided.
        dimension_col = request.dimension
        if dimension_col is None:
            dimension_col = self._analytics.infer_dimension_column(
                table_name, timeframe_column=request.timeframe_column
            )

        attributions = self._analytics.rank_dimensions(
            table_name,
            request.metric_name,
            dimension_col,
            request.timeframe_current,
            request.timeframe_baseline,
            timeframe_column=request.timeframe_column,
        )
        current_stats = self._analytics.metric_stats(
            table_name,
            request.metric_name,
            request.timeframe_column,
            request.timeframe_current,
        )
        baseline_stats = self._analytics.metric_stats(
            table_name,
            request.metric_name,
            request.timeframe_column,
            request.timeframe_baseline,
        )
        sig_result = self._analytics.compute_two_sample_significance(
            metric_name=request.metric_name,
            observed_delta=request.observed_delta,
            current_stats=current_stats,
            baseline_stats=baseline_stats,
            custom_expression=request.custom_expression,
        )

        if attributions:
            top = attributions[0]
            action = "increase" if top.delta >= 0 else "reduction"
            driver_text = (
                f"{top.segment_name} ({action} of {abs(top.delta):.2f}, "
                f"{top.contribution_percentage:.2f}% net contribution, {top.record_count} records)"
            )
        else:
            driver_text = "no identifiable dimensional driver"

        data_insufficient = False
        warning: str | None = None

        if current_stats["count"] == 0 or baseline_stats["count"] == 0:
            data_insufficient = True
            warning = (
                f"No records found for metric '{request.metric_name}' in "
                f"comparative periods ({request.timeframe_current}: {int(current_stats['count'])}, "
                f"{request.timeframe_baseline}: {int(baseline_stats['count'])}); deviation cannot be explained."
            )
            explanation = (
                f"The {request.metric_name} deviation of {request.observed_delta} "
                f"cannot be explained due to missing records."
            )
        elif not sig_result["is_testable"]:
            data_insufficient = False
            warning = sig_result.get("note")
            explanation = (
                f"The {request.metric_name} deviation of {request.observed_delta} "
                f"is primarily driven by {driver_text}. Note: {sig_result.get('note')}"
            )
        else:
            z_val = sig_result["z_score"]
            se_val = sig_result["standard_error"]
            is_sig = sig_result["significant"]
            if not is_sig:
                data_insufficient = True
                warning = (
                    f"Observed delta {request.observed_delta} is not statistically "
                    f"significant (z={z_val:.2f}); interpret with caution."
                )
                explanation = (
                    f"The {request.metric_name} deviation of {request.observed_delta} "
                    f"is primarily driven by {driver_text}. "
                    f"Statistical significance: z={z_val:.2f}, "
                    f"significant=False (SE={se_val:.2f})."
                )
            else:
                data_insufficient = False
                warning = None
                explanation = (
                    f"The {request.metric_name} deviation of {request.observed_delta} "
                    f"is primarily driven by {driver_text}. "
                    f"Statistical significance: z={z_val:.2f}, "
                    f"significant=True (SE={se_val:.2f})."
                )

        return DeviationExplanation(
            metric_name=request.metric_name,
            observed_delta=request.observed_delta,
            dimension=request.dimension or dimension_col,
            top_contributors=attributions,
            statistical_significance={
                "z_score": sig_result.get("z_score"),
                "significant": float(int(sig_result.get("significant", False))),
                "is_testable": float(int(sig_result.get("is_testable", False))),
                "standard_error": sig_result.get("standard_error"),
                "metric_type": sig_result.get("metric_type"),
                "std_dev": current_stats["std_dev"],
                "current_std_dev": current_stats["std_dev"],
                "baseline_std_dev": baseline_stats["std_dev"],
                "record_count": current_stats["count"],
                "current_count": current_stats["count"],
                "baseline_count": baseline_stats["count"],
            },
            explanation=explanation,
            data_insufficient=data_insufficient,
            warning=warning,
        )

    def generate_brief(
        self,
        request: DecisionBriefRequest,
        source_uri: str = DEFAULT_SOURCE_URI,
        table_name: str = DEFAULT_TABLE,
        timeframe_column: str = "quarter",
        current_quarter: str = DEFAULT_CURRENT_QUARTER,
        baseline_quarter: str = DEFAULT_BASELINE_QUARTER,
    ) -> DecisionBrief:
        """Generate a structured decision brief.

        Delegates to :class:`ReportingService`, which builds the brief
        exclusively from verified DuckDB metrics.

        Args:
            request: Decision brief request.
            source_uri: Source CSV file registered as a DuckDB view.
            table_name: Logical table name for the source data.
            timeframe_column: Column used for period filtering.
            current_quarter: Current period identifier.
            baseline_quarter: Baseline period identifier.
        """
        is_demo_default = request.source_uri is None
        if not is_demo_default:
            assert request.source_uri is not None
            if not Path(request.source_uri).exists():
                raise DecisionServiceError(
                    f"Specified source_uri '{request.source_uri}' does not exist. "
                    "Cannot generate brief from a non-existent dataset."
                )
            eff_source = request.source_uri
            eff_table = _table_name_from_uri(eff_source)
        else:
            eff_source = source_uri
            eff_table = table_name

        eff_timeframe_col = request.timeframe_column or timeframe_column
        eff_current = request.timeframe_current or current_quarter
        eff_baseline = request.timeframe_baseline or baseline_quarter

        return self._reporting.generate_brief(
            request,
            source_uri=eff_source,
            table_name=eff_table,
            timeframe_column=eff_timeframe_col,
            current_quarter=eff_current,
            baseline_quarter=eff_baseline,
            is_demo_default=is_demo_default,
        )
