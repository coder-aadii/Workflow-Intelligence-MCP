"""Unit and accuracy tests for the Business Decision Intelligence engine.

Tests numerical precision against known baseline datasets and verifies the
zero-hallucination guarantee: an error or warning is returned when data is
missing or statistically insignificant.
"""

from __future__ import annotations

import concurrent.futures
import os
import tempfile

import pandas as pd
import pytest

from workflow_intelligence.adapters.analytics.duckdb_adapter import (
    DuckDBAdapter,
    DuckDBAdapterError,
)
from workflow_intelligence.schemas.decision import (
    DecisionBrief,
    DecisionBriefRequest,
    DeviationExplanation,
    KPICalculationRequest,
    KPIExplanationRequest,
    MetricComparison,
    PerformanceAnalysisResult,
)
from workflow_intelligence.services.analytics_service import (
    METRIC_REGISTRY,
    AnalyticsService,
    AnalyticsServiceError,
)
from workflow_intelligence.services.decision_service import DecisionService
from workflow_intelligence.services.reporting_service import ReportingService

PROCUREMENT_CSV = "sample-data/procurement_costs.csv"

# Known baseline values computed from sample-data/procurement_costs.csv.
Q1_PROCESSING_COST = 500.0
Q2_PROCESSING_COST = 525.0
Q3_PROCESSING_COST = 1450.0
Q4_PROCESSING_COST = 550.0


class _RegisteredAnalyticsService(AnalyticsService):
    """AnalyticsService pre-registered against the procurement costs fixture."""

    def __init__(self) -> None:
        super().__init__(source_uri=PROCUREMENT_CSV, table_name="procurement_costs")
        self.register_source()


@pytest.fixture
def analytics_service() -> AnalyticsService:
    return _RegisteredAnalyticsService()


@pytest.fixture
def decision_service() -> DecisionService:
    return DecisionService(analytics=_RegisteredAnalyticsService())


@pytest.fixture
def reporting_service() -> ReportingService:
    return ReportingService(analytics=_RegisteredAnalyticsService())


class TestDuckDBAdapter:
    """Tests for the thread-safe DuckDB adapter."""

    def test_register_csv_and_query(self) -> None:
        adapter = DuckDBAdapter(":memory:")
        adapter.register_csv("procurement_costs", PROCUREMENT_CSV)
        assert adapter.table_row_count("procurement_costs") == 20
        rows = adapter.execute_query(
            "SELECT SUM(processing_cost) FROM procurement_costs WHERE quarter = ?",
            ["Q3"],
        )
        assert rows[0][0] == Q3_PROCESSING_COST

    def test_file_hash_is_deterministic(self) -> None:
        h1 = DuckDBAdapter.file_hash(PROCUREMENT_CSV)
        h2 = DuckDBAdapter.file_hash(PROCUREMENT_CSV)
        assert h1 == h2
        assert len(h1) == 64

    def test_missing_file_raises(self) -> None:
        adapter = DuckDBAdapter(":memory:")
        with pytest.raises(DuckDBAdapterError):
            adapter.register_csv("missing", "/nonexistent/file.csv")

    def test_concurrent_queries_are_safe(self) -> None:
        adapter = DuckDBAdapter(":memory:")
        adapter.register_csv("procurement_costs", PROCUREMENT_CSV)

        def query() -> float:
            rows = adapter.execute_query(
                "SELECT SUM(processing_cost) FROM procurement_costs WHERE quarter = ?",
                ["Q3"],
            )
            return float(rows[0][0])

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: query(), range(32)))
        assert all(r == Q3_PROCESSING_COST for r in results)


