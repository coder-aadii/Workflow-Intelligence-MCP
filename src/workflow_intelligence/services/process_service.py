"""Process Discovery service for business process analysis."""

from __future__ import annotations

import multiprocessing as mp
import pickle
import time
from collections.abc import Callable
from typing import Any

from workflow_intelligence.adapters.file_storage.loader import (
    FileStorageError,
    get_event_log_columns,
    load_event_log,
)
from workflow_intelligence.adapters.process_mining.pm4py_adapter import (
    PM4PyAdapter,
    PM4PyAdapterError,
)
from workflow_intelligence.config import get_settings
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


CANONICAL_CASE_COLS = ("case_id", "caseid")
CANONICAL_ACTIVITY_COLS = ("activity", "activity_name", "ocel:activity")
CANONICAL_TIMESTAMP_COLS = ("timestamp", "time:timestamp", "ocel:timestamp")

SYNONYM_CASE_COLS = (
    "case_id",
    "caseid",
    "case_number",
    "case_no",
    "case",
    "order_id",
    "claim_id",
    "ticket_id",
    "invoice_id",
    "incident_id",
    "patient_id",
    "correlation_id",
    "trace_id",
    "session_id",
    "run_id",
    "job_id",
    "id",
)
SYNONYM_ACTIVITY_COLS = (
    "activity",
    "activity_name",
    "act",
    "task",
    "task_name",
    "action",
    "status",
    "event",
    "event_type",
    "stage",
    "step",
    "state",
    "operation",
    "milestone",
)
SYNONYM_TIMESTAMP_COLS = (
    "timestamp",
    "time:timestamp",
    "datetime",
    "event_time",
    "event_timestamp",
    "created_at",
    "date",
    "time",
    "ts",
    "start_time",
    "end_time",
    "timestamp_utc",
    "logged_at",
    "occurred_at",
)


def _detect_columns(columns: list[str]) -> tuple[str, str, str]:
    """Heuristically detect case/activity/timestamp columns from a header list.

    Matches columns case-insensitively against canonical names and recognized
    synonyms. Rejects missing, ambiguous, or duplicate mappings without unsafe
    positional fallback. Also recognizes OCEL extended-table column names
    (``ocel:activity``, ``ocel:timestamp``, ``ocel:type:*``).

    Args:
        columns: List of column names from the event log header.

    Returns:
        Tuple of (case_id_col, activity_col, timestamp_col).

    Raises:
        ProcessServiceError: If any role cannot be inferred unambiguously.
    """
    lower = {c.lower(): c for c in columns}

    def _match_role(
        role: str,
        canonical_candidates: tuple[str, ...],
        synonym_candidates: tuple[str, ...],
        ocel_prefix: str | None = None,
    ) -> str:
        # Check OCEL prefix if applicable
        if ocel_prefix:
            ocel_matches = sorted(
                c for c in columns if c.lower().startswith(ocel_prefix)
            )
            if len(ocel_matches) == 1:
                return ocel_matches[0]
            if len(ocel_matches) > 1:
                raise ProcessServiceError(
                    f"Ambiguous column mapping for '{role}': multiple OCEL candidates "
                    f"found {ocel_matches}. Available columns: {columns}. "
                    f"Please specify '{role}_col' explicitly."
                )

        # Check canonical matches
        canonical_matches = [lower[c] for c in canonical_candidates if c in lower]
        if len(canonical_matches) == 1:
            return canonical_matches[0]
        if len(canonical_matches) > 1:
            raise ProcessServiceError(
                f"Ambiguous column mapping for '{role}': multiple canonical candidates "
                f"found {canonical_matches}. Available columns: {columns}. "
                f"Please specify '{role}_col' explicitly."
            )

        # Check broader synonym matches
        synonym_matches = [
            lower[c] for c in synonym_candidates if c in lower and c not in canonical_candidates
        ]
        if len(synonym_matches) == 1:
            return synonym_matches[0]
        if len(synonym_matches) > 1:
            raise ProcessServiceError(
                f"Ambiguous column mapping for '{role}': multiple candidates "
                f"found {synonym_matches}. Available columns: {columns}. "
                f"Please specify '{role}_col' explicitly."
            )

        raise ProcessServiceError(
            f"Could not automatically infer '{role}' column from event log. "
            f"Available columns: {columns}. Please specify '{role}_col' explicitly."
        )

    case_id_col = _match_role(
        "case_id",
        CANONICAL_CASE_COLS,
        SYNONYM_CASE_COLS,
        ocel_prefix="ocel:type:",
    )
    activity_col = _match_role(
        "activity",
        CANONICAL_ACTIVITY_COLS,
        SYNONYM_ACTIVITY_COLS,
    )
    timestamp_col = _match_role(
        "timestamp",
        CANONICAL_TIMESTAMP_COLS,
        SYNONYM_TIMESTAMP_COLS,
    )

    if len({case_id_col, activity_col, timestamp_col}) != 3:
        raise ProcessServiceError(
            f"Inferred overlapping or duplicate columns: case_id='{case_id_col}', "
            f"activity='{activity_col}', timestamp='{timestamp_col}'. "
            f"Each role must map to a distinct column. Available columns: {columns}. "
            "Please specify column mappings explicitly."
        )

    return case_id_col, activity_col, timestamp_col


