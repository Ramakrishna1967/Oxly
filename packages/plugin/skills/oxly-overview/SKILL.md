# Oxly Overview (Phase 2)

Oxly is trace / span observability for AI agents, exposed to assistants
through 6 project-scoped, read-only MCP tools at `/mcp` (Streamable HTTP).

## Auth (every tool call)

- Pass the project's SDK API key (`ak_...`, issued once by
  `POST /api/v1/projects`) as `api_key` on every call.
- Calls scope to the key's project. An optional `project_id` must match
  the key's project, otherwise the tool fails with `unknown project`
  (bad key and wrong project are indistinguishable by design).
- `DEMO_MODE=true` allows keyless calls with an explicit `project_id`
  for local exploration only.
- The toolset is read-only: project creation, deletion, and span
  ingestion stay on the REST API under JWT / `X-API-Key` auth.

## Tools

- `whoami(api_key)` — confirm which project a key is scoped to.
  Start here when unsure which key you hold.
- `query_traces(api_key, project_id?, status?, limit?=20, offset?=0)` —
  list traces newest-first with `span_count` (limit max 50).
- `get_trace(trace_id, api_key)` — full trace with spans in replay
  order (max 200 spans, `spans_truncated` flag when capped).
- `get_span(span_id, api_key)` — one span: timing, status, service,
  attributes, events.
- `query_security_alerts(api_key, project_id?, severity?, limit?=25)` —
  prompt-injection / PII / anomaly alerts, newest first.
  `severity` in `low | medium | high | critical` (limit max 100).
- `cost_summary(api_key, project_id?, interval?=day, start_date?, end_date?)` —
  total cost/tokens, per-model breakdown, and timeseries.
  `interval` in `hour | day | week`; dates are Unix seconds
  (same convention as `GET /api/v1/analytics/cost`).

## Typical flows

1. `whoami` → `query_traces` → `get_trace` (debug a failing run).
2. `query_security_alerts(severity=high)` → `get_span` (inspect the
   flagged span's attributes).
3. `cost_summary(interval=week)` → per-model spend for the status update.

## Errors

- `unknown project` — bad `api_key` or mismatched `project_id`.
- `unknown trace` / `unknown span` — id not found *or* not in the key's
  project (no cross-project oracle).
- `unknown severity|interval '...'` — with the valid values listed.
