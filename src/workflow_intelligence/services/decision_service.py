"""Decision service orchestrating deterministic KPI analysis and brief generation.

Coordinates :class:`AnalyticsService` (verified metrics) and
:class:`ReportingService` (structured briefs). All numbers flow through
DuckDB; the service never asks an LLM to compute a figure.
"""

from __future__ import annotations

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
        source_uri: str = DEFAULT_SOURCE_URI,
        table_name: str = DEFAULT_TABLE,
        current_quarter: str = DEFAULT_CURRENT_QUARTER,
        baseline_quarter: str = DEFAULT_BASELINE_QUARTER,
    ) -> PerformanceAnalysisResult:
        """Compute period-over-period KPI performance.

        Args:
            request: KPI calculation request.
            source_uri: Source CSV file registered as a DuckDB view.
            table_name: Logical table name for the source data.
            current_quarter: Current period identifier.
            baseline_quarter: Baseline period identifier.

        Returns:
            A :class:`PerformanceAnalysisResult` with baseline, current, and
            delta percentage.
        """
        self._analytics.use_source(source_uri, table_name)
        comparison = self._analytics.compare_periods(
            table_name, request.metric_name, current_quarter, baseline_quarter
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
        source_uri: str = DEFAULT_SOURCE_URI,
        table_name: str = DEFAULT_TABLE,
        dimension_col: str = "department",
        current_quarter: str = DEFAULT_CURRENT_QUARTER,
        baseline_quarter: str = DEFAULT_BASELINE_QUARTER,
    ) -> DeviationExplanation:
        """Explain a KPI deviation using verified dimensional drivers.

        Attributes the observed delta to the top contributing dimension values
        and attaches deterministic statistical significance indicators.

        Args:
            request: KPI explanation request.
            source_uri: Source CSV file registered as a DuckDB view.
            table_name: Logical table name for the source data.
            dimension_col: Column to segment the deviation by.
            current_quarter: Current period identifier.
            baseline_quarter: Baseline period identifier.

        Returns:
            A :class:`DeviationExplanation` with top contributors, significance
            indicators, and a number-only narrative.
        """
        self._analytics.use_source(source_uri, table_name)

        attributions = self._analytics.rank_dimensions(
            table_name,
            request.metric_name,
            dimension_col,
            current_quarter,
            baseline_quarter,
        )
        stats = self._analytics.metric_stats(
            table_name, request.metric_name, current_quarter
        )
        significance = self._analytics.significance_for_delta(
            request.observed_delta, 0.0, stats["std_dev"]
        )

        data_insufficient = stats["count"] == 0 or not significance["significant"]
        warning: str | None = None
        if stats["count"] == 0:
            warning = (
                f"No records found for metric '{request.metric_name}' in "
                f"{current_quarter}; deviation cannot be explained."
            )
        elif not significance["significant"]:
            warning = (
                f"Observed delta {request.observed_delta} is not statistically "
                f"significant (z={significance['z_score']:.2f}); interpret "
                f"with caution."
            )

        if attributions:
            top = attributions[0]
            driver_text = (
                f"{top.segment_name} ({top.contribution_percentage:.2f}% of "
                f"the variance, {top.record_count} records)"
            )
        else:
            driver_text = "no identifiable dimensional driver"

        explanation = (
            f"The {request.metric_name} deviation of {request.observed_delta} "
            f"is primarily driven by {driver_text}. "
            f"Statistical significance: z={significance['z_score']:.2f}, "
            f"significant={significance['significant']}."
        )

        return DeviationExplanation(
            metric_name=request.metric_name,
            observed_delta=request.observed_delta,
            dimension=request.dimension or dimension_col,
            top_contributors=attributions,
            statistical_significance={
                "z_score": significance["z_score"],
                "significant": float(int(significance["significant"])),
                "std_dev": stats["std_dev"],
                "record_count": stats["count"],
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
        current_quarter: str = DEFAULT_CURRENT_QUARTER,
        baseline_quarter: str = DEFAULT_BASELINE_QUARTER,
    ) -> DecisionBrief:
        """Generate a structured decision brief.

        Delegates to :class:`ReportingService`, which builds the brief
        exclusively from verified DuckDB metrics.
        """
        return self._reporting.generate_brief(
            request,
            source_uri=source_uri,
            table_name=table_name,
            current_quarter=current_quarter,
            baseline_quarter=baseline_quarter,
        )
