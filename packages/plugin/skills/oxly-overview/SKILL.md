# Oxly Overview (Phase 0 placeholder)

This skill is a placeholder for Phase 0. It documents what the Oxly plugin
will expose once tools land in Phase 1+.

## What Oxly does

- Trace / span observability for AI agents
- Cost attribution per model / project
- Security rules (PII, injection, anomaly)

## Phase 0 status

- MCP endpoint live at `/mcp` (Streamable HTTP)
- Empty tool list is EXPECTED — `initialize` must succeed, `tools/list` returns `[]`
- Auth hook point: `get_user_project_ids`, `verify_project_ownership` in `packages/api/src/api/dependencies.py`
- DB hook point: `packages/api/src/api/db.py` (`get_database`, `get_db`)

## Next phases will add

- `oxly.query_traces`, `oxly.get_trace`, `oxly.cost_summary`, `oxly.security_alerts`
- All tools project-scoped via `verify_project_ownership`
