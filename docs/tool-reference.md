# Workflow Intelligence MCP — Tool Reference

> **Version:** v1.0.0  
> **Protocol:** Model Context Protocol (MCP) over STDIO / SSE  
> **Total tools:** 9 across 3 suites  

All tool inputs are strictly validated with Pydantic V2. All calculations are
deterministic (DuckDB/SQL); LLMs draft narrative only from verified numbers.

---

## Suite 1: Process Discovery (`process_*`)

### `process_analyze_business_process`

Reconstruct actual execution paths from an event log using PM4Py process
mining.

**Input parameters**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `source_uri` | `str` | Yes | URI/path to the event log (CSV, JSON, SQL). |
| `case_id_col` | `str` | Yes | Column name for case identifiers. |
| `activity_col` | `str` | Yes | Column name for activity names. |
| `timestamp_col` | `str` | Yes | Column name for event timestamps. |

**Output** — `ProcessAnalysisResult`: `total_cases`, `total_events`,
`unique_activities`, `variant_count`, `top_variants`,
`average_case_duration_hours`, `transitions` (list of `StageMetric`).

**Example call**
```json
{
  "source_uri": "sample-data/procurement_events.csv",
  "case_id_col": "case_id",
  "activity_col": "activity",
  "timestamp_col": "timestamp"
}
```

**Example output (abridged)**
```json
{
  "success": true,
  "total_cases": 500,
  "total_events": 3108,
  "unique_activities": ["Purchase Request","Manager Approval","Finance Approval","Vendor Selection","Purchase Order"],
  "variant_count": 8,
  "average_case_duration_hours": 21.73,
  "transitions": [
    {"source_activity":"Purchase Request","target_activity":"Manager Approval","transition_count":500,"mean_duration_hours":2.5,...}
  ]
}
```

---

### `process_identify_bottlenecks`

Detect process stages whose mean cycle time exceeds a z-score threshold
relative to the global baseline.

**Input parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `source_uri` | `str` | Yes | — | URI/path to the event log. |
| `threshold_std_dev` | `float` | No | `2.0` | Z-score threshold (must be > 0). |

**Output** — `BottleneckReport`: `source_uri`, `total_stages_analyzed`,
`bottlenecks` (list of `BottleneckItem` with `stage_transition`,
`avg_duration_hours`, `baseline_duration_hours`, `z_score`,
`affected_case_count`, `recommendation`).

---

### `process_score_automation_opportunities`

Score repetitive activities for automation suitability using
`Score = f(frequency, duration, 1/variance)`.

**Input parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `source_uri` | `str` | Yes | — | URI/path to the event log. |
| `min_frequency` | `int` | No | `10` | Minimum frequency (≥ 1). |

**Output** — `AutomationScoreReport`: `candidates` (ranked list of
`AutomationCandidate` with `activity_name`, `execution_frequency`,
`avg_duration_minutes`, `manual_effort_score`,
`automation_potential_score`, `rank`), `methodology`.

---

## Suite 2: Workflow Exception Resolver (`exception_*`)

### `exception_investigate_workflow`

Triages a failed business workflow, classifies the failure, matches
entities against master data, and builds a structured investigation result.

**Input parameters**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `case_id` | `str` | Yes | Unique case identifier. |
| `workflow_type` | `str` | Yes | Type of workflow that failed. |
| `failure_payload` | `dict` | Yes | Free-form failure details. |

**Output** — `ExceptionInvestigationResult`: `case_id`, `exception_id`,
`category` (one of `MASTER_DATA_DISCREPANCY`,
`GOVERNANCE_INCOMPLETE`, `CONCURRENCY_CONFLICT`,
`TRANSIENT_NETWORK_DROP`, `FINANCIAL_POLICY_BREACH`),
`matched_entities`, `classification_reason`, `severity`,
`auto_resolvable`, `recommended_remediation`, `audit_narrative`.

**Example call**
```json
{
  "case_id": "PO-9001",
  "workflow_type": "purchase_order_creation",
  "failure_payload": {
    "vendor_name": "ABC Technologies",
    "tax_id": "TX-123456",
    "discrepancy": "vendor_name_typo",
    "master_records": [
      {"entity_id":"VEND-001","registered_name":"ABC Technology Pvt Ltd","tax_id":"TX-123456"}
    ]
  }
}
```

---

### `exception_recommend_resolution`

Produces a policy-driven resolution plan for an exception.

**Input parameters**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `exception_id` | `str` | Yes | Unique exception identifier. |
| `policy_context` | `dict \| null` | No | Optional policy constraints (e.g. auto-approve, approval tier). |

**Output** — `ResolutionPlan`: `exception_id`, `remediation_action`,
`auto_resolve`, `required_approval_tier`.

---

### `exception_escalate_case`

Routes an unresolved exception to a target department and records an
audit trail entry.

**Input parameters**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `case_id` | `str` | Yes | Unique case identifier. |
| `reason` | `str` | Yes | Reason for escalation. |
| `target_department` | `str` | Yes | Department to route the case to. |

