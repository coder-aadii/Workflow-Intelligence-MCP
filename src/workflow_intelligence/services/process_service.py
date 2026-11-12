"""Process Discovery service for business process analysis."""

from __future__ import annotations

from workflow_intelligence.adapters.file_storage.loader import (
    FileStorageError,
    get_event_log_columns,
    load_event_log,
)
from workflow_intelligence.adapters.process_mining.pm4py_adapter import (
    PM4PyAdapter,
    PM4PyAdapterError,
)
from workflow_intelligence.schemas.process import (
    AutomationCandidate,
    AutomationScoreReport,
    AutomationScoreRequest,
    BottleneckItem,
    BottleneckReport,
    BottleneckRequest,
    ProcessAnalysisResult,
    ProcessAnalyzeRequest,
    StageMetric,
)

# PM4Py standard column names used after format_event_log
_CASE_ID = "case:concept:name"
_ACTIVITY = "concept:name"
_TIMESTAMP = "time:timestamp"


class ProcessServiceError(Exception):
    """Raised when process discovery service operations fail."""


def _detect_columns(columns: list[str]) -> tuple[str, str, str]:
    """Heuristically detect case/activity/timestamp columns from a header list.

    Matches columns case-insensitively against common names, falling back to
    positional columns when no match is found.

    Args:
        columns: List of column names from the event log header.

    Returns:
        Tuple of (case_id_col, activity_col, timestamp_col).
    """
    lower = {c.lower(): c for c in columns}

    case_candidates = ("case_id", "caseid", "case", "id")
    activity_candidates = ("activity", "act", "activity_name", "task")
    timestamp_candidates = (
        "timestamp",
        "time",
        "time:timestamp",
        "datetime",
        "date",
        "event_time",
    )

    def _first_match(candidates: tuple[str, ...]) -> str | None:
        for cand in candidates:
            if cand in lower:
                return lower[cand]
        return None

    case_id_col = _first_match(case_candidates) or columns[0]
    activity_col = _first_match(activity_candidates) or (
        columns[1] if len(columns) > 1 else columns[0]
    )
    timestamp_col = _first_match(timestamp_candidates) or (
        columns[2] if len(columns) > 2 else columns[-1]
    )

    return case_id_col, activity_col, timestamp_col


