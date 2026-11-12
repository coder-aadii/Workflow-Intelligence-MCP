"""End-to-end procurement intelligence demo.

Chains all three tool suites through the unified procurement scenario:

1. DISCOVER  - Process Discovery Engine (process_*)
2. RESOLVE   - Workflow Exception Resolver (exception_*)
3. DECIDE    - Business Decision Intelligence (decision_*)

Run with:

    python demo/procurement/run_demo.py

The demo executes entirely offline against the local ``sample-data/`` fixtures
and completes in well under 10 seconds.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from workflow_intelligence.schemas.decision import (
    DecisionBriefRequest,
    KPICalculationRequest,
    KPIExplanationRequest,
)
from workflow_intelligence.schemas.exception import (
    ResolutionRequest,
    WorkflowInvestigationRequest,
)
from workflow_intelligence.schemas.process import (
    AutomationScoreRequest,
    BottleneckRequest,
    ProcessAnalyzeRequest,
)
from workflow_intelligence.services.decision_service import DecisionService
from workflow_intelligence.services.exception_service import ExceptionService
from workflow_intelligence.services.process_service import ProcessService

ROOT = Path(__file__).resolve().parents[2]
SAMPLE_DATA = ROOT / "sample-data"
EVENTS_CSV = SAMPLE_DATA / "procurement_events.csv"
FAILED_WORKFLOWS_JSON = SAMPLE_DATA / "failed_workflows.json"
COSTS_CSV = SAMPLE_DATA / "procurement_costs.csv"


def _header(text: str) -> None:
    print()
    print("=" * 72)
    print(text)
    print("=" * 72)


def _section(text: str) -> None:
    print()
    print(f"-- {text}")


def _kv(label: str, value: str) -> None:
    print(f"  {label:<28} {value}")


def step1_discover(process_service: ProcessService) -> None:
    """Step 1: Process Discovery Engine (process_*)."""
    _header("STEP 1 - DISCOVER: Process Discovery Engine (process_*)")

    _section("Ingest sample-data/procurement_events.csv")
    analysis = process_service.analyze_business_process(
        ProcessAnalyzeRequest(
            source_uri=str(EVENTS_CSV),
            case_id_col="case_id",
            activity_col="activity",
            timestamp_col="timestamp",
        )
    )
    _kv("Event log", str(EVENTS_CSV))
    _kv("Total cases", str(analysis.total_cases))
    _kv("Total events", str(analysis.total_events))
    _kv("Unique activities", str(len(analysis.unique_activities)))
    _kv("Execution variants", str(analysis.variant_count))
    _kv("Avg case duration (h)", f"{analysis.average_case_duration_hours:.2f}")

    _section("Bottleneck detection (process_identify_bottlenecks)")
    report = process_service.identify_bottlenecks(
        BottleneckRequest(source_uri=str(EVENTS_CSV), threshold_std_dev=2.0)
    )
    _kv("Stages analyzed", str(report.total_stages_analyzed))
    _kv("Bottlenecks detected", str(len(report.bottlenecks)))
    if report.bottlenecks:
        top = report.bottlenecks[0]
        _kv("Top bottleneck", top.stage_transition)
        _kv("Avg duration (h)", f"{top.avg_duration_hours:.1f}")
        _kv("Baseline duration (h)", f"{top.baseline_duration_hours:.1f}")
        _kv("Z-score", f"{top.z_score:.2f}")
        _kv("Affected cases", str(top.affected_case_count))
        _kv("Recommendation", top.recommendation)
    else:
        print("  (none detected)")

    _section("Automation scoring (process_score_automation_opportunities)")
    score = process_service.score_automation_opportunities(
        AutomationScoreRequest(source_uri=str(EVENTS_CSV), min_frequency=10)
    )
    _kv("Methodology", score.methodology)
    _kv("Candidates", str(len(score.candidates)))
    if score.candidates:
        top_cand = score.candidates[0]
        _kv("#1 automation target", top_cand.activity_name)
        _kv("Execution frequency", str(top_cand.execution_frequency))
        _kv("Avg duration (min)", f"{top_cand.avg_duration_minutes:.1f}")
        _kv("Automation potential", f"{top_cand.automation_potential_score:.2f}")


def step2_resolve(exception_service: ExceptionService) -> None:
    """Step 2: Workflow Exception Resolver (exception_*)."""
    _header("STEP 2 - RESOLVE: Workflow Exception Resolver (exception_*)")

    _section("Triage failed PO in sample-data/failed_workflows.json")
    failed = json.loads(FAILED_WORKFLOWS_JSON.read_text(encoding="utf-8"))

    for entry in failed:
        case_id = entry["case_id"]
        print(f"  [{case_id}] {entry['failure_category']}")
        print(f"           {entry['description']}")

        payload = dict(entry)
        payload.pop("failure_category", None)
        payload.pop("description", None)

        result = exception_service.investigate_failure(
            WorkflowInvestigationRequest(
                case_id=case_id,
                workflow_type=entry["workflow_type"],
                failure_payload=payload,
            )
        )
        print(f"           classified -> {result.category.value}")
        print(f"           auto_resolvable -> {result.auto_resolvable}")
        print(f"           remediation -> {result.recommended_remediation}")
        print(f"           audit -> {result.audit_narrative}")
        matched = result.matched_entities
        if matched:
            m = matched[0]
            print(
                f"           matched entity -> {m.entity_id} "
                f"({m.registered_name}, score {m.matching_score})"
            )

    _section("Resolution recommendation (exception_recommend_resolution)")
    plan = exception_service.recommend_resolution(
        ResolutionRequest(
            exception_id="EXC-0001",
            policy_context={"auto_approve": True, "approval_tier": "manager"},
        )
    )
    _kv("Auto resolve", str(plan.auto_resolve))
    _kv("Required approval tier", str(plan.required_approval_tier))
    _kv("Remediation action", plan.remediation_action)

    _section("Escalation (exception_escalate_case)")
    esc = exception_service.escalate_case(
        case_id="PO-9002",
        reason="Missing department cost-center code",
        target_department="Finance",
    )
    _kv("Ticket ID", esc.ticket_id)
    _kv("Routed department", esc.routed_department)
    _kv("Priority", esc.priority.value)
    _kv("Audit narrative", esc.audit_narrative)


def step3_decide(decision_service: DecisionService) -> None:
    """Step 3: Business Decision Intelligence (decision_*)."""
    _header("STEP 3 - DECIDE: Business Decision Intelligence (decision_*)")

    _section("Query sample-data/procurement_costs.csv via DuckDB")
    perf = decision_service.analyze_kpi_performance(
        KPICalculationRequest(
            metric_name="processing_cost",
            source_uri=str(COSTS_CSV),
            timeframe_column="quarter",
            timeframe_current="Q3",
            timeframe_baseline="Q2",
        )
    )
    _kv("Metric", perf.metric_name)
    _kv("Baseline (Q2)", f"{perf.baseline_value:.2f}")
    _kv("Current (Q3)", f"{perf.current_value:.2f}")
    _kv("Delta (%)", f"{perf.delta_percentage:.2f}%")

    _section("Attribute Q3 cost increase to rush shipping fees")
    explanation = decision_service.explain_kpi_deviation(
        KPIExplanationRequest(
            metric_name="processing_cost",
            source_uri=str(COSTS_CSV),
            timeframe_column="quarter",
            timeframe_current="Q3",
            timeframe_baseline="Q2",
            observed_delta=perf.current_value - perf.baseline_value,
            dimension="department",
        )
    )
    _kv("Observed delta", f"{explanation.observed_delta:.2f}")
    _kv("Dimension", explanation.dimension or "department")
    _kv("Data sufficient", str(not explanation.data_insufficient))
    is_sig = explanation.statistical_significance.get("significant", 0.0) == 1.0
    _kv("Significant", str(is_sig))
    _kv("Explanation", explanation.explanation)
    if explanation.top_contributors:
        top = explanation.top_contributors[0]
        _kv("Top driver", top.segment_name)
        _kv("Contribution (%)", f"{top.contribution_percentage:.2f}")
        _kv("Records", str(top.record_count))

    _section("Draft executive decision brief (decision_generate_brief)")
    brief = decision_service.generate_brief(
        DecisionBriefRequest(
            topic="Q3 Procurement Cost Inflation",
            metric_ids=["processing_cost", "order_count"],
        )
    )
    _kv("Topic", brief.topic)
    _kv("Findings", str(len(brief.findings)))
    _kv("Attributions", str(len(brief.attributions)))
    _kv("Recommendations", str(len(brief.recommendations)))
    print("  Provenance links:")
    for link in brief.provenance_links:
        print(f"    - {link}")


def main() -> int:
    start = time.time()
    process_service = ProcessService()
    exception_service = ExceptionService()
    decision_service = DecisionService()

    step1_discover(process_service)
    step2_resolve(exception_service)
    step3_decide(decision_service)

    elapsed = time.time() - start
    print()
    print("=" * 72)
    print("UNIFIED PROCUREMENT SCENARIO - COMPLETE")
    print(f"Elapsed: {elapsed:.2f}s")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(main())
