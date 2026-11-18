"""Pydantic V2 schemas for Business Decision Intelligence (decision_* tools)."""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field, field_validator

from .common import BaseMCPResponse

IDENTIFIER_REGEX = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class KPICalculationRequest(BaseModel):
    """Request to calculate KPI performance across two timeframes.

    Universal contract: the caller supplies the dataset URI and the
    timeframe column/values, so the tool works against any enterprise
    dataset with zero hardcoded procurement assumptions.

    Attributes:
        metric_name: Name of the KPI metric (or "custom" for custom_expression).
        source_uri: Path or URI to the dataset (CSV/Parquet).
        timeframe_column: Column used for period filtering.
        timeframe_current: Current period identifier.
        timeframe_baseline: Baseline period identifier.
        custom_expression: Optional dynamic DuckDB SQL aggregation expression
            (e.g. ``"100.0 * SUM(spoiled) / SUM(total)"``).
    """

    metric_name: str = Field(..., min_length=1, description="Name of the KPI metric.")
    source_uri: str = Field(
        ..., min_length=1, description="Path or URI to the dataset (CSV/Parquet)."
    )
    timeframe_column: str = Field(
        default="timeframe",
        description="Column used for period filtering.",
    )
    timeframe_current: str = Field(
        ..., min_length=1, description="Current period identifier."
    )
    timeframe_baseline: str = Field(
        ..., min_length=1, description="Baseline period identifier."
    )
    custom_expression: str | None = Field(
        default=None,
        description=(
            "Optional dynamic DuckDB SQL aggregation expression, e.g. "
            "'100.0 * SUM(spoiled) / SUM(total)'."
        ),
    )

    @field_validator("timeframe_column")
    @classmethod
    def validate_timeframe_column(cls, v: str) -> str:
        if not IDENTIFIER_REGEX.fullmatch(v):
            raise ValueError(f"Invalid SQL column identifier: {v!r}")
        return v


class KPIExplanationRequest(BaseModel):
    """Request to explain a KPI deviation.

    Universal contract: the caller supplies the dataset URI and the
    timeframe column/values, so the tool works against any enterprise
    dataset with zero hardcoded procurement assumptions.

    Attributes:
        metric_name: Name of the KPI metric.
        source_uri: Path or URI to the dataset (CSV/Parquet).
        timeframe_column: Column used for period filtering.
        timeframe_current: Current period identifier.
        timeframe_baseline: Baseline period identifier.
        observed_delta: Observed delta value.
        dimension: Optional dimension to segment the deviation by.
        custom_expression: Optional dynamic DuckDB SQL aggregation expression.
    """

    metric_name: str = Field(..., min_length=1, description="Name of the KPI metric.")
    source_uri: str = Field(
        ..., min_length=1, description="Path or URI to the dataset (CSV/Parquet)."
    )
    timeframe_column: str = Field(
        default="timeframe",
        description="Column used for period filtering.",
    )
    timeframe_current: str = Field(
        ..., min_length=1, description="Current period identifier."
    )
    timeframe_baseline: str = Field(
        ..., min_length=1, description="Baseline period identifier."
    )
    observed_delta: float = Field(..., description="Observed delta value.")
    dimension: str | None = Field(
        default=None,
        description="Optional dimension to segment the deviation by.",
    )
    custom_expression: str | None = Field(
        default=None,
        description=(
            "Optional dynamic DuckDB SQL aggregation expression, e.g. "
            "'100.0 * SUM(spoiled) / SUM(total)'."
        ),
    )

    @field_validator("timeframe_column")
    @classmethod
    def validate_timeframe_column(cls, v: str) -> str:
        if not IDENTIFIER_REGEX.fullmatch(v):
            raise ValueError(f"Invalid SQL column identifier: {v!r}")
        return v

    @field_validator("dimension")
    @classmethod
    def validate_dimension(cls, v: str | None) -> str | None:
        if v is not None and not IDENTIFIER_REGEX.fullmatch(v):
            raise ValueError(f"Invalid SQL dimension identifier: {v!r}")
        return v


class DecisionBriefRequest(BaseModel):
    """Request to generate a structured decision brief.

    Attributes:
        topic: Topic of the decision brief.
        metric_ids: List of metric identifiers to include.
    """

    topic: str = Field(..., min_length=1, description="Topic of the decision brief.")
    metric_ids: list[str] = Field(
        default_factory=list,
        description="List of metric identifiers to include.",
    )
    source_uri: str | None = Field(
        default=None, description="Optional path or URI to the dataset."
    )
    timeframe_column: str | None = Field(
        default=None, description="Optional column used for period filtering."
    )
    timeframe_current: str | None = Field(
        default=None, description="Optional current period identifier."
    )
    timeframe_baseline: str | None = Field(
        default=None, description="Optional baseline period identifier."
    )

    @field_validator("timeframe_column")
    @classmethod
    def validate_timeframe_column(cls, v: str | None) -> str | None:
        if v is not None and not IDENTIFIER_REGEX.fullmatch(v):
            raise ValueError(f"Invalid SQL column identifier: {v!r}")
        return v


