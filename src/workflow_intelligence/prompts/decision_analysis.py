"""MCP Prompt: standardized management decision brief."""

from __future__ import annotations


def management_decision_brief(
    topic: str,
    metric_name: str,
    baseline_value: float,
    current_value: float,
    delta_percentage: float,
    top_driver: str | None = None,
    attribution_percentage: float | None = None,
    recommendation: str | None = None,
) -> str:
    """Render a standardized management decision brief prompt.

    Guides the LLM to format DuckDB KPI findings into an executive-ready
    brief, linking every figure to verified source data.

    Args:
        topic: Topic of the decision brief.
        metric_name: Name of the KPI metric.
        baseline_value: Baseline period value.
        current_value: Current period value.
        delta_percentage: Percentage change from baseline.
        top_driver: Optional leading dimensional driver of the change.
        attribution_percentage: Optional share of variance explained by the
            top driver.
        recommendation: Optional recommended intervention.

    Returns:
        A formatted briefing prompt for the LLM.
    """
    direction = "increase" if delta_percentage >= 0 else "decrease"
    lines: list[str] = []
    lines.append(f"# Management Decision Brief: {topic}")
    lines.append("")
    lines.append(
        "You are preparing an executive-ready decision brief based on "
        "verified DuckDB KPI findings. Format the output as a concise "
        "brief with the following sections: Executive Summary, "
        "Observed Variance, Root Cause, and Recommended Action. "
        "Do not invent figures; every number must trace to the "
        "verified metrics below."
    )
    lines.append("")
    lines.append("## Executive Summary")
    lines.append(f"- Metric: {metric_name}")
    lines.append(
        f"- Change: {direction} of {abs(delta_percentage):.2f}% "
        f"from {baseline_value} (baseline) to {current_value} (current)."
    )
    lines.append("")
    lines.append("## Observed Variance")
    lines.append(f"- Baseline: {baseline_value}")
    lines.append(f"- Current: {current_value}")
    lines.append(f"- Delta: {delta_percentage:.2f}% ({direction})")
    lines.append("")
    lines.append("## Root Cause")
    if top_driver:
        driver_pct = (
            f" ({attribution_percentage:.2f}% of variance)"
            if attribution_percentage is not None
            else ""
        )
        lines.append(f"- Primary driver: {top_driver}{driver_pct}")
    else:
        lines.append("- No single dimensional driver identified from available data.")
    lines.append("")
    lines.append("## Recommended Action")
    action = (
        recommendation
        if recommendation
        else "Monitor the metric; no immediate action indicated."
    )
    lines.append(f"- {action}")
    return "\n".join(lines)
