"""MCP Prompt: structured review of discovered process bottlenecks."""

from __future__ import annotations

from workflow_intelligence.schemas.process import (
    AutomationScoreReport,
    BottleneckReport,
)


def process_bottleneck_review(
    bottleneck_report: BottleneckReport,
    automation_report: AutomationScoreReport,
) -> str:
    """Render a structured process bottleneck review prompt.

    Guides the LLM through a structured review of discovered process
    bottlenecks and automation targets, producing prioritized findings.

    Args:
        bottleneck_report: Bottleneck stages with abnormal cycle times.
        automation_report: Ranked automation candidates.

    Returns:
        A formatted instruction prompt for the LLM.
    """
    lines: list[str] = []
    lines.append("# Process Bottleneck Review")
    lines.append("")
    lines.append(
        "You are reviewing the results of a process discovery analysis. "
        "Below are the detected bottlenecks and automation candidates. "
        "Produce a prioritized, evidence-based review with the following "
        "sections: Executive Summary, Bottleneck Details, Automation "
        "Priorities, and Recommended Actions. Do not invent numbers; "
        "cite only the figures provided below."
    )
    lines.append("")
    lines.append("## Bottlenecks")
    if bottleneck_report.bottlenecks:
        for b in bottleneck_report.bottlenecks:
            lines.append(
                f"- {b.stage_transition}: avg {b.avg_duration_hours}h "
                f"(baseline {b.baseline_duration_hours}h), "
                f"z-score {b.z_score:.2f}, affected cases {b.affected_case_count}. "
                f"Recommendation: {b.recommendation}"
            )
    else:
        lines.append("- No bottlenecks detected above the configured threshold.")
    lines.append("")
    lines.append("## Automation Candidates")
    if automation_report.candidates:
        for c in automation_report.candidates:
            lines.append(
                f"- Rank {c.rank}: {c.activity_name} "
                f"(frequency {c.execution_frequency}, avg {c.avg_duration_minutes}min, "
                f"automation potential {c.automation_potential_score:.2f})"
            )
    else:
        lines.append("- No automation candidates meet the minimum frequency threshold.")
    lines.append("")
    lines.append("## Methodology")
    lines.append(automation_report.methodology)
    return "\n".join(lines)