MAX_ANALYSIS_RESULT_BYTES: int = 50 * 1024 * 1024  # 50 MB


def _terminate_process(p: Any) -> None:
    """Terminate and join a worker process with escalating SIGTERM -> SIGKILL."""
    if p is not None and p.is_alive():
        try:
            p.terminate()
            p.join(timeout=0.5)
            if p.is_alive():
                p.kill()
                p.join(timeout=0.2)
        except Exception:
            pass


def _isolated_analysis_worker(
    conn: Any, fn: Callable[..., Any], args: tuple[Any, ...], kwargs: dict[str, Any]
) -> None:
    """Worker function executed in an isolated child process via OS Pipe."""
    try:
        res = fn(*args, **kwargs)
        # Validate result size before transferring across the Pipe
        serialized = pickle.dumps(res, protocol=pickle.HIGHEST_PROTOCOL)
        if len(serialized) > MAX_ANALYSIS_RESULT_BYTES:
            err = ProcessServiceError(
                f"Analysis result size ({len(serialized)} bytes) exceeds the maximum "
                f"allowed payload limit of {MAX_ANALYSIS_RESULT_BYTES} bytes."
            )
            conn.send(("error", err))
        else:
            conn.send(("success", res))
    except Exception as exc:
        try:
            conn.send(("error", exc))
        except Exception as send_err:
            try:
                conn.send(
                    (
                        "error",
                        ProcessServiceError(
                            f"Analysis failed: {exc!s} (serialization error: {send_err!s})"
                        ),
                    )
                )
            except Exception:
                pass
    finally:
        try:
            conn.close()
        except Exception:
            pass


