"""Pydantic V2 schemas for the Process Discovery Engine (process_* tools)."""

from __future__ import annotations

from pydantic import BaseModel, Field

from .common import BaseMCPResponse


class ProcessAnalyzeRequest(BaseModel):
    """Request to analyze a business process event log.

    Attributes:
        source_uri: URI or path to the event log (CSV, JSON, SQL).
        case_id_col: Column name for case identifiers.
        activity_col: Column name for activity names.
        timestamp_col: Column name for event timestamps.
    """

    source_uri: str = Field(
        ..., min_length=1, description="URI or path to the event log."
    )
    case_id_col: str = Field(
        ..., min_length=1, description="Column name for case identifiers."
    )
    activity_col: str = Field(
        ..., min_length=1, description="Column name for activity names."
    )
    timestamp_col: str = Field(
        ..., min_length=1, description="Column name for event timestamps."
    )


class BottleneckRequest(BaseModel):
    """Request to identify bottlenecks in a process event log.

    Attributes:
        source_uri: URI or path to the event log.
        threshold_std_dev: Standard deviation threshold for anomaly detection.
    """

    source_uri: str = Field(
        ..., min_length=1, description="URI or path to the event log."
    )
    threshold_std_dev: float = Field(
        default=2.0,
        gt=0,
        description="Standard deviation threshold for anomaly detection.",
    )


class AutomationScoreRequest(BaseModel):
    """Request to score automation opportunities in a process event log.

    Attributes:
        source_uri: URI or path to the event log.
        min_frequency: Minimum frequency threshold for candidate activities.
    """

    source_uri: str = Field(
        ..., min_length=1, description="URI or path to the event log."
    )
    min_frequency: int = Field(
        default=10,
        ge=1,
        description="Minimum frequency threshold for candidate activities.",
    )


class StageMetric(BaseModel):
    """Metric describing a transition between two activities.

    Attributes:
        source_activity: Source activity name.
        target_activity: Target activity name.
        transition_count: Number of observed transitions.
        median_duration_hours: Median duration in hours.
        mean_duration_hours: Mean duration in hours.
        std_dev_hours: Standard deviation of duration in hours.
        is_bottleneck: Whether this transition is flagged as a bottleneck.
    """

    source_activity: str = Field(..., description="Source activity name.")
    target_activity: str = Field(..., description="Target activity name.")
    transition_count: int = Field(
        default=0, ge=0, description="Number of observed transitions."
    )
    median_duration_hours: float = Field(
        default=0.0, ge=0, description="Median duration in hours."
    )
    mean_duration_hours: float = Field(
        default=0.0, ge=0, description="Mean duration in hours."
    )
    std_dev_hours: float = Field(
        default=0.0, ge=0, description="Standard deviation of duration in hours."
    )
    is_bottleneck: bool = Field(
        default=False, description="Whether this transition is flagged as a bottleneck."
    )


class ProcessAnalysisResult(BaseMCPResponse):
    """Result of business process analysis.

    Attributes:
        total_cases: Total number of cases analyzed.
        total_events: Total number of events analyzed.
        unique_activities: List of unique activity names.
        variant_count: Number of distinct execution variants.
        top_variants: Top execution variants with frequencies.
        average_case_duration_hours: Average case duration in hours.
        transitions: List of transition metrics between activities.
    """

    total_cases: int = Field(
        default=0, ge=0, description="Total number of cases analyzed."
    )
    total_events: int = Field(
        default=0, ge=0, description="Total number of events analyzed."
    )
    unique_activities: list[str] = Field(
        default_factory=list, description="List of unique activity names."
    )
    variant_count: int = Field(
        default=0, ge=0, description="Number of distinct execution variants."
    )
    top_variants: list[dict[str, int]] = Field(
        default_factory=list,
        description="Top execution variants with frequencies.",
    )
    average_case_duration_hours: float = Field(
        default=0.0,
        ge=0,
        description="Average case duration in hours.",
    )
    transitions: list[StageMetric] = Field(
        default_factory=list,
        description="List of transition metrics between activities.",
    )


class BottleneckItem(BaseModel):
    """A detected process bottleneck.

    Attributes:
        stage_transition: Stage transition identifier (e.g. "A -> B").
        avg_duration_hours: Average duration in hours.
        baseline_duration_hours: Baseline duration in hours.
        z_score: Z-score of the delay relative to baseline.
        affected_case_count: Number of cases affected by this bottleneck.
        primary_contributor: Optional primary contributing factor.
        recommendation: Remediation recommendation.
    """

    stage_transition: str = Field(
        ..., description='Stage transition identifier (e.g. "A -> B").'
    )
    avg_duration_hours: float = Field(
        default=0.0, ge=0, description="Average duration in hours."
    )
    baseline_duration_hours: float = Field(
        default=0.0, ge=0, description="Baseline duration in hours."
    )
    z_score: float = Field(
        default=0.0, description="Z-score of the delay relative to baseline."
    )
    affected_case_count: int = Field(
        default=0, ge=0, description="Number of cases affected by this bottleneck."
    )
    primary_contributor: str | None = Field(
        default=None, description="Optional primary contributing factor."
    )
    recommendation: str = Field(..., description="Remediation recommendation.")


class BottleneckReport(BaseMCPResponse):
    """Report of detected process bottlenecks.

    Attributes:
        source_uri: URI of the analyzed event log.
        total_stages_analyzed: Total number of stages analyzed.
        bottlenecks: Filtered list of bottleneck items.
    """

    source_uri: str = Field(..., description="URI of the analyzed event log.")
    total_stages_analyzed: int = Field(
        default=0, ge=0, description="Total number of stages analyzed."
    )
    bottlenecks: list[BottleneckItem] = Field(
        default_factory=list,
        description="Filtered list of bottleneck items.",
    )


class AutomationCandidate(BaseModel):
    """A ranked automation opportunity candidate.

    Attributes:
        activity_name: Activity name.
        execution_frequency: Observed frequency of the activity.
        avg_duration_minutes: Average duration in minutes.
        standard_deviation_minutes: Standard deviation of duration in minutes.
        manual_effort_score: Manual effort score (0-100).
        automation_potential_score: Automation potential score (0-100).
        rank: Rank among candidates (1 = highest priority).
    """

    activity_name: str = Field(..., description="Activity name.")
    execution_frequency: int = Field(
        default=0, ge=0, description="Observed frequency of the activity."
    )
    avg_duration_minutes: float = Field(
        default=0.0, ge=0, description="Average duration in minutes."
    )
    standard_deviation_minutes: float = Field(
        default=0.0, ge=0, description="Standard deviation of duration in minutes."
    )
    manual_effort_score: float = Field(
        default=0.0,
        ge=0,
        le=100,
        description="Manual effort score (0-100).",
    )
    automation_potential_score: float = Field(
        default=0.0,
        ge=0,
        le=100,
        description="Automation potential score (0-100).",
    )
    rank: int = Field(
        default=0, ge=0, description="Rank among candidates (1 = highest priority)."
    )


class AutomationScoreReport(BaseMCPResponse):
    """Report of scored automation opportunities.

    Attributes:
        candidates: Ranked list of automation candidates.
        methodology: Scoring methodology description.
    """

    candidates: list[AutomationCandidate] = Field(
        default_factory=list, description="Ranked list of automation candidates."
    )
    methodology: str = Field(..., description="Scoring methodology description.")