**Output** — `EscalationResult`: `ticket_id`, `routed_department`,
`priority`, `audit_narrative`.

---

## Suite 3: Business Decision Intelligence (`decision_*`)

### `decision_analyze_kpi_performance`

Computes period-over-period KPI comparison (absolute and relative delta)
using deterministic DuckDB aggregation.

**Input parameters**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `metric_name` | `str` | Yes | Registered KPI metric (e.g. `processing_cost`). |
| `timeframe_current` | `str` | Yes | Current period identifier (e.g. `Q3`). |
| `timeframe_baseline` | `str` | Yes | Baseline period identifier (e.g. `Q2`). |

**Output** — `PerformanceAnalysisResult`: `metric_name`,
`baseline_value`, `current_value`, `delta_percentage`.

**Example call**
```json
{
  "metric_name": "processing_cost",
  "timeframe_current": "Q3",
  "timeframe_baseline": "Q2"
}
```

---

### `decision_explain_kpi_deviation`

Attributes an observed KPI delta to the top contributing dimensional
drivers and attaches deterministic statistical significance indicators.

**Input parameters**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `metric_name` | `str` | Yes | Registered KPI metric. |
| `observed_delta` | `float` | Yes | Observed delta between current and baseline. |
| `dimension` | `str \| null` | No | Dimension to segment by (e.g. `department`). |

**Output** — `DeviationExplanation`: `metric_name`, `observed_delta`,
`dimension`, `top_contributors` (list of `DimensionAttribution`),
`statistical_significance`, `explanation`, `data_insufficient`,
`warning` (set when data is missing or not statistically significant).

---

### `decision_generate_brief`

Combines verified KPI metrics into a structured markdown decision brief
(Executive Summary, Observed Variance, Primary Contributing Drivers,
Provenance Footnotes, Recommended Interventions).

**Input parameters**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `topic` | `str` | Yes | Topic of the decision brief. |
| `metric_ids` | `list[str]` | Yes | List of registered metric identifiers. |

**Output** — `DecisionBrief`: `topic`, `executive_summary`,
`findings` (list of `MetricComparison`), `attributions`
(list of `DimensionAttribution`), `provenance_links`,
`recommendations`.

**Example call**
```json
{
  "topic": "Q3 Procurement Cost Inflation",
  "metric_ids": ["processing_cost", "order_count"]
}
```

---

## Registered KPI Metrics

| Metric | Expression | Description |
|--------|------------|-------------|
| `processing_cost` | `SUM(processing_cost)` | Total processing cost in the period. |
| `avg_processing_cost` | `AVG(processing_cost)` | Average processing cost per order. |
| `total_expedited_fee` | `SUM(expedited_fee)` | Total expedited shipping fees. |
| `avg_expedited_fee` | `AVG(expedited_fee)` | Average expedited fee per order. |
| `order_count` | `COUNT(order_id)` | Number of orders. |
| `expedited_share_percentage` | share formula | Share of expedited orders (computed by DuckDB). |

---

## MCP Resources (`workflow://*`)

| URI | MIME type | Description |
|-----|-----------|-------------|
| `workflow://process-maps/{process_name}` | `application/json` | Directly-Follows Graph JSON. |
| `workflow://exceptions/active` | `application/json` | Open workflow failures queue. |
| `workflow://metrics/kpi-definitions` | `text/markdown` | KPI calculation rules catalog. |
| `workflow://audit/recent` | `application/json` | Recent audit-log entries. |

---

## MCP Prompts

| Prompt | Purpose |
|--------|---------|
| `process_bottleneck_review` | LLM review of bottlenecks + automation targets. |
| `exception_triage_session` | Step-by-step operator diagnostic workflow. |
| `management_decision_brief` | Executive briefing from verified KPI findings. |

---

## Provenance & Zero-Hallucination Guarantee

- Every `decision_*` output cites its source dataset (path, SHA-256 hash,
  record count, table, periods) in `provenance_links`.
- When data is missing or statistically insignificant, tools return
  `data_insufficient: true` with an explicit `warning` rather than
  fabricating figures.
- Unknown metric IDs raise a validation error — no metric is ever invented.

---

## Testing from the Terminal

All commands run offline against the `sample-data/` fixtures — no internet
required.

### 1. Run the full verification script (9 tools + 4 resources + 3 prompts)

```bash
.venv/bin/python scripts/verify_mcp_local.py
```

### 2. Run the end-to-end procurement demo (DISCOVER → RESOLVE → DECIDE)

```bash
.venv/bin/python demo/procurement/run_demo.py
```

### 3. Call individual decision tools

