# AGENTS.md — Agent Operating Guide for `workflow-intelligence-mcp`

> **Project Identity:** Workflow Intelligence MCP  
> **Tagline:** Turn fragmented enterprise processes into measurable, controlled agentic workflows.  
> **Mission:** Complement existing workflow engines (Temporal, n8n) with **Process Intelligence + Exception Reasoning + Evidence-Backed Business Decisions**.

---

## 1. Project Context & North Star

`workflow-intelligence-mcp` is an enterprise-grade Model Context Protocol (MCP) server written in Python. It does **not** compete with workflow execution engines like Temporal or n8n; rather, it sits alongside them as an **intelligence and reasoning layer**.

### The 3 Core MCP Tools

```
┌────────────────────────────────────────────────────────────────────────┐
│                      WORKFLOW INTELLIGENCE MCP                         │
├──────────────────────────┬─────────────────────────┬───────────────────┤
│ 1. Process Discovery     │ 2. Exception Resolver   │ 3. Decision Intel │
│    Engine (`process_`)   │    (`exception_`)       │    (`decision_`)  │
├──────────────────────────┼─────────────────────────┼───────────────────┤
│ • Event log ingestion    │ • Exception triage      │ • KPI deviation   │
│ • PM4Py reconstruction   │ • Context retrieval     │   root-cause      │
│ • Bottleneck detection   │ • Recovery suggestions  │ • DuckDB analysis │
│ • Automation scoring     │ • Escalation routing    │ • Evidence briefs │
└──────────────────────────┴─────────────────────────┴───────────────────┘
```

1. **Process Discovery Engine (`process_*`)**: Ingests event logs (CSV, JSON, SQL), reconstructs actual execution paths using process mining (PM4Py), detects delays and repeated tasks, and scores automation candidates.
2. **Workflow Exception Resolver (`exception_*`)**: Triages failed business workflows, pulls related context (ERP/CRM records, execution history), explains discrepancies, recommends recovery actions, and escalates when policy requires human intervention.
3. **Business Decision Intelligence (`decision_*`)**: Computes deterministic business metrics over operational databases, pinpoints the root cause of KPI deviations, links every claim to verified source records, and drafts structured decision briefs.

---

## 2. Architectural Blueprint

Every agent working in this repository must respect the **Modular Layered Architecture**:

```
MCP Clients (Claude Desktop, Cursor, Custom Agents)
                        │
                        ▼ (STDIO / Streamable HTTP)
               MCP Protocol Layer
           (FastMCP Server, Handlers)
                        │
                        ▼
            Validation & Security Layer
      (Pydantic V2 Schemas, AuthZ / Policy)
                        │
                        ▼
                Application Services
  (ProcessService, ExceptionService, DecisionService,
   AnalyticsService, EvidenceService, ReportingService)
                        │
                        ▼
                Adapters Layer
    (PM4Py, DuckDB, Temporal, n8n, Postgres/SQLA)
                        │
                        ▼
                   Persistence
        (PostgreSQL, DuckDB Local, Redis)
                        │
                        ▼
             Observability & Audit
          (structlog, OpenTelemetry)
```

### Key Architectural Rules

1. **Thin MCP Handlers**: Handlers in `src/workflow_intelligence/tools/` only parse parameters, call application services, and return validated schemas. Never put business logic in tool handlers.
2. **Deterministic Computation**: Calculations, aggregations, and metric evaluations **must** use deterministic code (DuckDB, Pandas, SQL). **Never let an LLM calculate KPI numbers or financial figures.** LLMs are strictly used for natural language explanation and drafting reports from verified numbers.
3. **Evidence & Provenance Tracking**: Every insight or recommendation produced by `decision_intelligence` must attach supporting evidence (dataset name, record IDs, query definition, calculation method).
4. **Adapter Isolation**: All external systems (Temporal, n8n, ERP mocks, databases) must sit behind abstract adapter interfaces in `src/workflow_intelligence/adapters/`. Local development and test suites must run seamlessly using local files and DuckDB without requiring third-party credentials.
5. **Fail-Closed Security**: Any mutation or escalation must validate caller permissions. Tool input parameters must be strictly validated with Pydantic.

---

## 3. Repository File Structure

