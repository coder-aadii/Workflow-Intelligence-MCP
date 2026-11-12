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
        expected_q3 = (
            pd.read_csv(PROCUREMENT_CSV)
            .query("quarter == 'Q3'")["processing_cost"]
            .sum()
        )
        assert rows[0][0] == expected_q3

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

        expected_q3 = (
            pd.read_csv(PROCUREMENT_CSV)
            .query("quarter == 'Q3'")["processing_cost"]
            .sum()
        )

        def query() -> float:
            rows = adapter.execute_query(
                "SELECT SUM(processing_cost) FROM procurement_costs WHERE quarter = ?",
                ["Q3"],
            )
            return float(rows[0][0])

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: query(), range(32)))
        assert all(r == expected_q3 for r in results)


class TestAnalyticsService:
    """Tests for deterministic KPI computation and dimensional attribution."""

    def test_compare_periods_exact_values(
        self, analytics_service: AnalyticsService
    ) -> None:
        comparison = analytics_service.compare_periods(
            "procurement_costs",
            "processing_cost",
            "quarter",
            "Q3",
            "Q2",
        )
        assert isinstance(comparison, MetricComparison)
        assert comparison.metric_name == "processing_cost"
        # Independent verification with pandas (exit criterion: 100% exact
        # numerical match between DuckDB SQL results and tool output schemas).
        df = pd.read_csv(PROCUREMENT_CSV)
        q2 = df[df["quarter"] == "Q2"]["processing_cost"].sum()
        q3 = df[df["quarter"] == "Q3"]["processing_cost"].sum()
        assert comparison.baseline_value == q2
        assert comparison.current_value == q3
        assert comparison.absolute_variance == q3 - q2
        expected_pct = (q3 - q2) / q2 * 100.0 if q2 != 0 else 0.0
        assert abs(comparison.relative_variance_percentage - expected_pct) < 1e-6

    def test_compare_periods_no_variance(
        self, analytics_service: AnalyticsService
    ) -> None:
        comparison = analytics_service.compare_periods(
            "procurement_costs", "order_count", "quarter", "Q3", "Q2"
        )
        assert comparison.absolute_variance == 0.0
        assert comparison.relative_variance_percentage == 0.0

    def test_rank_dimensions_isolates_variance_contributors(
        self, analytics_service: AnalyticsService
    ) -> None:
        attributions = analytics_service.rank_dimensions(
            "procurement_costs",
            "processing_cost",
            "department",
            "Q3",
            "Q2",
            timeframe_column="quarter",
        )
        assert len(attributions) >= 1
        # Independent per-quarter per-department sums with pandas.
        df = pd.read_csv(PROCUREMENT_CSV)
        q3 = df[df["quarter"] == "Q3"].groupby("department")["processing_cost"].sum()
        q2 = df[df["quarter"] == "Q2"].groupby("department")["processing_cost"].sum()
        deltas = {
            dept: abs(q3.get(dept, 0) - q2.get(dept, 0))
            for dept in set(q3.index) | set(q2.index)
        }
        total = sum(deltas.values())
        seen = set()
        for a in attributions:
            dept = a.segment_name.split(" = ")[1]
            seen.add(dept)
            expected = deltas.get(dept, 0) / total * 100 if total != 0 else 0.0
            assert abs(a.contribution_percentage - expected) < 1e-6
            assert a.record_count == len(
                df[(df["quarter"] == "Q3") & (df["department"] == dept)]
            )
        assert seen == set(deltas)
        # Contributions sum to 100% and are sorted descending.
        assert abs(sum(a.contribution_percentage for a in attributions) - 100.0) < 1e-6
        percents = [a.contribution_percentage for a in attributions]
        assert percents == sorted(percents, reverse=True)

    def test_rank_dimensions_matches_pandas(
        self, analytics_service: AnalyticsService
    ) -> None:
        attributions = analytics_service.rank_dimensions(
            "procurement_costs",
            "order_count",
            "department",
            "Q3",
            "Q2",
            timeframe_column="quarter",
        )
        assert len(attributions) >= 1
        df = pd.read_csv(PROCUREMENT_CSV)
        q3 = df[df["quarter"] == "Q3"].groupby("department")["order_id"].count()
        q2 = df[df["quarter"] == "Q2"].groupby("department")["order_id"].count()
        deltas = {
            dept: abs(q3.get(dept, 0) - q2.get(dept, 0))
            for dept in set(q3.index) | set(q2.index)
        }
        total = sum(deltas.values())
        seen = set()
        for a in attributions:
            dept = a.segment_name.split(" = ")[1]
            seen.add(dept)
            expected = deltas.get(dept, 0) / total * 100 if total != 0 else 0.0
            assert abs(a.contribution_percentage - expected) < 1e-6
            assert a.record_count == int(q3.get(dept, 0))
        assert seen == set(deltas)
        percents = [a.contribution_percentage for a in attributions]
        assert percents == sorted(percents, reverse=True)
        if total != 0:
            assert abs(sum(percents) - 100.0) < 1e-6

    def test_metric_stats(self, analytics_service: AnalyticsService) -> None:
        stats = analytics_service.metric_stats(
            "procurement_costs", "processing_cost", "quarter", "Q3"
        )
        df = pd.read_csv(PROCUREMENT_CSV)
        q3 = df[df["quarter"] == "Q3"]["processing_cost"]
        assert stats["mean"] == q3.mean()
        assert stats["count"] == len(q3)
        assert stats["std_dev"] > 0

    def test_significance_for_delta_significant(self) -> None:
        sig = AnalyticsService.significance_for_delta(10.0, 0.0, 1.0)
        assert sig["significant"] is True
        assert sig["z_score"] > 1.96

    def test_significance_not_significant(self) -> None:
        sig = AnalyticsService.significance_for_delta(5.0, 0.0, 100.0)
        assert sig["significant"] is False

    def test_unknown_metric_raises(self, analytics_service: AnalyticsService) -> None:
        with pytest.raises(AnalyticsServiceError):
            analytics_service.compare_periods(
                "procurement_costs",
                "nonexistent_metric",
                "quarter",
                "Q3",
                "Q2",
            )

    def test_register_custom_metric(self, analytics_service: AnalyticsService) -> None:
        analytics_service.register_custom_metric(
            "custom_ratio",
            "100.0 * SUM(spoiled) / SUM(total)",
            column="total",
        )
        assert "custom_ratio" in analytics_service._custom_metrics
        spec = analytics_service._spec("custom_ratio")
        assert spec["expression"] == "100.0 * SUM(spoiled) / SUM(total)"

    def test_custom_expression_injection_rejected(
        self, analytics_service: AnalyticsService
    ) -> None:
        with pytest.raises(AnalyticsServiceError):
            analytics_service.register_custom_metric(
                "bad", "DROP TABLE procurement_costs", column="x"
            )
        with pytest.raises(AnalyticsServiceError):
            analytics_service.register_custom_metric(
                "bad2", "SELECT * FROM procurement_costs", column="x"
            )
        with pytest.raises(AnalyticsServiceError):
            analytics_service.register_custom_metric(
                "bad3", "1; DELETE FROM procurement_costs", column="x"
            )

    def test_compute_dynamic_metric(self, analytics_service: AnalyticsService) -> None:
        value = analytics_service.compute_dynamic_metric(
            "procurement_costs",
            "SUM(processing_cost)",
            where_sql="quarter = ?",
            parameters=["Q3"],
        )
        df = pd.read_csv(PROCUREMENT_CSV)
        expected = df[df["quarter"] == "Q3"]["processing_cost"].sum()
        assert value == expected

    def test_available_metrics_documented(self) -> None:
        assert "processing_cost" in METRIC_REGISTRY
        assert "avg_processing_cost" in METRIC_REGISTRY
        assert "order_count" in METRIC_REGISTRY