```bash
# KPI performance (period-over-period variance)
.venv/bin/python -c "
from workflow_intelligence.services.decision_service import DecisionService
from workflow_intelligence.schemas.decision import KPICalculationRequest
svc = DecisionService()
r = svc.analyze_kpi_performance(KPICalculationRequest(
    metric_name='processing_cost',
    source_uri='sample-data/procurement_costs.csv',
    timeframe_column='quarter',
    timeframe_current='Q3',
    timeframe_baseline='Q2',
))
print(r)
"

# KPI deviation explanation
.venv/bin/python -c "
from workflow_intelligence.services.decision_service import DecisionService
from workflow_intelligence.schemas.decision import KPIExplanationRequest
svc = DecisionService()
r = svc.explain_kpi_deviation(KPIExplanationRequest(
    metric_name='processing_cost',
    source_uri='sample-data/procurement_costs.csv',
    timeframe_column='quarter',
    timeframe_current='Q3',
    timeframe_baseline='Q2',
    observed_delta=925.0,
    dimension='department',
))
print(r.explanation)
"

# Decision brief
.venv/bin/python -c "
from workflow_intelligence.services.decision_service import DecisionService
from workflow_intelligence.schemas.decision import DecisionBriefRequest
svc = DecisionService()
r = svc.generate_brief(DecisionBriefRequest(
    topic='Q3 Procurement Cost Inflation',
    metric_ids=['processing_cost', 'order_count'],
))
print(r.topic, len(r.findings), 'findings')
"
```

### 4. Call process discovery tools

```bash
# Analyze business process
.venv/bin/python -c "
from workflow_intelligence.services.process_service import ProcessService
from workflow_intelligence.schemas.process import ProcessAnalyzeRequest
svc = ProcessService()
r = svc.analyze_business_process(ProcessAnalyzeRequest(
    source_uri='sample-data/procurement_events.csv',
    case_id_col='case_id',
    activity_col='activity',
    timestamp_col='timestamp',
))
print('cases:', r.total_cases, '| events:', r.total_events, '| variants:', r.variant_count)
"

# Identify bottlenecks
.venv/bin/python -c "
from workflow_intelligence.services.process_service import ProcessService
from workflow_intelligence.schemas.process import BottleneckRequest
svc = ProcessService()
r = svc.identify_bottlenecks(BottleneckRequest(
    source_uri='sample-data/procurement_events.csv', threshold_std_dev=2.0
))
print('bottlenecks:', len(r.bottlenecks))
for b in r.bottlenecks:
    print(f'  {b.stage_transition}: z={b.z_score:.2f}')
"

# Score automation opportunities
.venv/bin/python -c "
from workflow_intelligence.services.process_service import ProcessService
from workflow_intelligence.schemas.process import AutomationScoreRequest
svc = ProcessService()
r = svc.score_automation_opportunities(AutomationScoreRequest(
    source_uri='sample-data/procurement_events.csv', min_frequency=10
))
for c in r.candidates:
    print(f'  rank {c.rank}: {c.activity_name} score={c.automation_potential_score:.2f}')
"
```

### 5. Call exception resolver tools

```bash
# Investigate a failure
.venv/bin/python -c "
from workflow_intelligence.services.exception_service import ExceptionService
from workflow_intelligence.schemas.exception import WorkflowInvestigationRequest
svc = ExceptionService()
r = svc.investigate_failure(WorkflowInvestigationRequest(
    case_id='PO-9001',
    workflow_type='purchase_order_creation',
    failure_payload={
        'vendor_name': 'ABC Technologies',
        'tax_id': 'TX-123456',
        'discrepancy': 'vendor_name_typo',
        'master_records': [{
            'entity_id': 'VEND-001',
            'registered_name': 'ABC Technology Pvt Ltd',
            'tax_id': 'TX-123456',
        }],
    },
))
print(r.category, '| auto_resolvable:', r.auto_resolvable)
"

# Recommend resolution
.venv/bin/python -c "
from workflow_intelligence.services.exception_service import ExceptionService
from workflow_intelligence.schemas.exception import ResolutionRequest
svc = ExceptionService()
r = svc.recommend_resolution(ResolutionRequest(
    exception_id='EXC-001',
    policy_context={'auto_approve': True, 'approval_tier': 'manager'},
))
print('auto_resolve:', r.auto_resolve, '|', r.remediation_action)
"

# Escalate a case
.venv/bin/python -c "
from workflow_intelligence.services.exception_service import ExceptionService
svc = ExceptionService()
r = svc.escalate_case(
    case_id='PO-9002',
    reason='Missing department cost-center code',
    target_department='Finance',
)
print(r.ticket_id, '|', r.routed_department, '|', r.priority)
```

### 6. Start the MCP server over SSE (for MCP clients)

```bash
# Terminal 1 — start the server
.venv/bin/python -m workflow_intelligence.server --transport sse --port 8000

# Terminal 2 — query a resource via curl
curl -N http://localhost:8000/sse
```

Or connect Claude Desktop / Cursor via STDIO:

```json
{
  "mcpServers": {
    "workflow-intelligence": {
      "command": "python",
      "args": ["-m", "workflow_intelligence.server", "--transport", "stdio"],
      "cwd": "/home/rails/Work/Github-Repos/workflow-intelligence-mcp"
    }
  }
}
```