```text
workflow-intelligence-mcp/
├── src/
│   └── workflow_intelligence/
│       ├── __init__.py
│       ├── server.py                   # FastMCP instance setup & entrypoint
│       ├── config.py                   # Pydantic Settings & env configuration
│       ├── dependencies.py             # Dependency injection container
│       │
│       ├── tools/                      # Thin MCP tool endpoints
│       │   ├── __init__.py
│       │   ├── process_discovery.py    # process_analyze, process_bottlenecks, etc.
│       │   ├── exception_resolver.py   # exception_investigate, exception_resolve, etc.
│       │   └── decision_intelligence.py# decision_kpi, decision_brief, etc.
│       │
│       ├── resources/                  # MCP Resources (maps, reports, metadata)
│       │   ├── __init__.py
│       │   ├── process_resources.py
│       │   ├── exception_resources.py
│       │   └── decision_resources.py
│       │
│       ├── prompts/                    # MCP Prompts (analysis templates)
│       │   ├── __init__.py
│       │   ├── process_analysis.py
│       │   ├── exception_analysis.py
│       │   └── decision_analysis.py
│       │
│       ├── services/                   # Core business logic layer
│       │   ├── __init__.py
│       │   ├── process_service.py      # PM4Py wrappers, process reconstruction
│       │   ├── exception_service.py    # Failure classification & resolution
│       │   ├── decision_service.py     # KPI deviation & decision brief engine
│       │   ├── analytics_service.py    # DuckDB / analytical calculations
│       │   ├── evidence_service.py     # Provenance tracking & record matching
│       │   └── reporting_service.py    # Structured report & brief generation
│       │
│       ├── adapters/                   # Integration boundaries
│       │   ├── __init__.py
│       │   ├── process_mining/         # PM4Py adapter
│       │   ├── analytics/              # DuckDB & SQL execution adapter
│       │   ├── database/               # PostgreSQL / SQLAlchemy repository adapter
│       │   ├── workflow_engine/        # Temporal / n8n interfaces & mock adapters
│       │   └── file_storage/           # Local CSV / Parquet / JSON loader
│       │
│       ├── schemas/                    # Pydantic data models (IO contracts)
│       │   ├── __init__.py
│       │   ├── common.py
│       │   ├── process.py
│       │   ├── exception.py
│       │   └── decision.py
│       │
│       ├── models/                     # Domain entities & database tables
│       ├── persistence/                # Repositories & migrations
│       ├── security/                   # Input sanitization, authorization gates
│       ├── observability/              # structlog configuration, OpenTelemetry
│       └── utils/                      # Formatting, time helpers
│
├── tests/
│   ├── unit/                          # Unit tests for services & schemas
│   ├── integration/                   # Adapters, DuckDB, file parsing
│   ├── protocol/                      # FastMCP tool discovery & invocation tests
│   ├── fixtures/                      # Sample event logs & failure scenarios
│   └── conftest.py
│
├── sample-data/                       # Datasets for demonstrations & tests
│   ├── procurement_events.csv
│   ├── failed_workflows.json
│   └── procurement_costs.csv
│
├── demo/                              # Walkthrough scripts & client configs
│   ├── procurement/
│   ├── workflow-exceptions/
│   └── business-intelligence/
│
├── docs/                              # Technical specifications
│   ├── architecture.md
│   ├── tool-reference.md
│   └── security.md
│
├── pyproject.toml
├── Dockerfile
├── docker-compose.yml
├── .env.example
├── README.md
├── AGENTS.md                          # (This file)
├── PLAN.md                            # Comprehensive master specification
├── CONTRIBUTING.md
└── LICENSE
```

---

## 4. Technology Stack & Dependencies

- **Language:** Python `>=3.12`
- **MCP Framework:** `mcp` (Official SDK) and `fastmcp`
- **Validation:** `pydantic >= 2.0`
- **Process Mining:** `pm4py >= 2.7`
- **Analytical Engine:** `duckdb >= 1.0`, `pandas >= 2.2`
- **Database / ORM:** `SQLAlchemy >= 2.0`, `asyncpg`, `psycopg2-binary` (PostgreSQL)
- **Workflow Connectors:** `temporalio`, `httpx` (n8n Webhook / API)
- **Logging & Tracing:** `structlog`, `opentelemetry-api`, `opentelemetry-sdk`
- **Testing:** `pytest`, `pytest-asyncio`, `pytest-cov`
- **Packaging:** `hatchling` or `flit` via `pyproject.toml`