class MetricComparison(BaseModel):
    """Period-over-period comparison of a KPI metric.

    Attributes:
        metric_name: Name of the KPI metric.
        baseline_value: Baseline period value.
        current_value: Current period value.
        absolute_variance: Absolute difference between current and baseline.
        relative_variance_percentage: Relative variance as a percentage.
    """

    metric_name: str = Field(..., description="Name of the KPI metric.")
    baseline_value: float = Field(..., description="Baseline period value.")
    current_value: float = Field(..., description="Current period value.")
    absolute_variance: float = Field(
        ..., description="Absolute difference between current and baseline."
    )
    relative_variance_percentage: float = Field(
        ..., description="Relative variance as a percentage."
    )


class DimensionAttribution(BaseModel):
    """Attribution of a KPI deviation to a specific operational segment.

    Attributes:
        segment_name: Name of the operational segment.
        contribution_percentage: Net percentage contribution to the total delta (can be negative or >100%).
        record_count: Number of records supporting the attribution.
        delta: Actual signed delta for this segment.
        gross_share_percentage: Optional gross absolute-change share (0-100%).
    """

    segment_name: str = Field(..., description="Name of the operational segment.")
    contribution_percentage: float = Field(
        default=0.0,
        description="Net percentage contribution to the total delta (can be negative or >100%).",
    )
    record_count: int = Field(
        default=0, ge=0, description="Number of records supporting the attribution."
    )
    delta: float = Field(
        default=0.0,
        description="Actual signed delta for this segment.",
    )
    gross_share_percentage: float | None = Field(
        default=None,
        ge=0,
        le=100,
        description="Gross absolute-change share percentage (0-100%).",
    )


class DecisionBrief(BaseMCPResponse):
    """Structured evidence-backed decision brief.

    Attributes:
        topic: Topic of the brief.
        executive_summary: High-level executive summary.
        findings: List of metric comparisons supporting the findings.
        attributions: List of dimension attributions explaining drivers.
        provenance_links: List of verifiable data provenance links.
        recommendations: List of actionable recommendations.
    """

    topic: str = Field(..., description="Topic of the brief.")
    executive_summary: str = Field(..., description="High-level executive summary.")
    findings: list[MetricComparison] = Field(
        default_factory=list,
        description="List of metric comparisons supporting the findings.",
    )
    attributions: list[DimensionAttribution] = Field(
        default_factory=list,
        description="List of dimension attributions explaining drivers.",
    )
    provenance_links: list[str] = Field(
        default_factory=list,
        description="List of verifiable data provenance links.",
    )
    recommendations: list[str] = Field(
        default_factory=list, description="List of actionable recommendations."
    )


class DeviationExplanation(BaseMCPResponse):
    """Explanation of a KPI deviation attributed to verified dimensional drivers.

    Attributes:
        metric_name: Name of the KPI metric being explained.
        observed_delta: Observed delta between current and baseline.
        dimension: Optional dimension used to segment the deviation.
        top_contributors: Dimension attributions ranked by contribution to the delta.
        statistical_significance: Deterministic significance indicators
            (std_dev, z_score, significant flag).
        explanation: Natural-language explanation drafted from verified numbers only.
        data_insufficient: True when data is missing or statistically insignificant.
        warning: Non-fatal warning when results should be interpreted with caution.
    """

    metric_name: str = Field(..., description="Name of the KPI metric being explained.")
    observed_delta: float = Field(
        ..., description="Observed delta between current and baseline."
    )
    dimension: str | None = Field(
        default=None, description="Optional dimension used to segment the deviation."
    )
    top_contributors: list[DimensionAttribution] = Field(
        default_factory=list,
        description="Dimension attributions ranked by contribution to the delta.",
    )
    statistical_significance: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Deterministic significance indicators (std_dev, z_score, "
            "significant flag, standard_error, metric_type)."
        ),
    )
    explanation: str = Field(
        ...,
        description="Natural-language explanation drafted from verified numbers only.",
    )
    data_insufficient: bool = Field(
        default=False,
        description="True when data is missing or statistically insignificant.",
    )
    warning: str | None = Field(
        default=None,
        description=(
            "Non-fatal warning when results should be interpreted with caution."
        ),
    )


class PerformanceAnalysisResult(BaseMCPResponse):
    """Result of KPI performance analysis.

    Attributes:
        metric_name: Name of the KPI metric.
        baseline_value: Baseline period value.
        current_value: Current period value.
        delta_percentage: Percentage change from baseline.
    """

    metric_name: str = Field(..., description="Name of the KPI metric.")
    baseline_value: float = Field(..., description="Baseline period value.")
    current_value: float = Field(..., description="Current period value.")
    delta_percentage: float = Field(..., description="Percentage change from baseline.")
