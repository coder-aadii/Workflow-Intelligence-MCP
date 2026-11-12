# Workflow Intelligence MCP — Architecture

> **Version:** v1.0.0  
> **Milestone:** Production-Ready Release  

## 1. Overview

`workflow-intelligence-mcp` is an enterprise-grade Model Context Protocol (MCP)
server written in Python. It sits alongside workflow execution engines
(Temporal, n8n) as an **intelligence and reasoning layer** rather than
competing with them.

It exposes three core tool suites, four MCP resources, three MCP prompts,
a persistence layer (PostgreSQL), a cache layer (Redis), and enterprise
adapters for Temporal and n8n — all instrumented with structured logging
and OpenTelemetry tracing.

```
MCP Clients (Claude Desktop, Cursor, Custom Agents)
                        │
                        ▼  (STDIO / Streamable HTTP / SSE)
               MCP Protocol Layer
          (FastMCP Server — tools, resources, prompts)
                        │
                        ▼
            Validation & Security Layer
      (Pydantic V2 Schemas, Input Sanitization)
                        │
                        ▼
                Application Services
  (ProcessService, ExceptionService, DecisionService,
   AnalyticsService, ReportingService, EvidenceService)
                        │
                        ▼
                Adapters Layer
   (PM4Py, DuckDB, Temporal, n8n, PostgreSQL, Redis, File)
                        │
                        ▼
                   Persistence
          (PostgreSQL 16, DuckDB Local, Redis 7)
                        │
                        ▼
             Observability & Audit
          (structlog JSON logs, OpenTelemetry spans)
```

## 2. Core MCP Tools (`9 domain actions`)

| Tool suite | Tool | Purpose |
|------------|------|---------|
| `process_*` | `process_analyze_business_process` | Ingest event log, reconstruct execution paths via PM4Py. |
| `process_*` | `process_identify_bottlenecks` | Detect stages exceeding z-score duration thresholds. |
| `process_*` | `process_score_automation_opportunities` | Rank automation candidates by frequency/duration/variance. |
| `exception_*` | `exception_investigate_workflow` | Triage failures, match entities against master data. |
| `exception_*` | `exception_recommend_resolution` | Policy-driven remediation + auto-resolve recommendation. |
| `exception_*` | `exception_escalate_case` | Route unresolved cases to a target department (audit trail). |
| `decision_*` | `decision_analyze_kpi_performance` | Period-over-period KPI comparison (absolute + relative delta). |
| `decision_*` | `decision_explain_kpi_deviation` | Attribute a KPI delta to top dimensional drivers + significance. |
| `decision_*` | `decision_generate_brief` | Markdown executive decision brief with provenance links. |

All calculations are **deterministic** (DuckDB / SQL). LLMs are used only
for narrative explanation and report drafting — never to compute figures.

## 3. MCP Resources (`workflow://*`)

| URI | MIME type | Description |
|-----|-----------|-------------|
| `workflow://process-maps/{process_name}` | `application/json` | Directly-Follows Graph (nodes, edges, average duration). |
| `workflow://exceptions/active` | `application/json` | Open workflow failures awaiting resolution. |
| `workflow://metrics/kpi-definitions` | `text/markdown` | Catalog of KPI calculation rules and SQL expressions. |
| `workflow://audit/recent` | `application/json` | Tail of recent audit-log entries (escalations, recoveries, scans). |

## 4. MCP Prompts

| Prompt | Purpose |
|--------|---------|
| `process_bottleneck_review` | Structured LLM review of detected bottlenecks + automation targets. |
| `exception_triage_session` | Step-by-step diagnostic workflow for human operators. |
| `management_decision_brief` | Executive-ready briefing prompt from verified DuckDB KPI findings. |

## 5. Persistence Layer (SQLAlchemy 2.0 Async)

| Model | Key attributes |
|-------|----------------|
| `WorkflowInstance` | `id`, `workflow_type`, `status`, `start_time`, `end_time`, `metadata_json` |
| `ExceptionRecord` | `id`, `workflow_id` (FK), `category`, `root_cause`, `is_resolved`, `resolved_at`, `payload` |
| `AuditLog` | `id`, `action_name`, `actor`, `timestamp`, `details_json`, `correlation_id` |

Repositories: `WorkflowRepository`, `ExceptionRepository`, `AuditRepository`.
Schema versioning: Alembic (baseline `001_initial_schema`).

