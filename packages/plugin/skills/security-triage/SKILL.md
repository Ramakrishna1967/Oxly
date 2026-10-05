# Security Triage (Phase 5)

Triage prompt-injection, PII, and anomaly alerts using Oxly's project-scoped,
read-only MCP tools at `/mcp`. Auth: pass the project's SDK API key
(`ak_...`) as `api_key` on every call; optional `project_id` must match the
key's project or the tool fails with `unknown project` (bad key and wrong
project are indistinguishable by design).

## Tool chain

1. `query_security_alerts(api_key, project_id?, severity?, limit?=25, offset?=0)` —
   alerts newest-first (limit max 100, `offset` for paging).
   `severity` in `low | medium | high | critical` (case-insensitive;
   anything else fails with `unknown severity '...'` listing valid values).
2. `get_span(span_id, api_key, project_id?)` — inspect the flagged span's `attributes`
   and `events` for the injected prompt or leaked PII.
3. `get_trace(trace_id, api_key, project_id?)` — surrounding spans in replay order to see
   which LLM call consumed the tainted input and what followed.

## Alert shape

Each alert carries `id, trace_id, span_id, project_id, severity (lowercase),
alert_type (rule_name), message, metadata (object), created_at`.
`metadata` is a parsed JSON object (empty `{}` when absent).

## Errors

- `unknown project` — bad key or mismatched `project_id`.
- `unknown trace` / `unknown span` — id not found *or* outside the key's
  project. Do not distinguish the two cases.
- Start with `severity=high` (or `critical`) for the typical triage flow,
  then widen to unfiltered when the queue is clear.

## Phase 5 notes

- `query_security_alerts` returns `total` + `count` + echoed
  `limit`/`offset` (max 100, `offset` for paging).