---

## 5. Standard MCP Tool Catalog

### Tool 1: `process_*`
1. `process_analyze_business_process(source_uri: str, case_id_col: str, activity_col: str, timestamp_col: str) -> ProcessAnalysisResult`
2. `process_identify_bottlenecks(source_uri: str, threshold_std_dev: float = 2.0) -> BottleneckReport`
3. `process_score_automation_opportunities(source_uri: str, min_frequency: int = 10) -> AutomationScoreReport`

### Tool 2: `exception_*`
1. `exception_investigate_workflow(case_id: str, workflow_type: str, failure_payload: dict) -> ExceptionInvestigationResult`
2. `exception_recommend_resolution(exception_id: str, policy_context: dict | None = None) -> ResolutionPlan`
3. `exception_escalate_case(case_id: str, reason: str, target_department: str) -> EscalationResult`

### Tool 3: `decision_*`
1. `decision_analyze_kpi_performance(metric_name: str, timeframe_current: str, timeframe_baseline: str) -> PerformanceAnalysisResult`
2. `decision_explain_kpi_deviation(metric_name: str, observed_delta: float, dimension: str | None = None) -> DeviationExplanation`
3. `decision_generate_brief(topic: str, metric_ids: list[str]) -> DecisionBrief`

---

## 6. Implementation Phasing & Milestones

Agents should consult the following roadmap when picking up tasks:

### Phase 1: Foundation & v0.1.0 MVP (Immediate Focus)
- [ ] Initialize Python package environment (`pyproject.toml`, `.env.example`, `.gitignore`).
- [ ] Implement core Pydantic schemas in `src/workflow_intelligence/schemas/`.
- [ ] Build `ProcessService` with PM4Py adapter for CSV event log ingestion & bottleneck detection.
- [ ] Build `ExceptionService` with deterministic rule-based classifier and vendor matching.
- [ ] Build `DecisionService` & `AnalyticsService` backed by DuckDB for KPI deviations.
- [ ] Implement FastMCP server in `src/workflow_intelligence/server.py` exposing all 3 tool sets.
- [ ] Provide sample datasets in `sample-data/` (Procurement end-to-end scenario).
- [ ] Create pytest suite (unit + protocol tests).
- [ ] Author comprehensive `README.md` with client setup instructions.

### Phase 2: Engineering Maturity (v0.5.0)
- [ ] Add PostgreSQL repository persistence for workflows, exception logs, and audit trails.
- [ ] Add Temporal and n8n adapters with robust mocking for testing.
- [ ] Implement MCP Resources and Prompts.
- [ ] Dockerize application (`Dockerfile`, `docker-compose.yml` with Postgres + Redis).
- [ ] Integrate `structlog` and OpenTelemetry tracing.

### Phase 3: Production-Ready Hardening (v1.0.0)
- [ ] Complete CI/CD with GitHub Actions (lint, type-check with `mypy`, test with coverage).
- [ ] Implement authorization checkpoints for write/escalate operations.
- [ ] End-to-end automated demo script showcasing the Procurement scenario across all 3 tools.
- [ ] Finalize documentation in `docs/` and architecture diagrams.

---

## 7. Guidelines for AI Agents Working on this Codebase

When executing development tasks in this repository, follow these rules:

1. **Read Before Writing**: Check existing schemas and interfaces before implementing new modules. Do not reinvent schemas or duplicate service methods.
2. **Build Incrementally**: Do not create empty skeleton files or stub modules that contain nothing but `pass` or `TODO`. Write functional, tested code with real implementations.
3. **Strict Type Annotations**: All function signatures must have complete type hints (`typing` / built-in generics) and docstrings explaining inputs and outputs.
4. **No External Network Dependencies in Tests**: All unit and integration tests must run offline using local file fixtures, in-memory DuckDB, or SQLite/mocks.
5. **No Hallucinated Data**: In `decision_intelligence`, never invent explanations or metrics not present in the provided dataset. If data is insufficient, state the limitation explicitly in the decision brief.
6. **Follow Linux & Sandboxing Conventions**: Never attempt to run unauthorized network services or break container/environment isolation.
7. **Clean Git Hygiene**: Keep commits atomic, well-described, and mapped to specific roadmap milestones.