class ProcessService:
    """Service for process discovery, bottleneck detection, and automation scoring."""

    def __init__(self) -> None:
        self._loader = load_event_log
        self._columns = get_event_log_columns
        self._pm4py = PM4PyAdapter()

    def analyze_business_process(
        self, request: ProcessAnalyzeRequest
    ) -> ProcessAnalysisResult:
        """Analyze a business process event log and reconstruct execution paths.

        Args:
            request: Process analysis request with source URI and column names.

        Returns:
            ProcessAnalysisResult with case statistics, variants, and transitions.

        Raises:
            ProcessServiceError: If loading or mining fails.
        """
        try:
            df = self._loader(
                source_uri=request.source_uri,
                case_id_col=request.case_id_col,
                activity_col=request.activity_col,
                timestamp_col=request.timestamp_col,
            )
        except FileStorageError as exc:
            raise ProcessServiceError(str(exc)) from exc

        try:
            formatted = self._pm4py.format_event_log(
                df,
                case_id_col=request.case_id_col,
                activity_col=request.activity_col,
                timestamp_col=request.timestamp_col,
            )
        except PM4PyAdapterError as exc:
            raise ProcessServiceError(str(exc)) from exc

        # Basic case/event statistics using PM4Py standard column names
        total_cases = formatted[_CASE_ID].nunique()
        total_events = len(formatted)
        unique_activities = sorted(formatted[_ACTIVITY].unique().tolist())

        # Variants
        variants = self._pm4py.discover_variants(formatted)
        variant_count = len(variants)
        top_variants = sorted(variants.items(), key=lambda x: x[1], reverse=True)[:10]
        top_variants_list = [{k: v} for k, v in top_variants]

        # Average case duration
        case_durations: list[float] = []
        for _, case_group in formatted.groupby(_CASE_ID):
            start = case_group[_TIMESTAMP].min()
            end = case_group[_TIMESTAMP].max()
            duration_hours = (end - start).total_seconds() / 3600.0
            case_durations.append(duration_hours)
        avg_case_duration = (
            sum(case_durations) / len(case_durations) if case_durations else 0.0
        )

        # DFG and performance DFG
        dfg = self._pm4py.discover_dfg(formatted)
        perf_dfg = self._pm4py.discover_performance_dfg(formatted)

        # Build transitions
        transitions: list[StageMetric] = []
        for (source, target), count in sorted(dfg.items()):
            perf = perf_dfg.get((source, target), {})
            transitions.append(
                StageMetric(
                    source_activity=source,
                    target_activity=target,
                    transition_count=count,
                    median_duration_hours=perf.get("median_duration_hours", 0.0),
                    mean_duration_hours=perf.get("mean_duration_hours", 0.0),
                    std_dev_hours=perf.get("std_dev_hours", 0.0),
                    is_bottleneck=False,
                )
            )

        return ProcessAnalysisResult(
            total_cases=total_cases,
            total_events=total_events,
            unique_activities=unique_activities,
            variant_count=variant_count,
            top_variants=top_variants_list,
            average_case_duration_hours=avg_case_duration,
            transitions=transitions,
        )

    def _resolve_columns(self, source_uri: str) -> tuple[str, str, str]:
        """Resolve case/activity/timestamp columns for an event log.

        Inspects the file header and heuristically detects column names so
        callers do not need to know exact column spellings in advance.

        Args:
            source_uri: Path to the event log file.

        Returns:
            Tuple of (case_id_col, activity_col, timestamp_col).

        Raises:
            ProcessServiceError: If column inspection fails.
        """
        try:
            columns = self._columns(source_uri)
        except FileStorageError as exc:
            raise ProcessServiceError(str(exc)) from exc
        return _detect_columns(columns)

    def identify_bottlenecks(self, request: BottleneckRequest) -> BottleneckReport:
        """Identify operational bottlenecks using z-score deviation analysis.

        Args:
            request: Bottleneck request with source URI and threshold.

        Returns:
            BottleneckReport with stages exceeding the z-score threshold.

        Raises:
            ProcessServiceError: If loading or mining fails.
        """
        case_id_col, activity_col, timestamp_col = self._resolve_columns(
            request.source_uri
        )

        analysis_request = ProcessAnalyzeRequest(
            source_uri=request.source_uri,
            case_id_col=case_id_col,
            activity_col=activity_col,
            timestamp_col=timestamp_col,
        )

        analysis = self.analyze_business_process(analysis_request)

        # Compute global baseline: mean of all transition mean durations
        all_means = [
            t.mean_duration_hours
            for t in analysis.transitions
            if t.mean_duration_hours > 0
        ]
        if not all_means:
            return BottleneckReport(
                source_uri=request.source_uri,
                total_stages_analyzed=len(analysis.transitions),
                bottlenecks=[],
            )

        baseline_mean = sum(all_means) / len(all_means)
        baseline_std = (
            sum((m - baseline_mean) ** 2 for m in all_means) / len(all_means)
        ) ** 0.5
        if baseline_std == 0:
            baseline_std = 1.0

        bottlenecks: list[BottleneckItem] = []
        for t in analysis.transitions:
            if t.mean_duration_hours <= 0:
                continue
            z_score = (t.mean_duration_hours - baseline_mean) / baseline_std
            if z_score >= request.threshold_std_dev:
                bottlenecks.append(
                    BottleneckItem(
                        stage_transition=(
                            t.source_activity + " -> " + t.target_activity
                        ),
                        avg_duration_hours=t.mean_duration_hours,
                        baseline_duration_hours=baseline_mean,
                        z_score=z_score,
                        affected_case_count=t.transition_count,
                        primary_contributor=("Excessive waiting time at stage"),
                        recommendation=(
                            "Review and optimize the "
                            + t.source_activity
                            + " to "
                            + t.target_activity
                            + " transition to reduce delays."
                        ),
                    )
                )

        bottlenecks.sort(key=lambda b: b.z_score, reverse=True)

        return BottleneckReport(
            source_uri=request.source_uri,
            total_stages_analyzed=len(analysis.transitions),
            bottlenecks=bottlenecks,
        )

    def score_automation_opportunities(
        self, request: AutomationScoreRequest
    ) -> AutomationScoreReport:
        """Score repetitive activities for automation suitability.

        Uses formula: Score = f(frequency, duration, 1/variance)
        where higher frequency, longer duration, and lower variance increase score.

        Args:
            request: Automation scoring request with source URI and min frequency.

        Returns:
            AutomationScoreReport with ranked candidates.

        Raises:
            ProcessServiceError: If loading or mining fails.
        """
        case_id_col, activity_col, timestamp_col = self._resolve_columns(
            request.source_uri
        )

        try:
            analysis = self.analyze_business_process(
                ProcessAnalyzeRequest(
                    source_uri=request.source_uri,
                    case_id_col=case_id_col,
                    activity_col=activity_col,
                    timestamp_col=timestamp_col,
                )
            )
        except ProcessServiceError:
            raise

        # Count activity frequencies from transitions
        activity_freq: dict[str, int] = {}
        activity_durations: dict[str, list[float]] = {}
        for t in analysis.transitions:
            for activity in (t.source_activity, t.target_activity):
                activity_freq[activity] = (
                    activity_freq.get(activity, 0) + t.transition_count
                )
                activity_durations.setdefault(activity, []).append(
                    t.mean_duration_hours
                )

        # Filter by min frequency
        candidates: list[AutomationCandidate] = []
        for activity, freq in activity_freq.items():
            if freq < request.min_frequency:
                continue

            durations = activity_durations.get(activity, [0.0])
            avg_minutes = (sum(durations) / len(durations)) * 60.0 if durations else 0.0
            variance_minutes = (
                (sum((d - avg_minutes / 60.0) ** 2 for d in durations) / len(durations))
                * 3600.0
                if len(durations) > 1
                else 0.0
            )
            std_minutes = variance_minutes**0.5

            # Manual effort score: higher duration and frequency = more effort
            manual_effort = min(100.0, (avg_minutes / 10.0) + (freq / 5.0))

            # Automation potential: frequency * duration / (1 + variance)
            variance_factor = 1.0 / (1.0 + std_minutes)
            automation_potential = min(
                100.0, freq * avg_minutes * variance_factor / 100.0
            )

            candidates.append(
                AutomationCandidate(
                    activity_name=activity,
                    execution_frequency=freq,
                    avg_duration_minutes=avg_minutes,
                    standard_deviation_minutes=std_minutes,
                    manual_effort_score=round(manual_effort, 2),
                    automation_potential_score=round(automation_potential, 2),
                    rank=0,
                )
            )

        # Rank by automation potential score (descending)
        candidates.sort(key=lambda c: c.automation_potential_score, reverse=True)
        for idx, c in enumerate(candidates, start=1):
            c.rank = idx

        return AutomationScoreReport(
            candidates=candidates,
            methodology="Score = f(frequency, duration, 1/variance)",
        )
