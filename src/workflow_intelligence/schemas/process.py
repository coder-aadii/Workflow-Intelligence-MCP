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


class TransitionMetric(BaseModel):
    """Metric describing a transition between two activities.

    Attributes:
        source_activity: Source activity name.
        target_activity: Target activity name.
        transition_count: Number of observed transitions.
        mean_duration_hours: Mean duration in hours.
        median_duration_hours: Median duration in hours.
        std_dev_duration_hours: Standard deviation of duration in hours.
    """

    source_activity: str = Field(..., description="Source activity name.")
    target_activity: str = Field(..., description="Target activity name.")
    transition_count: int = Field(
        default=0, ge=0, description="Number of observed transitions."
    )
    mean_duration_hours: float = Field(
        default=0.0, ge=0, description="Mean duration in hours."
    )
    median_duration_hours: float = Field(
        default=0.0, ge=0, description="Median duration in hours."
    )
    std_dev_duration_hours: float = Field(
        default=0.0, ge=0, description="Standard deviation of duration in hours."
    )


class ProcessAnalysisResult(BaseMCPResponse):
    """Result of business process analysis.

    Attributes:
        total_cases: Total number of cases analyzed.
        total_events: Total number of events analyzed.
        unique_activities: List of unique activity names.
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
    transitions: list[TransitionMetric] = Field(
        default_factory=list,
        description="List of transition metrics between activities.",
    )


class BottleneckReport(BaseMCPResponse):
    """Report of detected process bottlenecks.

    Attributes:
        stages: Filtered list of stages with abnormal cycle times.
        threshold_std_dev: Standard deviation threshold used for detection.
    """

    stages: list[TransitionMetric] = Field(
        default_factory=list,
        description="Filtered list of stages with abnormal cycle times.",
    )
    threshold_std_dev: float = Field(
        default=2.0,
        gt=0,
        description="Standard deviation threshold used for detection.",
    )


class AutomationCandidate(BaseModel):
    """A ranked automation opportunity candidate.

    Attributes:
        activity: Activity name.
        frequency: Observed frequency of the activity.
        avg_duration_hours: Average duration in hours.
        variance_hours: Variance of duration in hours.
        automation_score: Composite automation suitability score (0-100).
    """

    activity: str = Field(..., description="Activity name.")
    frequency: int = Field(
        default=0, ge=0, description="Observed frequency of the activity."
    )
    avg_duration_hours: float = Field(
        default=0.0, ge=0, description="Average duration in hours."
    )
    variance_hours: float = Field(
        default=0.0, ge=0, description="Variance of duration in hours."
    )
    automation_score: float = Field(
        default=0.0,
        ge=0,
        le=100,
        description="Composite automation suitability score (0-100).",
    )


class AutomationScoreReport(BaseMCPResponse):
    """Report of scored automation opportunities.

    Attributes:
        candidates: Ranked list of automation candidates.
        min_frequency: Minimum frequency threshold used for scoring.
    """

    candidates: list[AutomationCandidate] = Field(
        default_factory=list, description="Ranked list of automation candidates."
    )
    min_frequency: int = Field(
        default=10,
        ge=1,
        description="Minimum frequency threshold used for scoring.",
    )
