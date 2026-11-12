"""MCP Prompt: interactive exception triage diagnostic workflow."""

from __future__ import annotations


def exception_triage_session(
    case_id: str,
    workflow_type: str,
    category: str,
    root_cause: str,
    severity: str,
    matched_entities: list[dict[str, object]],
    recommended_remediation: str,
) -> str:
    """Render an interactive exception triage diagnostic prompt.

    Guides human operators step-by-step through resolving ambiguous
    exceptions, confirming entity matches, and selecting a recovery action.

    Args:
        case_id: Unique case identifier.
        workflow_type: Type of workflow that failed.
        category: Classified exception category.
        root_cause: Human-readable root-cause description.
        severity: Priority level.
        matched_entities: Entities matched against master data.
        recommended_remediation: Suggested remediation action.

    Returns:
        A formatted diagnostic prompt for the operator.
    """
    lines: list[str] = []
    lines.append("# Exception Triage Session")
    lines.append("")
    lines.append(
        "Work through the following diagnostic steps for the failed "
        "workflow case. Confirm each step before selecting a recovery "
        "action. Record your decisions in the audit trail."
    )
    lines.append("")
    lines.append("## Case Summary")
    lines.append(f"- Case ID: {case_id}")
    lines.append(f"- Workflow Type: {workflow_type}")
    lines.append(f"- Category: {category}")
    lines.append(f"- Severity: {severity}")
    lines.append(f"- Root Cause: {root_cause}")
    lines.append("")
    lines.append("## Matched Entities")
    if matched_entities:
        for m in matched_entities:
            lines.append(
                f"- {m.get('entity_id', '?')}: {m.get('registered_name', '?')} "
                f"(confidence {m.get('matching_score', 0)}%)"
            )
    else:
        lines.append("- No entities matched against master data.")
    lines.append("")
    lines.append("## Recommended Remediation")
    lines.append(f"- {recommended_remediation}")
    lines.append("")
    lines.append("## Diagnostic Steps")
    lines.append("1. Verify the case context and failure payload above.")
    lines.append("2. Confirm each matched entity against the system of record.")
    lines.append(
        "3. Decide whether to auto-resolve, apply the recommended "
        "remediation, or escalate."
    )
    lines.append("4. If escalating, specify the target department and reason.")
    lines.append("5. Record the final resolution and audit narrative.")
    return "\n".join(lines)
