# Workflow Intelligence MCP — Security & Hardening Reference

> **Version:** v1.0.1 (Hardened Release)  
> **Target Framework:** FastMCP & MCP Protocol Specification

---

## 1. Security Philosophy

`workflow-intelligence-mcp` follows **fail-closed security**: any mutation, escalation, or file/data access must validate caller permissions, path boundaries, and schema invariants before acting. Tool input parameters are strictly validated with Pydantic V2 schemas, all network endpoints require cryptographic bearer authentication or validated reverse-proxy attestation, and sensitive fields are systematically redacted from logs, database snapshots, and MCP responses.

The server operates as an **intelligence and reasoning layer** alongside workflow execution engines. It reads event logs, identifies operational bottlenecks, triages exceptions, and calculates deterministic business KPIs without executing unauthorized external mutations.

---

## 2. Authentication & Authorization

### 2.1 Transport Security Architecture

- **STDIO Transport**: Executes in the client's local execution boundary (e.g. Claude Desktop, Cursor). The trust boundary is enforced by OS-level file permissions and user sandboxing.
- **Network Transports (Streamable HTTP `/mcp` & Legacy SSE `/sse`)**:
  - Protected by `StaticTokenVerifier` implementing timing-attack-safe token comparison using Python's `hmac.compare_digest`.
  - In production (`WORKFLOW_ENV=production`), server startup **strictly fails** if network endpoints are exposed without a valid, non-empty, non-placeholder `MCP_AUTH_TOKEN`. A gateway flag alone (`TRUSTED_AUTH_GATEWAY=true`) cannot bypass authentication without an active token; network endpoints fail closed.
  - Template placeholder tokens (e.g. `replace_with_*`, `changeme`, `placeholder`) and insecure/default passwords are systematically rejected at startup in production mode.
  - Requests with missing, malformed, or invalid tokens are immediately rejected with HTTP `401 Unauthorized`.

---

## 3. Filesystem Sandboxing & Path Protection

All file access (CSV, JSON, OCEL, DuckDB sources) is routed through `validate_safe_path` in `src/workflow_intelligence/security/file_validator.py`:

1. **Path Traversal Blocking**: Absolute paths and relative paths containing `..` sequences are normalized using `pathlib.Path.resolve()`.
2. **Directory Allowlisting**: File reads are strictly constrained to configured directory roots (defaults: `sample-data/`, `data/`, `local/`).
3. **Sensitive System File Protection**: Prohibits access to critical host files and system directories (`/etc/passwd`, `/etc/shadow`, `/proc`, `/sys`, `/dev`, `~/.ssh`, `~/.aws`, `~/.kube`).
4. **Symlink Escape Prevention**: Symlinks pointing outside the allowlisted directories are blocked upon resolution.
5. **Denial-of-Service / File Size Limits**: Files exceeding `MAX_FILE_SIZE_BYTES` (default: 200 MB) are rejected before memory allocation.

---

## 4. Input Validation & Deterministic Computing

- All MCP tool parameters pass through Pydantic V2 models (`min_length`, boundary checks `gt=0`, `le=100`, closed enum taxonomies).
- **Parameterized SQL Execution**: DuckDB queries exclusively use parameterized execution (`?` placeholders). Table names and column identifiers are strictly sanitized and validated against data schemas.
- **Deterministic Computing Rule**: LLMs are never permitted to compute KPI figures or financial metrics. All statistics, z-scores, durations, and financial aggregates are calculated by PM4Py and DuckDB.

---

## 5. Sensitive Data Redaction & Provenance Tracking

- **Automated Field Redaction**: Payloads and audit details undergo recursive masking of sensitive credential fields (`password`, `secret`, `token`, `api_key`, `authorization`, `credit_card`, `ssn`, `cvv`) via `sanitize_payload()`.
- **Data Provenance**: Every metric analysis, root cause explanation, and executive decision brief includes SHA-256 dataset hashes, record byte counts, and exact table bindings.

---

## 6. Process Isolation & Execution Timeouts

- **Bounded Analysis Execution**: High-cardinality process mining jobs (e.g., BPIC19 with 1.6M events) are executed in isolated child processes (`spawn` multiprocessing context).
- **Hard Timeout Termination**: If an analysis exceeds `ANALYSIS_TIMEOUT_SECONDS` (default: 60s), the worker process is terminated (`terminate()` followed by `kill()`). This guarantees 100% reclamation of memory and CPU cores without memory leaks or zombie processes.

---

## 7. Persistent Exception Lifecycle & Audit Integrity

- **Database Backed State Machine**: The lifecycle (`Investigate -> Persist -> Retrieve -> Recommend -> Resolve/Escalate -> Audit`) uses PostgreSQL with transactional isolation.
- **Fail-Closed Persistence**: When `WORKFLOW_ENV=production`, database failures raise `ExceptionServiceError` immediately. Silent fallback to in-memory storage is strictly prohibited in production.
- **Idempotency & Duplicate Prevention**: Repeated investigations for the same workflow reuse existing open exception records, preventing duplicate records or contradictory resolutions.
- **Restart Recovery**: All exception records, resolution statuses, and audit log entries survive container restarts.

---

## 8. Deployment Hardening & Operational Runbooks

- **Least Privilege Non-Root Container**: The Docker image executes as non-root `appuser` (UID 10001) using a multi-stage non-editable Python build.
- **Network Isolation**: Docker Compose isolates PostgreSQL (5432) and Redis (6379) on an internal bridge network (`wimcp-network`). Only port 8000 is exposed to the host.
- **Readiness Probes**: `/ready` performs active health verification before routing client traffic.
- **PostgreSQL Backup & Recovery**:
  ```bash
  # Backup
  docker compose exec -T postgres pg_dump -U postgres workflow_intelligence > backup_$(date +%Y%m%d).sql

  # Restore
  docker compose exec -T postgres psql -U postgres workflow_intelligence < backup_file.sql
  ```
