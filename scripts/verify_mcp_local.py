"""Local MCP client verification script for the Workflow Intelligence server.

Starts the FastMCP server in-process and exercises every domain tool through
the official MCP ``Client``, mirroring how Claude Desktop / Cursor would
connect. Prints a pass/fail summary and exits non-zero if any check fails.

Usage:
    python scripts/verify_mcp_local.py
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastmcp import Client

from workflow_intelligence.server import create_server

SAMPLE_DATA = Path(__file__).resolve().parents[1] / "sample-data"
EVENTS_CSV = SAMPLE_DATA / "procurement_events.csv"
COSTS_CSV = SAMPLE_DATA / "procurement_costs.csv"

RESULTS: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    status = "PASS" if ok else "FAIL"
    print(f"[{status}] {name}" + (f" - {detail}" if detail else ""))


async def main() -> int:
    mcp = create_server()

    async with Client(mcp) as client:
        # ------------------------------------------------------------------
        # 1. Tool discovery
        # ------------------------------------------------------------------
        tools = await client.list_tools()
        names = {t.name for t in tools}
        expected = {
            "process_analyze_business_process",
            "process_identify_bottlenecks",
            "process_score_automation_opportunities",
            "exception_investigate_workflow",
            "exception_recommend_resolution",
            "exception_escalate_case",
            "decision_analyze_kpi_performance",
            "decision_explain_kpi_deviation",
            "decision_generate_brief",
        }
        record("tool_listing", names == expected, f"{len(names)} tools registered")

        # ------------------------------------------------------------------
        # 2. Tool execution
        # ------------------------------------------------------------------
        async def call(name: str, arguments: dict) -> object:
            result = await client.call_tool(name, arguments)
            return result

        try:
            out = await call(
                "process_analyze_business_process",
                {
                    "source_uri": str(EVENTS_CSV),
                    "case_id_col": "case_id",
                    "activity_col": "activity",
                    "timestamp_col": "timestamp",
                },
            )
            data = json.loads(out.content[0].text)
            record(
                "process_analyze_business_process",
                data.get("success") is True and data.get("total_cases") == 500,
                f"cases={data.get('total_cases')}, events={data.get('total_events')}",
            )
        except Exception as exc:  # noqa: BLE001
            record("process_analyze_business_process", False, str(exc))

        try:
            out = await call(
                "process_identify_bottlenecks",
                {"source_uri": str(EVENTS_CSV), "threshold_std_dev": 2.0},
            )
            data = json.loads(out.content[0].text)
            record(
                "process_identify_bottlenecks",
                data.get("success") is True,
                (
                    f"stages={data.get('total_stages_analyzed')}, "
                    f"bottlenecks={len(data.get('bottlenecks', []))}"
                ),
            )
        except Exception as exc:  # noqa: BLE001
            record("process_identify_bottlenecks", False, str(exc))

        try:
            out = await call(
                "process_score_automation_opportunities",
                {"source_uri": str(EVENTS_CSV), "min_frequency": 10},
            )
            data = json.loads(out.content[0].text)
            record(
                "process_score_automation_opportunities",
                data.get("success") is True and len(data.get("candidates", [])) > 0,
                f"candidates={len(data.get('candidates', []))}",
            )
        except Exception as exc:  # noqa: BLE001
            record("process_score_automation_opportunities", False, str(exc))

        try:
            out = await call(
                "exception_investigate_workflow",
                {
                    "case_id": "PO-9001",
                    "workflow_type": "purchase_order_creation",
                    "failure_payload": {
                        "vendor_name": "ABC Technologies",
                        "tax_id": "TX-123456",
                        "discrepancy": "vendor_name_typo",
                        "master_records": [
                            {
                                "entity_id": "VEND-001",
                                "registered_name": "ABC Technology Pvt Ltd",
                                "tax_id": "TX-123456",
                            }
                        ],
                    },
                },
            )
            data = json.loads(out.content[0].text)
            record(
                "exception_investigate_workflow",
                data.get("success") is True
                and data.get("category") == "MASTER_DATA_DISCREPANCY",
                (
                    f"category={data.get('category')}, "
                    f"auto_resolvable={data.get('auto_resolvable')}"
                ),
            )
        except Exception as exc:  # noqa: BLE001
            record("exception_investigate_workflow", False, str(exc))

        try:
            out = await call(
                "exception_recommend_resolution",
                {
                    "exception_id": "EXC-001",
                    "policy_context": {
                        "auto_approve": True,
                        "approval_tier": "manager",
                    },
                },
            )
            data = json.loads(out.content[0].text)
            record("exception_recommend_resolution", data.get("auto_resolve") is True)
        except Exception as exc:  # noqa: BLE001
            record("exception_recommend_resolution", False, str(exc))

        try:
            out = await call(
                "exception_escalate_case",
                {
                    "case_id": "PO-9002",
                    "reason": "Missing department cost-center code",
                    "target_department": "Finance",
                },
            )
            data = json.loads(out.content[0].text)
            record(
                "exception_escalate_case",
                data.get("ticket_id", "").startswith("TKT-"),
                f"ticket={data.get('ticket_id')}",
            )
        except Exception as exc:  # noqa: BLE001
            record("exception_escalate_case", False, str(exc))

        try:
            out = await call(
                "decision_analyze_kpi_performance",
                {
                    "metric_name": "processing_cost",
                    "source_uri": str(COSTS_CSV),
                    "timeframe_column": "quarter",
                    "timeframe_current": "Q3",
                    "timeframe_baseline": "Q2",
                },
            )
            data = json.loads(out.content[0].text)
            record(
                "decision_analyze_kpi_performance",
                data.get("success") is True and data.get("current_value", 0) > 0,
                (
                    f"baseline={data.get('baseline_value')}, "
                    f"current={data.get('current_value')}, "
                    f"delta={data.get('delta_percentage')}%"
                ),
            )
        except Exception as exc:  # noqa: BLE001
            record("decision_analyze_kpi_performance", False, str(exc))

        try:
            out = await call(
                "decision_explain_kpi_deviation",
                {
                    "metric_name": "processing_cost",
                    "source_uri": str(COSTS_CSV),
                    "timeframe_column": "quarter",
                    "timeframe_current": "Q3",
                    "timeframe_baseline": "Q2",
                    "observed_delta": 925.0,
                    "dimension": "department",
                },
            )
            data = json.loads(out.content[0].text)
            record(
                "decision_explain_kpi_deviation",
                data.get("success") is True and bool(data.get("explanation")),
                (
                    "significant="
                    f"{data.get('statistical_significance', {}).get('significant')}"
                ),
            )
        except Exception as exc:  # noqa: BLE001
            record("decision_explain_kpi_deviation", False, str(exc))

        try:
            out = await call(
                "decision_generate_brief",
                {
                    "topic": "Q3 Procurement Cost Inflation",
                    "metric_ids": ["processing_cost", "order_count"],
                },
            )
            data = json.loads(out.content[0].text)
            record(
                "decision_generate_brief",
                data.get("success") is True
                and len(data.get("findings", [])) == 2
                and len(data.get("provenance_links", [])) > 0,
                (
                    f"findings={len(data.get('findings', []))}, "
                    f"attributions={len(data.get('attributions', []))}"
                ),
            )
        except Exception as exc:  # noqa: BLE001
            record("decision_generate_brief", False, str(exc))

        # ------------------------------------------------------------------
        # 3. Resource reads
        # ------------------------------------------------------------------
        try:
            res = await client.read_resource("workflow://process-maps/procurement")
            item = res[0]
            data = json.loads(item.text)
            record(
                "resource_process_maps_procurement",
                item.mime_type == "application/json"
                and data.get("process_name") == "procurement"
                and isinstance(data.get("nodes"), list)
                and isinstance(data.get("edges"), list),
                f"nodes={len(data.get('nodes', []))}, "
                f"edges={len(data.get('edges', []))}",
            )
        except Exception as exc:  # noqa: BLE001
            record("resource_process_maps_procurement", False, str(exc))

        try:
            res = await client.read_resource("workflow://metrics/kpi-definitions")
            item = res[0]
            record(
                "resource_kpi_definitions",
                item.mime_type == "text/markdown"
                and item.text.startswith("# KPI Definitions"),
                f"mime={item.mime_type}",
            )
        except Exception as exc:  # noqa: BLE001
            record("resource_kpi_definitions", False, str(exc))

        try:
            res = await client.read_resource("workflow://exceptions/active")
            item = res[0]
            data = json.loads(item.text)
            record(
                "resource_exceptions_active",
                item.mime_type == "application/json"
                and data.get("kind") == "active_exceptions"
                and isinstance(data.get("exceptions"), list),
            )
        except Exception as exc:  # noqa: BLE001
            record("resource_exceptions_active", False, str(exc))

        try:
            res = await client.read_resource("workflow://audit/recent")
            item = res[0]
            data = json.loads(item.text)
            record(
                "resource_audit_recent",
                item.mime_type == "application/json"
                and data.get("kind") == "recent_audit_log"
                and isinstance(data.get("entries"), list),
            )
        except Exception as exc:  # noqa: BLE001
            record("resource_audit_recent", False, str(exc))

        # ------------------------------------------------------------------
        # 4. Prompt rendering
        # ------------------------------------------------------------------
        try:
            result = await client.get_prompt(
                "process_bottleneck_review",
                arguments={
                    "bottleneck_report": {
                        "source_uri": "sample-data/procurement_events.csv",
                        "total_stages_analyzed": 4,
                        "bottlenecks": [],
                    },
                    "automation_report": {
                        "candidates": [],
                        "methodology": "score = f(frequency, duration, 1/variance)",
                    },
                },
            )
            messages = result.messages
            text = " ".join(str(m.content) for m in messages)
            record(
                "prompt_process_bottleneck_review",
                isinstance(messages, list)
                and len(messages) >= 1
                and "Process Bottleneck Review" in text,
            )
        except Exception as exc:  # noqa: BLE001
            record("prompt_process_bottleneck_review", False, str(exc))

        try:
            result = await client.get_prompt(
                "management_decision_brief",
                arguments={
                    "topic": "Q3 Cost Inflation",
                    "metric_name": "processing_cost",
                    "baseline_value": 100.0,
                    "current_value": 150.0,
                    "delta_percentage": 50.0,
                },
            )
            messages = result.messages
            text = " ".join(str(m.content) for m in messages)
            record(
                "prompt_management_decision_brief",
                isinstance(messages, list)
                and len(messages) >= 1
                and "Management Decision Brief: Q3 Cost Inflation" in text,
            )
        except Exception as exc:  # noqa: BLE001
            record("prompt_management_decision_brief", False, str(exc))

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    failed = sum(1 for _, ok, _ in RESULTS if not ok)
    print("-" * 60)
    print(
        f"Verification complete: {passed} passed, {failed} failed out of {len(RESULTS)}"
    )
    if failed:
        print("FAILURES:")
        for name, ok, detail in RESULTS:
            if not ok:
                print(f"  - {name}: {detail}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