## 6. Storage & Caching

- **PostgreSQL 16**: audit trails, cases, exceptions (production).
- **SQLite** (aiosqlite): offline/local test databases (no credentials).
- **Redis 7**: cache computed process-transition graphs (TTL 1 hour) and track
  active exception triage sessions to prevent duplicate escalations.
- **In-memory cache**: offline mock used in tests/local dev when Redis is absent.

## 7. Workflow Engine Adapters

| Adapter | Methods | Implementation |
|---------|---------|----------------|
| `TemporalAdapter` | `get_workflow_history`, `send_update_signal`, `query_workflow_state` | `temporalio` SDK (live) + `MockTemporalAdapter` (offline JSON fixtures). |
| `N8NAdapter` | `dispatch_webhook`, `get_execution_log` | `httpx` with exponential-backoff retries + `MockN8NAdapter` (in-memory log). |

Both expose the `BaseWorkflowEngineAdapter` interface. Mocks are used by
default in tests; no live Temporal/n8n instance is required.

## 8. Observability

- **Logging**: `structlog` with JSON renderer (production) / colored console
  (development). Every log line carries `correlation_id`, `tool_name`,
  `case_id` (when applicable), and `duration_ms`.
- **Tracing**: OpenTelemetry `TracerProvider`. Standard spans:
  `mcp.tool.invoke`, `pm4py.discover_dfg`, `duckdb.query`.
  Dev mode exports spans to stdout via `ConsoleSpanExporter`; production
  wires an OTLP exporter.

## 9. Data Flow (Unified Procurement Scenario)

1. **DISCOVER** — `process_*` ingests `sample-data/procurement_events.csv`,
   discovers the 74-hour delay at Finance Approval → Vendor Selection,
   ranks `Manager Approval` as the #1 automation target.
2. **RESOLVE** — `exception_*` triages the failed PO in
   `sample-data/failed_workflows.json`, resolves the vendor mismatch
   (`ABC Tech` → `ABC Technology Pvt Ltd`), writes the audit log, and
   escalates unresolved cases.
3. **DECIDE** — `decision_*` queries `sample-data/procurement_costs.csv`
   via DuckDB, attributes the Q3 cost spike to rush-shipping fees, and
   drafts an executive decision brief with source-data provenance links
   (dataset path, SHA-256 hash, record count, query timestamp).

## 10. Directory Layout

```
workflow-intelligence-mcp/
├── .github/workflows/ci.yml        # GitHub Actions CI (lint, typecheck, test, docker-build)
├── Dockerfile                      # Multi-stage build (builder -> distroless runtime, appuser)
├── docker-compose.yml              # MCP server + postgres 16 + redis 7 (named volumes)
├── pyproject.toml                  # Hatchling build, dependencies, ruff/mypy/pytest config
├── sample-data/                    # Fixture datasets (events, failed workflows, costs)
├── demo/procurement/run_demo.py    # End-to-end unified procurement scenario CLI
├── docs/                           # architecture.md, tool-reference.md, security.md
├── scripts/                        # generate_sample_data.py, verify_mcp_local.py
├── src/workflow_intelligence/
│   ├── __init__.py                 # package version (0.5.0)
│   ├── server.py                   # FastMCP assembly, lifespan, CLI
│   ├── config.py                   # Pydantic BaseSettings
│   ├── tools/                      # Thin MCP tool endpoints (process, exception, decision)
│   ├── resources/                  # MCP Resources (workflow://*)
│   ├── prompts/                    # MCP Prompts (process_bottleneck_review, ...)
│   ├── services/                   # Application logic (process, exception, decision, analytics, reporting, evidence)
│   ├── adapters/                   # DuckDB analytics, PM4Py, file storage, cache, workflow engine
│   ├── models/                     # SQLAlchemy ORM domain models
│   ├── persistence/                # async DB engine, session, Alembic migrations, repositories
│   ├── observability/              # structlog logging, OpenTelemetry tracing
│   ├── schemas/                    # Pydantic V2 data contracts
│   └── security/                   # input sanitization, authorization gates
└── tests/                          # unit, integration, protocol suites
```

## 11. Release

The v1.0.0 release is tagged `v1.0.0`. See `CHANGELOG` / git history for
per-milestone commits (v0.1.0 Phase 4, v0.5.0 Phases 5–7, v1.0.0 Phase 8).
