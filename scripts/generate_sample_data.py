"""Deterministic synthetic procurement dataset generator.

Creates the three datasets referenced by Phase 5 (Protocol Server
Integration) and saved under ``sample-data/``:

- ``procurement_events.csv``: >= 2,500 events across 500 purchase
  requisitions with an embedded 74-hour delay between "Finance Approval"
  and "Vendor Selection" plus repeated approval loops on orders over
  $10,000.
- ``failed_workflows.json``: failure scenarios covering vendor entity
  mismatch, missing department cost-center code, and manager sign-off
  approval timeout.
- ``procurement_costs.csv``: 4 quarters (Q1-Q4) of transactional costs
  with an embedded Q3 processing cost spike driven by rush-delivery fees.

The generator is fully deterministic (seeded PRNG) and idempotent: re-running
it overwrites the files with byte-identical output.
"""

from __future__ import annotations

import csv
import json
import random
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DATA = ROOT / "sample-data"

ACTIVITIES = [
    "Purchase Request",
    "Manager Approval",
    "Finance Approval",
    "Vendor Selection",
    "Purchase Order",
]

DEPARTMENTS = ["Engineering", "Sales", "Marketing", "Finance", "HR", "Procurement"]
RESOURCES = ["alice", "bob", "carol", "dave", "eve", "frank", "grace", "heidi"]
CURRENCIES = ["USD", "EUR", "GBP"]

START_DATE = datetime(2026, 1, 1, 9, 0, 0, tzinfo=UTC)

# High-value threshold that triggers repeated approval loops.
HIGH_VALUE_THRESHOLD = 10_000

# Number of distinct purchase requisitions (cases).
NUM_CASES = 500


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def generate_event_log() -> list[dict]:
    """Generate the procurement event log.

    Each case follows the base trace
    ``Purchase Request -> Manager Approval -> Finance Approval -> Vendor
    Selection -> Purchase Order``. Between "Finance Approval" and "Vendor
    Selection" a 74-hour delay is embedded. Orders over $10,000 receive
    repeated "Manager Approval" loops.
    """
    rng = random.Random(20260112)
    rows: list[dict] = []

    for i in range(1, NUM_CASES + 1):
        case_id = f"PO-{i:04d}"
        department = rng.choice(DEPARTMENTS)
        estimated_cost = round(rng.uniform(1_000, 25_000), 2)
        currency = rng.choice(CURRENCIES)

        # Base start time spreads cases across January 2026.
        case_start = START_DATE + timedelta(hours=rng.randint(0, 720))

        t_request = case_start
        t_manager = t_request + timedelta(hours=rng.randint(1, 4))
        t_finance = t_manager + timedelta(hours=rng.randint(1, 4))

        # Embedded inefficiency: a 74-hour delay between Finance Approval and
        # Vendor Selection on a subset of cases (creates bottleneck contrast
        # against normally-flowing cases).
        delayed = i % 10 == 0  # every 10th case is delayed (~50 cases)
        vendor_gap_hours = 74 if delayed else rng.randint(1, 4)
        t_vendor = t_finance + timedelta(hours=vendor_gap_hours)
        t_po = t_vendor + timedelta(hours=rng.randint(1, 3))

        rows.append(
            {
                "case_id": case_id,
                "activity": "Purchase Request",
                "timestamp": _iso(t_request),
                "resource": rng.choice(RESOURCES),
                "department": department,
                "estimated_cost": estimated_cost,
                "currency": currency,
            }
        )
        rows.append(
            {
                "case_id": case_id,
                "activity": "Manager Approval",
                "timestamp": _iso(t_manager),
                "resource": rng.choice(RESOURCES),
                "department": department,
                "estimated_cost": estimated_cost,
                "currency": currency,
            }
        )

        # Repeated approval loops for high-value orders (> $10,000).
        loop_count = 2 if estimated_cost > HIGH_VALUE_THRESHOLD else 0
        for _ in range(loop_count):
            t_manager = t_manager + timedelta(hours=rng.randint(4, 12))
            rows.append(
                {
                    "case_id": case_id,
                    "activity": "Manager Approval",
                    "timestamp": _iso(t_manager),
                    "resource": rng.choice(RESOURCES),
                    "department": department,
                    "estimated_cost": estimated_cost,
                    "currency": currency,
                }
            )

        rows.append(
            {
                "case_id": case_id,
                "activity": "Finance Approval",
                "timestamp": _iso(t_finance),
                "resource": rng.choice(RESOURCES),
                "department": department,
                "estimated_cost": estimated_cost,
                "currency": currency,
            }
        )
        rows.append(
            {
                "case_id": case_id,
                "activity": "Vendor Selection",
                "timestamp": _iso(t_vendor),
                "resource": rng.choice(RESOURCES),
                "department": department,
                "estimated_cost": estimated_cost,
                "currency": currency,
            }
        )
        rows.append(
            {
                "case_id": case_id,
                "activity": "Purchase Order",
                "timestamp": _iso(t_po),
                "resource": rng.choice(RESOURCES),
                "department": department,
                "estimated_cost": estimated_cost,
                "currency": currency,
            }
        )

    return rows


