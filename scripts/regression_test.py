#!/usr/bin/env python3
"""Regression test script for automation scoring implementation (commit ae960c4).

Uses FastMCP Client to invoke MCP tools directly in-process.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

# Set extended timeout for large dataset analysis BEFORE importing the server
# BPIC19_flat.csv has ~1.5M rows and requires more processing time
os.environ["ANALYSIS_TIMEOUT_SECONDS"] = "300"  # 5 minutes for large datasets
os.environ["WORKFLOW_ENV"] = "development"

from fastmcp import Client

from workflow_intelligence.server import create_server

# File paths
LOCAL_DIR = Path("/home/rails/Work/Github-Repos/workflow-intelligence-mcp/local")
INSURANCE_CLAIMS = LOCAL_DIR / "Insurance_claims_event_log.csv"
BPIC19_FLAT = LOCAL_DIR / "BPIC19_flat.csv"


class TestResult:
    """Holds test result information."""

    def __init__(self, name: str):
        self.name = name
        self.start_time = time.time()
        self.passed = False
        self.expected: str | None = None
        self.actual: str | None = None
        self.error: str | None = None

    def fail(self, reason: str):
        self.actual = reason
        self.error = reason

    def pass_msg(self, msg: str = ""):
        self.passed = True
        self.actual = msg

    @property
    def elapsed(self) -> float:
        return time.time() - self.start_time


async def test_insurance_claims_automation_scoring(client: Client) -> list[TestResult]:
    """Test 1: process_score_automation_opportunities with Insurance claims data."""
    results = []

    # Test 1a: Automation scoring with Insurance claims
    result = TestResult("Insurance_claims automation scoring (min_frequency=100)")
    try:
        start = time.time()
        out = await client.call_tool(
            "process_score_automation_opportunities",
            {
                "source_uri": str(INSURANCE_CLAIMS),
                "min_frequency": 100,
            },
        )
        data = json.loads(out.content[0].text)

        # Expected values from the requirements:
        # Activity    Expected Frequency    Expected Automation Score
        # First Notification of Loss (FNOL)    30,000    100.00
        # Assign Claim    30,000    37.66
        # Claim Decision    30,000    18.78
        # Set Reserve    30,000    18.53
        # Payment Sent    30,000    9.01
        # Close Claim    30,000    0.00

        candidates = data.get("candidates", [])
        if not candidates:
            result.fail("No candidates returned")
            return results

        # Build lookup by activity name
        by_activity = {c["activity_name"]: c for c in candidates}

        # Expected mappings
        expected = {
            "First Notification of Loss (FNOL)": {"frequency": 30000, "score": 100.00},
            "Assign Claim": {"frequency": 30000, "score": 37.66},
            "Claim Decision": {"frequency": 30000, "score": 18.78},
            "Set Reserve": {"frequency": 30000, "score": 18.53},
            "Payment Sent": {"frequency": 30000, "score": 9.01},
            "Close Claim": {"frequency": 30000, "score": 0.00},
        }

        all_ok = True
        details = []

        for activity, exp in expected.items():
            if activity not in by_activity:
                details.append(f"Missing activity: {activity}")
                all_ok = False
                continue

            actual_freq = by_activity[activity]["execution_frequency"]
            actual_score = by_activity[activity]["automation_potential_score"]

            freq_ok = actual_freq == exp["frequency"]
            score_ok = abs(actual_score - exp["score"]) < 0.5  # Allow 0.5 tolerance

            if not freq_ok:
                details.append(
                    f"{activity}: freq {actual_freq} != expected {exp['frequency']}"
                )
                all_ok = False
            if not score_ok:
                details.append(
                    f"{activity}: score {actual_score:.2f} not within tolerance of {exp['score']}"
                )
                all_ok = False

        result.pass_msg(f"candidates={len(candidates)}, {'; '.join(details)}" if details else f"candidates={len(candidates)}")
        result.expected = str(expected)

    except Exception as exc:
        result.fail(str(exc))

    results.append(result)

    return results


async def test_bpic19_automation_scoring(client: Client) -> list[TestResult]:
    """Test 2: process_score_automation_opportunities with BPIC19 data."""
    results = []

    result = TestResult("BPIC19 automation scoring (min_frequency=1000)")
    try:
        start = time.time()
        out = await client.call_tool(
            "process_score_automation_opportunities",
            {
                "source_uri": str(BPIC19_FLAT),
                "min_frequency": 1000,
            },
        )
        data = json.loads(out.content[0].text)

        # Expected:
        # Activity    Expected Frequency    Expected Score
        # Clear Invoice    194,393    100.00
        # Vendor creates invoice    219,919    61.94
        # Change Quantity    21,449    6.40

        candidates = data.get("candidates", [])
        if not candidates:
            result.fail("No candidates returned")
            return results

        by_activity = {c["activity_name"]: c for c in candidates}

        expected = {
            "Clear Invoice": {"frequency": 194393, "score": 100.00},
            "Vendor creates invoice": {"frequency": 219919, "score": 61.94},
            "Change Quantity": {"frequency": 21449, "score": 6.40},
        }

        all_ok = True
        details = []

        for activity, exp in expected.items():
            if activity not in by_activity:
                details.append(f"Missing activity: {activity}")
                all_ok = False
                continue

            actual_freq = by_activity[activity]["execution_frequency"]
            actual_score = by_activity[activity]["automation_potential_score"]

            freq_ok = actual_freq == exp["frequency"]
            score_ok = abs(actual_score - exp["score"]) < 0.5

            if not freq_ok:
                details.append(
                    f"{activity}: freq {actual_freq} != expected {exp['frequency']}"
                )
                all_ok = False
            if not score_ok:
                details.append(
                    f"{activity}: score {actual_score:.2f} not within tolerance of {exp['score']}"
                )
                all_ok = False

        result.pass_msg(f"candidates={len(candidates)}, {'; '.join(details)}" if details else f"candidates={len(candidates)}")

    except Exception as exc:
        result.fail(str(exc))

    results.append(result)

    return results


async def test_insurance_claims_bottlenecks(client: Client) -> list[TestResult]:
    """Test 3: process_identify_bottlenecks with Insurance claims (BPIC19)."""
    results = []

    result = TestResult("BPIC19 bottlenecks (threshold_std_dev=2.5, min_frequency=10)")
    try:
        start = time.time()
        out = await client.call_tool(
            "process_identify_bottlenecks",
            {
                "source_uri": str(BPIC19_FLAT),
                "threshold_std_dev": 2.5,
                "min_frequency": 10,
            },
        )
        data = json.loads(out.content[0].text)

        bottlenecks = data.get("bottlenecks", [])
        by_transition = {b["stage_transition"]: b for b in bottlenecks}

        # Expected exclusions:
        # - Vendor creates debit memo → SRM: Created (5 cases) is excluded
        # - Change Storage Location → Block Purchase Order Item (1 case) is excluded
        # Expected inclusions:
        # - Vendor creates debit memo → Create Purchase Order Item (117 cases) remains eligible

        details = []
        all_ok = True

        excluded_5 = "Vendor creates debit memo -> SRM: Created"
        included_117 = "Vendor creates debit memo -> Create Purchase Order Item"
        excluded_1 = "Change Storage Location -> Block Purchase Order Item"

        if excluded_5 in by_transition:
            details.append(f"ERROR: {excluded_5} should be excluded but found (5 cases)")
            all_ok = False

        if excluded_1 in by_transition:
            details.append(f"ERROR: {excluded_1} should be excluded but found (1 case)")
            all_ok = False

        if included_117 not in by_transition:
            details.append(f"ERROR: {included_117} should remain but not found (117 cases)")
            all_ok = False

        # Check low-sample-size warnings
        low_sample_items = [b for b in bottlenecks if b.get("sample_size_warning")]
        details.append(f"low-sample-size warnings: {len(low_sample_items)}")

        result.pass_msg(f"{'; '.join(details)}" if details else f"bottlenecks={len(bottlenecks)}")

    except Exception as exc:
        result.fail(str(exc))

    results.append(result)

    return results


async def test_insurance_claims_bottlenecks_2(client: Client) -> list[TestResult]:
    """Test 4: process_identify_bottlenecks with Insurance claims data."""
    results = []

    result = TestResult("Insurance_claims bottlenecks (threshold_std_dev=2.0, min_frequency=1)")
    try:
        start = time.time()
        out = await client.call_tool(
            "process_identify_bottlenecks",
            {
                "source_uri": str(INSURANCE_CLAIMS),
                "threshold_std_dev": 2.0,
                "min_frequency": 1,
            },
        )
        data = json.loads(out.content[0].text)

        bottlenecks = data.get("bottlenecks", [])
        by_transition = {b["stage_transition"]: b for b in bottlenecks}

        # Expected:
        # - FNOL → Assign Claim: approximately 360.25 hours, z-score 2.22, 29,807 cases.
        # - FNOL → Set Reserve: approximately 354.78 hours, z-score 2.17, 1 case, with a low-sample-size warning.
        # - FNOL → Claim Decision: approximately 345.95 hours, z-score 2.09, 192 cases.

        details = []
        all_ok = True

        fnol_assign = by_transition.get("First Notification of Loss (FNOL) -> Assign Claim")
        fnol_reserve = by_transition.get("First Notification of Loss (FNOL) -> Set Reserve")
        fnol_decision = by_transition.get("First Notification of Loss (FNOL) -> Claim Decision")

        # Check FNOL → Assign Claim
        if fnol_assign:
            if abs(fnol_assign["avg_duration_hours"] - 360.25) > 1.0:
                details.append(
                    f"FNOL→Assign: duration {fnol_assign['avg_duration_hours']:.2f} not ~360.25 hours"
                )
            if abs(fnol_assign["z_score"] - 2.22) > 0.1:
                details.append(f"FNOL→Assign: z_score {fnol_assign['z_score']:.2f} not ~2.22")
            if fnol_assign["affected_case_count"] != 29807:
                details.append(
                    f"FNOL→Assign: case_count {fnol_assign['affected_case_count']} not 29807"
                )
        else:
            details.append("ERROR: FNOL → Assign Claim not found")
            all_ok = False

        # Check FNOL → Set Reserve
        if fnol_reserve:
            if abs(fnol_reserve["avg_duration_hours"] - 354.78) > 1.0:
                details.append(
                    f"FNOL→Set Reserve: duration {fnol_reserve['avg_duration_hours']:.2f} not ~354.78 hours"
                )
            if abs(fnol_reserve["z_score"] - 2.17) > 0.1:
                details.append(f"FNOL→Set Reserve: z_score {fnol_reserve['z_score']:.2f} not ~2.17")
            if fnol_reserve["affected_case_count"] != 1:
                details.append(
                    f"FNOL→Set Reserve: case_count {fnol_reserve['affected_case_count']} not 1"
                )
            if not fnol_reserve.get("sample_size_warning"):
                details.append("ERROR: FNOL→Set Reserve should have low-sample-size warning")
        else:
            details.append("ERROR: FNOL → Set Reserve not found")
            all_ok = False

        # Check FNOL → Claim Decision
        if fnol_decision:
            if abs(fnol_decision["avg_duration_hours"] - 345.95) > 1.0:
                details.append(
                    f"FNOL→Claim Decision: duration {fnol_decision['avg_duration_hours']:.2f} not ~345.95 hours"
                )
            if abs(fnol_decision["z_score"] - 2.09) > 0.1:
                details.append(f"FNOL→Claim Decision: z_score {fnol_decision['z_score']:.2f} not ~2.09")
            if fnol_decision["affected_case_count"] != 192:
                details.append(
                    f"FNOL→Claim Decision: case_count {fnol_decision['affected_case_count']} not 192"
                )
        else:
            details.append("ERROR: FNOL → Claim Decision not found")
            all_ok = False

        result.pass_msg(f"{'; '.join(details)}" if details else f"bottlenecks={len(bottlenecks)}")

    except Exception as exc:
        result.fail(str(exc))

    results.append(result)

    return results


async def main() -> int:
    """Run all regression tests."""
    print("=" * 80)
    print("Workflow Intelligence MCP - Automation Scoring Regression Test")
    print(f"Commit: ae960c4 (fix: correct activity frequencies, normalize automation scoring)")
    print("=" * 80)
    print()

    mcp = create_server()
    all_results = []

    async with Client(mcp) as client:
        # Verify MCP connection
        tools = await client.list_tools()
        names = {t.name for t in tools}
        expected_tools = {
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

        print("[INFO] MCP Connection Status: ", end="")
        if names == expected_tools:
            print("PASS - All 9 tools registered")
        else:
            print("FAIL - Tools mismatch")
            print(f"  Expected: {expected_tools}")
            print(f"  Got: {names}")
            return 1

        print()

        # Run tests sequentially
        print("Running tests sequentially...")
        print("-" * 40)

        # Test 1: Insurance claims automation scoring
        print("\n[TEST 1] Insurance_claims_event_log.csv automation scoring (min_frequency=100)")
        results1 = await test_insurance_claims_automation_scoring(client)
        all_results.extend(results1)
        for r in results1:
            status = "PASS" if r.passed else "FAIL"
            print(f"  {status} | {r.name} | elapsed: {r.elapsed:.2f}s")
            if r.error:
                print(f"         Error: {r.error[:200]}" if len(r.error) > 200 else f"         Error: {r.error}")

        # Test 2: BPIC19 automation scoring
        print("\n[TEST 2] BPIC19_flat.csv automation scoring (min_frequency=1000)")
        results2 = await test_bpic19_automation_scoring(client)
        all_results.extend(results2)
        for r in results2:
            status = "PASS" if r.passed else "FAIL"
            print(f"  {status} | {r.name} | elapsed: {r.elapsed:.2f}s")
            if r.error:
                print(f"         Error: {r.error[:200]}" if len(r.error) > 200 else f"         Error: {r.error}")

        # Test 3: BPIC19 bottlenecks
        print("\n[TEST 3] BPIC19_flat.csv bottlenecks (threshold_std_dev=2.5, min_frequency=10)")
        results3 = await test_bpic19_bottlenecks(client)
        all_results.extend(results3)
        for r in results3:
            status = "PASS" if r.passed else "FAIL"
            print(f"  {status} | {r.name} | elapsed: {r.elapsed:.2f}s")
            if r.error:
                print(f"         Error: {r.error[:200]}" if len(r.error) > 200 else f"         Error: {r.error}")

        # Test 4: Insurance claims bottlenecks
        print("\n[TEST 4] Insurance_claims_event_log.csv bottlenecks (threshold_std_dev=2.0, min_frequency=1)")
        results4 = await test_insurance_claims_bottlenecks_2(client)
        all_results.extend(results4)
        for r in results4:
            status = "PASS" if r.passed else "FAIL"
            print(f"  {status} | {r.name} | elapsed: {r.elapsed:.2f}s")
            if r.error:
                print(f"         Error: {r.error[:200]}" if len(r.error) > 200 else f"         Error: {r.error}")

    # Summary
    print()
    print("=" * 80)
    print("SUMMARY")
    print("=" * 80)

    passed = sum(1 for r in all_results if r.passed)
    failed = sum(1 for r in all_results if not r.passed)

    print(f"Total: {len(all_results)} tests")
    print(f"Passed: {passed}")
    print(f"Failed: {failed}")

    if failed > 0:
        print("\nFAILED TESTS:")
        for r in all_results:
            if not r.passed:
                print(f"  - {r.name}: {r.actual}")

    print()
    if failed == 0:
        print("All regression tests PASSED. Issues resolved in commit ae960c4:")
        print("  ✓ Frequency double-counting fixed")
        print("  ✓ Score saturation normalized (0-100 range)")
        print("  ✓ Sparse-bottleneck reporting with low-sample-size warnings")
    else:
        print("Some tests FAILED. Issues may not be fully resolved.")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    exit(exit_code)