class TestAnalyticsService:
    """Tests for deterministic KPI computation and dimensional attribution."""

    def test_compare_periods_exact_values(
        self, analytics_service: AnalyticsService
    ) -> None:
        comparison = analytics_service.compare_periods(
            "procurement_costs", "processing_cost", "Q3", "Q2"
        )
        assert isinstance(comparison, MetricComparison)
        assert comparison.metric_name == "processing_cost"
        assert comparison.baseline_value == Q2_PROCESSING_COST
        assert comparison.current_value == Q3_PROCESSING_COST
        assert comparison.absolute_variance == Q3_PROCESSING_COST - Q2_PROCESSING_COST
        assert abs(comparison.relative_variance_percentage - 176.19047619047618) < 1e-6

    def test_compare_periods_no_variance(
        self, analytics_service: AnalyticsService
    ) -> None:
        comparison = analytics_service.compare_periods(
            "procurement_costs", "order_count", "Q3", "Q2"
        )
        assert comparison.absolute_variance == 0.0
        assert comparison.relative_variance_percentage == 0.0

    def test_rank_dimensions_isolates_variance_contributors(
        self, analytics_service: AnalyticsService
    ) -> None:
        attributions = analytics_service.rank_dimensions(
            "procurement_costs", "processing_cost", "department", "Q3", "Q2"
        )
        assert len(attributions) == 2
        engineering = next(a for a in attributions if "Engineering" in a.segment_name)
        sales = next(a for a in attributions if "Sales" in a.segment_name)
        # Engineering: (930 - 345) = 585; Sales: (520 - 180) = 340; total 925.
        assert abs(engineering.contribution_percentage - 63.24324324324324) < 1e-6
        assert abs(sales.contribution_percentage - 36.75675675675676) < 1e-6
        assert engineering.record_count == 3
        assert sales.record_count == 2
        assert (
            attributions[0].contribution_percentage
            >= attributions[1].contribution_percentage
        )

    def test_rank_dimensions_empty_delta(
        self, analytics_service: AnalyticsService
    ) -> None:
        attributions = analytics_service.rank_dimensions(
            "procurement_costs", "order_count", "department", "Q3", "Q2"
        )
        for a in attributions:
            assert a.contribution_percentage == 0.0

    def test_metric_stats(self, analytics_service: AnalyticsService) -> None:
        stats = analytics_service.metric_stats(
            "procurement_costs", "processing_cost", "Q3"
        )
        assert stats["mean"] == Q3_PROCESSING_COST
        assert stats["count"] == 5
        assert stats["std_dev"] > 0

    def test_significance_for_delta_significant(self) -> None:
        sig = AnalyticsService.significance_for_delta(1450.0, 525.0, 29.154759474226502)
        assert sig["significant"] is True
        assert sig["z_score"] > 1.96

    def test_significance_not_significant(self) -> None:
        sig = AnalyticsService.significance_for_delta(5.0, 0.0, 100.0)
        assert sig["significant"] is False

    def test_unknown_metric_raises(self, analytics_service: AnalyticsService) -> None:
        with pytest.raises(AnalyticsServiceError):
            analytics_service.compare_periods(
                "procurement_costs", "nonexistent_metric", "Q3", "Q2"
            )

    def test_available_metrics_documented(self) -> None:
        assert "processing_cost" in METRIC_REGISTRY
        assert "avg_processing_cost" in METRIC_REGISTRY
        assert "order_count" in METRIC_REGISTRY