class TestDecisionService:
    """Tests for the decision orchestration service."""

    def test_analyze_kpi_performance(self, decision_service: DecisionService) -> None:
        df = pd.read_csv(PROCUREMENT_CSV)
        q2 = df[df["quarter"] == "Q2"]["processing_cost"].sum()
        q3 = df[df["quarter"] == "Q3"]["processing_cost"].sum()
        result = decision_service.analyze_kpi_performance(
            KPICalculationRequest(
                metric_name="processing_cost",
                source_uri=PROCUREMENT_CSV,
                timeframe_column="quarter",
                timeframe_current="Q3",
                timeframe_baseline="Q2",
            )
        )
        assert isinstance(result, PerformanceAnalysisResult)
        assert result.success is True
        assert result.baseline_value == q2
        assert result.current_value == q3
        assert result.delta_percentage == (q3 - q2) / q2 * 100.0

    def test_analyze_kpi_performance_custom_expression(self) -> None:
        # A dataset without "processing_cost" but with spoilage columns.
        rows = [
            {"plant_id": "P1", "timeframe": "August", "spoiled": 10, "total": 1000},
            {"plant_id": "P1", "timeframe": "July", "spoiled": 5, "total": 1000},
            {"plant_id": "P2", "timeframe": "August", "spoiled": 20, "total": 1000},
            {"plant_id": "P2", "timeframe": "July", "spoiled": 10, "total": 1000},
        ]
        fd, path = tempfile.mkstemp(suffix=".csv")
        with os.fdopen(fd, "w") as fh:
            pd.DataFrame(rows).to_csv(fh, index=False)
        try:
            svc = DecisionService()
            result = svc.analyze_kpi_performance(
                KPICalculationRequest(
                    metric_name="spoilage_rate",
                    source_uri=path,
                    timeframe_column="timeframe",
                    timeframe_current="August",
                    timeframe_baseline="July",
                    custom_expression="100.0 * SUM(spoiled) / SUM(total)",
                )
            )
            assert result.success is True
            aug = 30.0 / 2000.0 * 100.0
            jul = 15.0 / 2000.0 * 100.0
            assert abs(result.current_value - aug) < 1e-6
            assert abs(result.baseline_value - jul) < 1e-6
        finally:
            os.remove(path)

    def test_explain_kpi_deviation_significant(
        self, decision_service: DecisionService
    ) -> None:
        df = pd.read_csv(PROCUREMENT_CSV)
        q2 = df[df["quarter"] == "Q2"]["processing_cost"].sum()
        q3 = df[df["quarter"] == "Q3"]["processing_cost"].sum()
        observed_delta = q3 - q2
        explanation = decision_service.explain_kpi_deviation(
            KPIExplanationRequest(
                metric_name="processing_cost",
                source_uri=PROCUREMENT_CSV,
                timeframe_column="quarter",
                timeframe_current="Q3",
                timeframe_baseline="Q2",
                observed_delta=observed_delta,
                dimension="department",
            )
        )
        assert isinstance(explanation, DeviationExplanation)
        assert explanation.success is True
        assert explanation.metric_name == "processing_cost"
        assert explanation.observed_delta == observed_delta
        assert explanation.dimension == "department"
        assert explanation.data_insufficient is False
        assert explanation.warning is None
        assert explanation.statistical_significance["significant"] == 1.0
        assert len(explanation.top_contributors) >= 1
        # The top contributor must be named in the explanation.
        assert explanation.top_contributors[0].segment_name in explanation.explanation
        assert str(observed_delta) in explanation.explanation

    def test_explain_kpi_deviation_infers_dimension(self) -> None:
        # Dataset without a "department" column - dimension should be inferred.
        rows = [
            {"plant_id": "P1", "quarter": "Q2", "processing_cost": 100},
            {"plant_id": "P2", "quarter": "Q2", "processing_cost": 200},
            {"plant_id": "P1", "quarter": "Q3", "processing_cost": 500},
            {"plant_id": "P2", "quarter": "Q3", "processing_cost": 300},
        ]
        fd, path = tempfile.mkstemp(suffix=".csv")
        with os.fdopen(fd, "w") as fh:
            pd.DataFrame(rows).to_csv(fh, index=False)
        try:
            svc = DecisionService()
            explanation = svc.explain_kpi_deviation(
                KPIExplanationRequest(
                    metric_name="processing_cost",
                    source_uri=path,
                    timeframe_column="quarter",
                    timeframe_current="Q3",
                    timeframe_baseline="Q2",
                    observed_delta=400.0,
                    dimension=None,
                )
            )
            assert explanation.success is True
            # Dimension should be inferred as "plant_id" (the only text col).
            assert explanation.dimension == "plant_id"
            assert not explanation.data_insufficient
        finally:
            os.remove(path)

    def test_explain_kpi_deviation_missing_data(
        self, decision_service: DecisionService
    ) -> None:
        explanation = decision_service.explain_kpi_deviation(
            KPIExplanationRequest(
                metric_name="processing_cost",
                source_uri=PROCUREMENT_CSV,
                timeframe_column="quarter",
                timeframe_current="Q9",
                timeframe_baseline="Q8",
                observed_delta=9999.0,
                dimension="department",
            )
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
                    source_uri=path,
                    timeframe_column="quarter",
                    timeframe_current="Q3",
                    timeframe_baseline="Q2",
                    observed_delta=5.0,
                    dimension="department",
                )
            )
            assert explanation.data_insufficient is True
            assert explanation.warning is not None
            assert "not statistically significant" in explanation.warning
        finally:
            os.remove(path)

    def test_generate_brief_structure(self, decision_service: DecisionService) -> None:
        df = pd.read_csv(PROCUREMENT_CSV)
        q3 = df[df["quarter"] == "Q3"]["processing_cost"].sum()
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
        assert brief.findings[0].current_value == q3
        assert len(brief.attributions) >= 1
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
