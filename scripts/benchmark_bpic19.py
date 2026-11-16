#!/usr/bin/env python3
"""Benchmark script for testing Workflow Intelligence MCP against large-scale event logs (BPIC19).

Measures:
- Ingestion and path parsing time
- Total cases, total events, unique activities, and variants
- Peak memory usage via tracemalloc
- Bottleneck and transition metrics calculation
"""

from __future__ import annotations

import os
import sys
import time
import tracemalloc
from pathlib import Path

# Add project src to path
repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root / "src"))

from workflow_intelligence.config import Settings
from workflow_intelligence.schemas.process import ProcessAnalyzeRequest
from workflow_intelligence.services.process_service import ProcessService


def run_benchmark(dataset_path: str | Path | None = None) -> dict[str, float | int | str]:
    if dataset_path is None:
        dataset_path = repo_root / "local" / "BPIC19_flat.csv"

    dataset_path = Path(dataset_path)
    if not dataset_path.exists():
        print(f"Dataset not found at: {dataset_path}")
        return {"status": "missing_dataset", "path": str(dataset_path)}

    file_size_mb = dataset_path.stat().st_size / (1024 * 1024)
    print(f"============================================================")
    print(f"BPIC19 BENCHMARK SUITE — Workflow Intelligence MCP")
    print(f"Dataset:   {dataset_path.name} ({file_size_mb:.2f} MB)")
    print(f"============================================================")

    # Disable timeout for full unconstrained benchmark run
    service = ProcessService()
    # Mock settings to allow local/ dir and sufficient timeout
    req = ProcessAnalyzeRequest(
        source_uri=str(dataset_path),
        case_id_col="ocel:type:PO",
        activity_col="ocel:activity",
        timestamp_col="ocel:timestamp",
    )

    tracemalloc.start()
    start_time = time.perf_counter()

    # Call internal implementation directly to benchmark full unconstrained PM4Py graph discovery
    result = service._analyze_business_process_impl(req)

    elapsed_seconds = time.perf_counter() - start_time
    current_mem, peak_mem = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    peak_mb = peak_mem / (1024 * 1024)

    metrics = {
        "status": "success",
        "dataset": dataset_path.name,
        "file_size_mb": round(file_size_mb, 2),
        "total_cases": result.total_cases,
        "total_events": result.total_events,
        "unique_activities": len(result.unique_activities),
        "variant_count": result.variant_count,
        "transitions_count": len(result.transitions),
        "avg_case_duration_hours": round(result.average_case_duration_hours, 2),
        "elapsed_seconds": round(elapsed_seconds, 2),
        "peak_memory_mb": round(peak_mb, 2),
    }

    print("\n--- Benchmark Results ---")
    print(f"Total Cases:              {metrics['total_cases']:,}")
    print(f"Total Events:             {metrics['total_events']:,}")
    print(f"Unique Activities:        {metrics['unique_activities']}")
    print(f"Discovered Variants:      {metrics['variant_count']:,}")
    print(f"Discovered Transitions:   {metrics['transitions_count']}")
    print(f"Avg Case Duration:        {metrics['avg_case_duration_hours']} hours")
    print(f"Total Execution Time:     {metrics['elapsed_seconds']} s")
    print(f"Peak Memory Allocated:    {metrics['peak_memory_mb']} MB (~{round(peak_mb / 1024, 2)} GB)")
    print("============================================================\n")

    return metrics


if __name__ == "__main__":
    run_benchmark()