class TestDecisionService:
    """Tests for the decision orchestration service."""

    def test_analyze_kpi_performance(self, decision_service: DecisionService) -> None:
        result = decision_service.analyze_kpi_performance(
            KPICalculationRequest(
                metric_name="processing_cost",
                timeframe_current="Q3",
                timeframe_baseline="Q2",
            )
        )
        assert isinstance(result, PerformanceAnalysisResult)
        assert result.baseline_value == Q2_PROCESSING_COST
        assert result.current_value == Q3_PROCESSING_COST
        assert abs(result.delta_percentage - 176.19047619047618) < 1e-6
        assert result.success is True

    def test_explain_kpi_deviation_significant(
        self, decision_service: DecisionService
    ) -> None:
        explanation = decision_service.explain_kpi_deviation(
            KPIExplanationRequest(
                metric_name="processing_cost",
                observed_delta=925.0,
                dimension="department",
            )
        )
        assert isinstance(explanation, DeviationExplanation)
        assert explanation.metric_name == "processing_cost"
        assert explanation.observed_delta == 925.0
        assert explanation.dimension == "department"
        assert explanation.data_insufficient is False
        assert explanation.warning is None
        assert explanation.statistical_significance["significant"] == 1.0
        assert len(explanation.top_contributors) == 2
        assert "Engineering" in explanation.explanation
        assert "925.0" in explanation.explanation

    def test_explain_kpi_deviation_missing_data(
        self, decision_service: DecisionService
    ) -> None:
        explanation = decision_service.explain_kpi_deviation(
            KPIExplanationRequest(
                metric_name="processing_cost",
                observed_delta=9999.0,
                dimension="department",
            ),
            current_quarter="Q9",
            baseline_quarter="Q8",
        )
        assert explanation.data_insufficient is True
        assert explanation.warning is not None
        assert "No records found" in explanation.warning

    def test_explain_kpi_deviation_not_significant(self) -> None:
        rows = [
            {
                "order_id": "O1",
                "quarter": "Q2",
                "department": "Engineering",
                "supplier_tier": "T1",
                "processing_cost": 100,
                "shipping_method": "Standard",
                "expedited_fee": 0,
            },
            {
                "order_id": "O2",
                "quarter": "Q2",
                "department": "Sales",
                "supplier_tier": "T2",
                "processing_cost": 200,
                "shipping_method": "Standard",
                "expedited_fee": 0,
            },
            {
                "order_id": "O3",
                "quarter": "Q2",
                "department": "Engineering",
                "supplier_tier": "T1",
                "processing_cost": 300,
                "shipping_method": "Standard",
                "expedited_fee": 0,
            },
            {
                "order_id": "O4",
                "quarter": "Q3",
                "department": "Engineering",
                "supplier_tier": "T1",
                "processing_cost": 105,
                "shipping_method": "Standard",
                "expedited_fee": 0,
            },
            {
                "order_id": "O5",
                "quarter": "Q3",
                "department": "Sales",
                "supplier_tier": "T2",
                "processing_cost": 195,
                "shipping_method": "Standard",
                "expedited_fee": 0,
            },
            {
                "order_id": "O6",
                "quarter": "Q3",
                "department": "Engineering",
                "supplier_tier": "T1",
                "processing_cost": 305,
                "shipping_method": "Standard",
                "expedited_fee": 0,
            },
        ]
        fd, path = tempfile.mkstemp(suffix=".csv")
        with os.fdopen(fd, "w") as fh:
            pd.DataFrame(rows).to_csv(fh, index=False)
        try:
            svc = DecisionService(
                analytics=AnalyticsService(source_uri=path, table_name="flat_costs")
            )
            svc._analytics.register_source()
            explanation = svc.explain_kpi_deviation(
                KPIExplanationRequest(
                    metric_name="processing_cost",
                    observed_delta=5.0,
                    dimension="department",
                ),
                source_uri=path,
                table_name="flat_costs",
                current_quarter="Q3",
                baseline_quarter="Q2",
            )
            assert explanation.data_insufficient is True
            assert explanation.warning is not None
            assert "not statistically significant" in explanation.warning
        finally:
            os.remove(path)

    def test_generate_brief_structure(self, decision_service: DecisionService) -> None:
        brief = decision_service.generate_brief(
            DecisionBriefRequest(
                topic="Q3 Procurement Cost Inflation",
                metric_ids=["processing_cost"],
            )
        )
        assert isinstance(brief, DecisionBrief)
        assert brief.topic == "Q3 Procurement Cost Inflation"
        assert brief.success is True
        assert len(brief.findings) == 1
        assert brief.findings[0].current_value == Q3_PROCESSING_COST
        assert len(brief.attributions) == 2
        assert any("sha256=" in link for link in brief.provenance_links)
        assert any("source=" in link for link in brief.provenance_links)
        assert len(brief.recommendations) >= 1
        assert "Executive Summary" in brief.executive_summary
        assert "Observed Variance" in brief.executive_summary
        assert "Primary Contributing Drivers" in brief.executive_summary
        assert "Provenance Footnotes" in brief.executive_summary
        assert "Recommended Interventions" in brief.executive_summary


class TestReportingService:
    """Tests for the markdown decision-brief engine."""

    def test_generate_brief_provenance_traceability(
        self, reporting_service: ReportingService
    ) -> None:
        request = DecisionBriefRequest(
            topic="Cost Review", metric_ids=["processing_cost", "order_count"]
        )
        brief = reporting_service.generate_brief(request)
        assert isinstance(brief, DecisionBrief)
        assert len(brief.findings) == 2
        assert any(PROCUREMENT_CSV in link for link in brief.provenance_links)
        assert any("sha256=" in link for link in brief.provenance_links)
        assert any("size_bytes=" in link for link in brief.provenance_links)

    def test_generate_brief_unknown_metric_raises(
        self, reporting_service: ReportingService
    ) -> None:
        request = DecisionBriefRequest(
            topic="Cost Review", metric_ids=["nonexistent_metric"]
        )
        with pytest.raises(AnalyticsServiceError):
            reporting_service.generate_brief(request)