class ProcessService:
    """Service for process discovery, bottleneck detection, and automation scoring."""

    def __init__(self) -> None:
        self._loader = load_event_log
        self._columns = get_event_log_columns
        self._pm4py = PM4PyAdapter()

    def _run_with_timeout(
        self, fn: Callable[..., Any], *args: Any, **kwargs: Any
    ) -> Any:
        """Execute an analysis function with OS-level process isolation and timeout guardrail.

        Uses an OS-level Pipe (free of background feeder thread deadlocks) and bounds
        both execution and result transfer within ANALYSIS_TIMEOUT_SECONDS. Terminates
        the worker process immediately upon timeout or crash to prevent unbounded
        CPU/memory consumption and resource leakage.
        """
        settings = get_settings()
        timeout = settings.analysis_timeout_seconds
        if not timeout or timeout <= 0:
            return fn(*args, **kwargs)

        ctx = mp.get_context("spawn")
        parent_conn, child_conn = ctx.Pipe(duplex=False)
        p = ctx.Process(
            target=_isolated_analysis_worker,
            args=(child_conn, fn, args, kwargs),
        )

        try:
            p.start()
            # Close child connection in parent process so EOF is raised if child terminates
            try:
                child_conn.close()
            except Exception:
                pass

            deadline = time.monotonic() + timeout
            has_data = False

            # Bounded polling loop: monitors pipe data, worker liveness, and timeout deadline
            while time.monotonic() < deadline:
                remaining = max(0.01, deadline - time.monotonic())
                poll_interval = min(0.2, remaining)
                if parent_conn.poll(timeout=poll_interval):
                    has_data = True
                    break
                if not p.is_alive():
                    # Child process terminated before returning data
                    break

            if not has_data:
                child_exited_early = not p.is_alive()
                exit_code = p.exitcode if child_exited_early else None

                _terminate_process(p)

                if child_exited_early and exit_code not in (None, 0):
                    raise ProcessServiceError(
                        f"Process mining worker terminated unexpectedly (exit code {exit_code}). "
                        f"{'Worker process was likely killed by OOM (Out Of Memory).' if exit_code == -9 else 'Worker crashed.'}"
                    )
                raise ProcessServiceError(
                    f"Process mining analysis timed out after {timeout} seconds. "
                    "Worker process was terminated to prevent memory or CPU exhaustion."
                )

            # Receive result from worker; handle crash or EOF during transfer
            try:
                status, payload = parent_conn.recv()
            except EOFError as exc:
                _terminate_process(p)
                exit_code = p.exitcode
                if exit_code is not None and exit_code != 0:
                    raise ProcessServiceError(
                        f"Process mining worker terminated unexpectedly (exit code {exit_code}). "
                        f"{'Worker process was likely killed by OOM (Out Of Memory).' if exit_code == -9 else 'Worker crashed.'}"
                    ) from exc
                raise ProcessServiceError(
                    "Process mining worker terminated unexpectedly without returning a result."
                ) from exc

            # Verify that total execution and result transfer time respected the timeout
            if time.monotonic() > deadline:
                _terminate_process(p)
                raise ProcessServiceError(
                    f"Process mining analysis and transfer timed out after {timeout} seconds. "
                    "Worker process was terminated."
                )

            p.join(timeout=1.0)
            if status == "error":
                raise payload
            return payload
        finally:
            try:
                parent_conn.close()
            except Exception:
                pass
            try:
                child_conn.close()
            except Exception:
                pass
            _terminate_process(p)
            try:
                p.close()
            except Exception:
                pass

    def analyze_business_process(
        self, request: ProcessAnalyzeRequest
    ) -> ProcessAnalysisResult:
        """Analyze a business process event log and reconstruct execution paths."""
        res: ProcessAnalysisResult = self._run_with_timeout(
            self._analyze_business_process_impl, request
        )
        return res

    def _analyze_business_process_impl(
        self, request: ProcessAnalyzeRequest
    ) -> ProcessAnalysisResult:
        """Reconstruct execution paths from an event log."""
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
                timestamp_format=request.timestamp_format,
            )
        except PM4PyAdapterError as exc:
            raise ProcessServiceError(str(exc)) from exc

        # Basic case/event statistics using PM4Py standard column names
        total_cases = formatted[_CASE_ID].nunique()
        total_events = len(formatted)
        unique_activities = sorted(formatted[_ACTIVITY].unique().tolist())
        raw_act_counts = formatted[_ACTIVITY].value_counts().to_dict()
        activity_frequencies = {str(k): int(v) for k, v in raw_act_counts.items()}

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

        # Check for suspicious data patterns
        warnings: list[str] = []
        if total_cases == total_events and total_events > 1:
            warnings.append(
                f"Suspicious data pattern: every case contains exactly one event "
                f"({total_cases} single-event cases). Verify that column '{request.case_id_col}' "
                "is not a surrogate row identifier."
            )
        elif total_cases == 1 and total_events > 5:
            warnings.append(
                f"Suspicious data pattern: all {total_events} events belong to a single case. "
                f"Verify that column '{request.case_id_col}' is a valid case identifier."
            )
        if len(unique_activities) == 1:
            warnings.append(
                f"Suspicious data pattern: only 1 unique activity ('{unique_activities[0]}') "
                "found across all events."
            )
        if avg_case_duration == 0.0 and total_events > total_cases:
            warnings.append(
                "Suspicious timestamp distribution: average case duration is 0.0 hours. "
                "Verify timestamp precision and column mapping."
            )

        return ProcessAnalysisResult(
            total_cases=total_cases,
            total_events=total_events,
            unique_activities=unique_activities,
            variant_count=variant_count,
            top_variants=top_variants_list,
            average_case_duration_hours=avg_case_duration,
            transitions=transitions,
            activity_frequencies=activity_frequencies,
            warnings=warnings if warnings else None,
        )

    def _resolve_columns(
        self,
        source_uri: str,
        case_id_col: str | None = None,
        activity_col: str | None = None,
        timestamp_col: str | None = None,
    ) -> tuple[str, str, str]:
        """Resolve case/activity/timestamp columns for an event log.

        Uses caller-supplied explicit column mappings if provided, and
        automatically infers any missing column names from the file header.

        Args:
            source_uri: Path to the event log file.
            case_id_col: Optional explicit case ID column name.
            activity_col: Optional explicit activity column name.
            timestamp_col: Optional explicit timestamp column name.

        Returns:
            Tuple of (case_id_col, activity_col, timestamp_col).

        Raises:
            ProcessServiceError: If column inspection fails, required columns are
                missing, ambiguous, or duplicated.
        """
        try:
            columns = self._columns(source_uri)
        except FileStorageError as exc:
            raise ProcessServiceError(str(exc)) from exc

        # If all 3 are explicitly provided:
        if case_id_col and activity_col and timestamp_col:
            if len({case_id_col, activity_col, timestamp_col}) != 3:
                raise ProcessServiceError(
                    f"Columns must be distinct for case_id, activity, and timestamp. "
                    f"Received duplicate mappings: case_id='{case_id_col}', "
                    f"activity='{activity_col}', timestamp='{timestamp_col}'. "
                    f"Available columns: {columns}"
                )
            missing = {case_id_col, activity_col, timestamp_col} - set(columns)
            if missing:
                raise ProcessServiceError(
                    f"Specified column(s) {sorted(missing)} not found in '{source_uri}'. "
                    f"Available columns: {columns}"
                )
            return case_id_col, activity_col, timestamp_col

        inferred_case, inferred_act, inferred_ts = _detect_columns(columns)
        final_case = case_id_col or inferred_case
        final_act = activity_col or inferred_act
        final_ts = timestamp_col or inferred_ts

        if len({final_case, final_act, final_ts}) != 3:
            raise ProcessServiceError(
                f"Resolved overlapping or duplicate columns: case_id='{final_case}', "
                f"activity='{final_act}', timestamp='{final_ts}'. "
                f"Available columns: {columns}. Please specify distinct column mappings."
            )
        missing = {final_case, final_act, final_ts} - set(columns)
        if missing:
            raise ProcessServiceError(
                f"Resolved column(s) {sorted(missing)} not found in '{source_uri}'. "
                f"Available columns: {columns}"
            )
        return final_case, final_act, final_ts

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
            request.source_uri,
            case_id_col=request.case_id_col,
            activity_col=request.activity_col,
            timestamp_col=request.timestamp_col,
        )

        analysis_request = ProcessAnalyzeRequest(
            source_uri=request.source_uri,
            case_id_col=case_id_col,
            activity_col=activity_col,
            timestamp_col=timestamp_col,
            timestamp_format=request.timestamp_format,
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
                warnings=analysis.warnings,
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
            if t.transition_count < request.min_frequency:
                continue
            z_score = (t.mean_duration_hours - baseline_mean) / baseline_std
            if z_score >= request.threshold_std_dev:
                warning = None
                if t.transition_count < 30:
                    warning = (
                        f"Low sample size (n={t.transition_count}). Outlier delay may be an "
                        "isolated edge case rather than a systemic process bottleneck."
                    )
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
                        sample_size_warning=warning,
                    )
                )

        bottlenecks.sort(key=lambda b: b.z_score, reverse=True)

        return BottleneckReport(
            source_uri=request.source_uri,
            total_stages_analyzed=len(analysis.transitions),
            bottlenecks=bottlenecks,
            warnings=analysis.warnings,
        )

    def score_automation_opportunities(
        self, request: AutomationScoreRequest
    ) -> AutomationScoreReport:
        """Score repetitive activities for automation suitability.

        Uses formula: Score = f(frequency, duration, 1/variance)
        where higher frequency, longer duration, and lower variance increase score.

        Activity frequency represents the true observed event count of each activity
        in the event log (not the sum of in/out transition counts).

        Duration and variance are computed from outgoing transitions (falling back to
        incoming transitions for strictly terminal activities).

        Scores are normalized to the [0.0, 100.0] range across candidate activities in
        the process to prevent saturation and enable meaningful ranking.

        Args:
            request: Automation scoring request with source URI and min frequency.

        Returns:
            AutomationScoreReport with ranked candidates.

        Raises:
            ProcessServiceError: If loading or mining fails.
        """
        case_id_col, activity_col, timestamp_col = self._resolve_columns(
            request.source_uri,
            case_id_col=request.case_id_col,
            activity_col=request.activity_col,
            timestamp_col=request.timestamp_col,
        )

        try:
            analysis = self.analyze_business_process(
                ProcessAnalyzeRequest(
                    source_uri=request.source_uri,
                    case_id_col=case_id_col,
                    activity_col=activity_col,
                    timestamp_col=timestamp_col,
                    timestamp_format=request.timestamp_format,
                )
            )
        except ProcessServiceError:
            raise

        # Map outgoing and incoming transitions per activity
        outgoing_transitions: dict[str, list[StageMetric]] = {}
        incoming_transitions: dict[str, list[StageMetric]] = {}
        for t in analysis.transitions:
            outgoing_transitions.setdefault(t.source_activity, []).append(t)
            incoming_transitions.setdefault(t.target_activity, []).append(t)

        # Retrieve true activity event occurrence frequencies
        activity_freq = dict(analysis.activity_frequencies)
        if not activity_freq:
            # Fallback for backwards compatibility if activity_frequencies was not populated
            for t in analysis.transitions:
                activity_freq[t.source_activity] = (
                    activity_freq.get(t.source_activity, 0) + t.transition_count
                )

        raw_candidates: list[dict[str, Any]] = []
        for activity, freq in activity_freq.items():
            if freq < request.min_frequency:
                continue

            # Prefer outgoing transitions for stage duration;
            # fall back to incoming transitions if the activity is terminal.
            trans_list = outgoing_transitions.get(activity, [])
            if not trans_list:
                trans_list = incoming_transitions.get(activity, [])

            total_weight = sum(t.transition_count for t in trans_list)
            if total_weight > 0:
                # Weighted average of mean durations across transitions
                avg_minutes = (
                    sum(
                        t.transition_count * t.mean_duration_hours * 60.0
                        for t in trans_list
                    )
                    / total_weight
                )
                # Compute combined standard deviation across transitions using the
                # Law of Total Variance (Eve's Law), capturing both within-transition
                # variance (sigma_i^2) and between-transition mean differences ((mu_i - mu_bar)^2):
                #   Var = sum(n_i * (sigma_i^2 + (mu_i - mu_bar)^2)) / sum(n_i)
                variance_sum = sum(
                    t.transition_count
                    * (
                        ((t.std_dev_hours * 60.0) ** 2)
                        + (((t.mean_duration_hours * 60.0) - avg_minutes) ** 2)
                    )
                    for t in trans_list
                )
                std_minutes = (variance_sum / total_weight) ** 0.5
            else:
                avg_minutes = 0.0
                std_minutes = 0.0

            variance_factor = 1.0 / (1.0 + std_minutes)
            raw_automation = freq * avg_minutes * variance_factor
            raw_effort = freq * avg_minutes

            raw_candidates.append(
                {
                    "activity_name": activity,
                    "execution_frequency": freq,
                    "avg_duration_minutes": avg_minutes,
                    "standard_deviation_minutes": std_minutes,
                    "raw_automation": raw_automation,
                    "raw_effort": raw_effort,
                }
            )

        # Normalize relative to max candidate scores to prevent saturation
        max_auto = max((c["raw_automation"] for c in raw_candidates), default=1.0)
        max_effort = max((c["raw_effort"] for c in raw_candidates), default=1.0)
        if max_auto <= 0:
            max_auto = 1.0
        if max_effort <= 0:
            max_effort = 1.0

        candidates: list[AutomationCandidate] = []
        for raw_c in raw_candidates:
            auto_score = (raw_c["raw_automation"] / max_auto) * 100.0
            effort_score = (raw_c["raw_effort"] / max_effort) * 100.0

            candidates.append(
                AutomationCandidate(
                    activity_name=raw_c["activity_name"],
                    execution_frequency=raw_c["execution_frequency"],
                    avg_duration_minutes=round(raw_c["avg_duration_minutes"], 2),
                    standard_deviation_minutes=round(
                        raw_c["standard_deviation_minutes"], 2
                    ),
                    manual_effort_score=round(min(100.0, max(0.0, effort_score)), 2),
                    automation_potential_score=round(
                        min(100.0, max(0.0, auto_score)), 2
                    ),
                    rank=0,
                )
            )

        # Deterministic ranking: automation score desc, manual effort desc, frequency desc, activity name asc
        candidates.sort(
            key=lambda item: (
                -item.automation_potential_score,
                -item.manual_effort_score,
                -item.execution_frequency,
                item.activity_name,
            )
        )
        for idx, ranked_cand in enumerate(candidates, start=1):
            ranked_cand.rank = idx

        return AutomationScoreReport(
            candidates=candidates,
            methodology=(
                "Relative heuristic scoring: automation_potential = f(frequency, elapsed_stage_duration, 1/(1+std_dev)) "
                "normalized across candidates. Manual effort index = f(frequency, elapsed_stage_duration) normalized. "
                "Note: Elapsed duration reflects inter-event transition time rather than verified human touch time."
            ),
            warnings=analysis.warnings,
        )