def generate_failed_workflows() -> list[dict]:
    """Generate the failed workflows fixture."""
    return [
        {
            "case_id": "PO-9001",
            "workflow_type": "purchase_order_creation",
            "failure_category": "MASTER_DATA_DISCREPANCY",
            "submitted_vendor": "ABC Technologies",
            "registered_vendor": "ABC Technology Pvt Ltd",
            "submitted_tax_id": "TX-123456",
            "registered_tax_id": "TX-123456",
            "discrepancy": "vendor_name_typo",
            "description": (
                "Submitted as 'ABC Technologies', registered in master DB as "
                "'ABC Technology Pvt Ltd'. Tax ID matches exactly."
            ),
        },
        {
            "case_id": "PO-9002",
            "workflow_type": "purchase_order_creation",
            "failure_category": "GOVERNANCE_INCOMPLETE",
            "submitted_vendor": "Omega Supplies Inc",
            "registered_vendor": "Omega Supplies Inc",
            "submitted_tax_id": "TX-777888",
            "registered_tax_id": "TX-777888",
            "department_cost_center": None,
            "discrepancy": "missing_department_cost_center",
            "description": (
                "Missing department cost-center code on the purchase "
                "requisition; governance record incomplete."
            ),
        },
        {
            "case_id": "PO-9003",
            "workflow_type": "purchase_order_creation",
            "failure_category": "APPROVAL_TIMEOUT",
            "submitted_vendor": "Delta Logistics Ltd",
            "registered_vendor": "Delta Logistics Ltd",
            "submitted_tax_id": "TX-999000",
            "registered_tax_id": "TX-999000",
            "manager_sign_off_days": 7,
            "approval_sla_business_days": 5,
            "discrepancy": "approval_timeout",
            "description": (
                "Manager sign-off took 7 business days, exceeding the "
                "5-business-day SLA; approval timeout on manager sign-off."
            ),
        },
    ]


def generate_procurement_costs() -> list[dict]:
    """Generate transactional costs across 4 quarters.

    Embeds a Q3 processing cost spike caused by a surge in rush-delivery
    (expedited) fees.
    """
    rng = random.Random(20260304)
    rows: list[dict] = []
    order_id = 1

    base_cost = {
        "Q1": 100,
        "Q2": 105,
        "Q3": 300,  # Q3 processing cost spike (rush-delivery surge)
        "Q4": 110,
    }
    expedited_fee = {
        "Q1": 0,
        "Q2": 10,
        "Q3": 85,  # rush-delivery fee surge drives the Q3 anomaly
        "Q4": 0,
    }

    for quarter in ["Q1", "Q2", "Q3", "Q4"]:
        for _ in range(5):  # 5 orders per quarter -> 20 total
            department = rng.choice(DEPARTMENTS)
            supplier_tier = rng.choice(["Tier1", "Tier2"])
            shipping_method = "Expedited" if quarter == "Q3" else "Standard"
            cost = base_cost[quarter] + round(rng.uniform(-10, 15), 2)
            fee = expedited_fee[quarter] + round(rng.uniform(-5, 10), 2)
            rows.append(
                {
                    "order_id": f"O{order_id}",
                    "quarter": quarter,
                    "department": department,
                    "supplier_tier": supplier_tier,
                    "processing_cost": round(max(cost, 0), 2),
                    "shipping_method": shipping_method,
                    "expedited_fee": round(max(fee, 0), 2),
                }
            )
            order_id += 1

    return rows


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    SAMPLE_DATA.mkdir(parents=True, exist_ok=True)

    events = generate_event_log()
    write_csv(
        SAMPLE_DATA / "procurement_events.csv",
        events,
        [
            "case_id",
            "activity",
            "timestamp",
            "resource",
            "department",
            "estimated_cost",
            "currency",
        ],
    )

    failed = generate_failed_workflows()
    with (SAMPLE_DATA / "failed_workflows.json").open("w") as handle:
        json.dump(failed, handle, indent=2)
        handle.write("\n")

    costs = generate_procurement_costs()
    write_csv(
        SAMPLE_DATA / "procurement_costs.csv",
        costs,
        [
            "order_id",
            "quarter",
            "department",
            "supplier_tier",
            "processing_cost",
            "shipping_method",
            "expedited_fee",
        ],
    )

    print(
        f"Wrote {len(events)} events across {NUM_CASES} cases to "
        f"{SAMPLE_DATA / 'procurement_events.csv'}"
    )
    print(
        f"Wrote {len(failed)} failed workflows to "
        f"{SAMPLE_DATA / 'failed_workflows.json'}"
    )
    print(f"Wrote {len(costs)} cost records to {SAMPLE_DATA / 'procurement_costs.csv'}")


if __name__ == "__main__":
    main()
