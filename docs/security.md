# Workflow Intelligence MCP — Security

> **Version:** v1.0.0  

## 1. Security Philosophy

`workflow-intelligence-mcp` follows **fail-closed security**: any mutation,
escalation, or data access must validate caller permissions before acting.
Tool input parameters are strictly validated with Pydantic V2 schemas, and
no secret material is ever logged or persisted in plaintext.

The server is an **intelligence/reasoning layer**, not a workflow execution
engine. It does not execute arbitrary business transactions; it reads
event logs, classifies exceptions, and recommends decisions. Human
intervention is required for state-changing mutations (escalations,
resolutions).

---

## 2. Authentication & Authorization

MCP itself does not prescribe an authentication scheme. Authorization is the
responsibility of the MCP client / host (Claude Desktop, Cursor, custom
agent) and any transport-layer security in front of the server:

- **STDIO transport**: runs in the client's local process; trust boundary is
  the operating-system user account.
- **SSE / Streamable HTTP transport**: expose the server over a network.
  Deploy behind a reverse proxy with TLS and enforce client authentication
  (OAuth2 bearer tokens, mTLS, or API keys) at the ingress layer.
- **docker-compose**: bind the MCP server to `127.0.0.1` or an internal
  network in production; do not expose port 8000 to the public internet
  without an auth gateway.

There is no built-in user registry or RBAC in this release. Caller identity
is propagated as a `correlation_id` / `actor` string in audit logs for
traceability, not for access decisions.

---

## 3. Input Validation & Sanitization

All MCP tool parameters pass through Pydantic V2 models before reaching
application logic:

- String fields enforce `min_length=1` where empty values are meaningless.
- Numeric fields enforce boundaries (`gt=0`, `ge=1`, `le=100`).
- Enum fields (`ExceptionCategory`, `SeverityLevel`, `ExecutionStatus`)
  restrict values to a closed taxonomy.
- `failure_payload` and `policy_context` are `dict[str, Any]` — free-form
  but never interpolated into shell commands or raw SQL.

### SQL safety

All analytical queries are executed through DuckDB's **parameterized API**
(`?` / `$name` placeholders). Identifiers (table names, column names) are
never interpolated from user input; they are validated against an
allow-list or derived from file headers only. The `DuckDBAdapter`
sanitizes table names to alphanumeric-plus-underscore and escapes SQL
string literals when registering CSV/Parquet views.

### Path safety

File storage (`file_storage/loader.py`) rejects path-traversal attempts
(`..` components) **before** canonicalizing the path, and confines reads to
the resolved local filesystem path. No arbitrary URL fetching is performed.

### Secret handling

No API keys, passwords, or credentials are accepted as tool parameters.
The `DATABASE_URL` and `REDIS_URL` are read from environment variables
(`.env` / deployment secrets) and are **never** logged or echoed in tool
responses. The `.env` file is gitignored; only `.env.example` is tracked.

---

## 4. Audit Trail & Non-Repudiation

Every state mutation (investigation, resolution recommendation,
escalation) is recorded in the `AuditLog` table:

- `action_name` — the performed action (e.g. `exception_escalate_case`).
- `actor` — the caller/user/system identity.
- `timestamp` — UTC timestamp.
- `details_json` — structured details of the action.
- `correlation_id` — links related audit entries across tool calls.

Escalations produce a `ticket_id` (format `TKT-<n>`) and an `audit_narrative`
describing the case, reason, routed department, and priority. Audit records
are append-only and persisted to PostgreSQL.

---

## 5. Secrets & Credentials Management

| Secret | Source | Handling |
|--------|--------|----------|
| `DATABASE_URL` | `DATABASE_URL` env var | Never logged; used only for DB connections. |
| `REDIS_URL` | `REDIS_URL` env var | Never logged; used only for Redis connections. |
| Transport tokens | MCP host / reverse proxy | Handled outside this server. |

- Do not commit `.env` or any `*.pem`, `*.key`, `*.cert` (gitignored).
- Rotate database/Redis credentials on a regular schedule.
- Use a secrets manager (AWS Secrets Manager, HashiCorp Vault, GitHub
  Secrets) in CI/CD rather than plaintext environment files.

---

## 6. Deployment Hardening

- **Container**: The Docker image runs as a non-root user (`appuser`) and
  uses a distroless/slim Python base with only production dependencies.
- **Healthcheck**: verifies the server module imports and responds; does
  not expose internal state.
- **Network**: In `docker-compose`, PostgreSQL and Redis are on an internal
  bridge network; only the MCP server port (8000) is published.
- **Dependencies**: All runtime dependencies are pinned in
  `pyproject.toml`; CI runs `ruff` and `mypy` to catch unsafe constructs.

---

## 7. Known Limitations (v1.0.0)

- No built-in authentication or RBAC; authorization is delegated to the MCP
  host / ingress layer.
- Audit-log `actor` is a free-form string, not a cryptographically verified
  identity.
- Escalation tickets are in-memory counters per server process (no external
  ticketing integration).
- No encryption-at-rest configuration for the local DuckDB/SQLite files in
  this release; rely on filesystem/disk encryption at the host level.

---

## 8. Reporting Security Concerns

Report security issues to the project maintainer via the repository's
security advisory process or by opening a private issue. Do not disclose
vulnerabilities in public issues or discussions.
