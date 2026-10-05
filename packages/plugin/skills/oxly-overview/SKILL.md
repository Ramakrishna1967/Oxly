# Oxly Overview (Phase 6)

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

- `whoami(api_key, project_id?)` — confirm which project a key is scoped to.
  Start here when unsure which key you hold.
- `query_traces(api_key, project_id?, status?, limit?=20, offset?=0)` —
  list traces newest-first with `span_count` (limit max 50).
  `status` in `OK | ERROR` (anything else fails with `unknown status '...'`).
- `get_trace(trace_id, api_key, project_id?, span_limit?=200, span_offset?=0)` —
  full trace with spans in replay order, paged (`span_limit` max 200,
  `span_offset` for pages; `span_count` is the true total,
  `spans_truncated` flags remaining pages).
- `get_span(span_id, api_key, project_id?)` — one span: timing, status, service,
  attributes, events.
- `query_security_alerts(api_key, project_id?, severity?, limit?=25, offset?=0)` —
  prompt-injection / PII / anomaly alerts, newest first (limit max 100).
  `severity` in `low | medium | high | critical` (limit max 100).
- `cost_summary(api_key, project_id?, interval?=day, start_date?, end_date?)` —
  total cost/tokens, per-model breakdown, and timeseries.
  `interval` in `hour | day | week`; dates are Unix seconds
  (same convention as `GET /api/v1/analytics/cost`).

## Task skills

- `trace-debug` — failing-run workflow
  (`whoami` → `query_traces` → `get_trace` → `get_span`).
- `security-triage` — alert queue workflow
  (`query_security_alerts` → `get_span` → `get_trace`).
- `cost-report` — spend workflow (`cost_summary` → per-model + timeseries).

## Typical flows

1. `whoami` → `query_traces` → `get_trace` (debug a failing run;
   see `trace-debug`).
2. `query_security_alerts(severity=high)` → `get_span` (inspect the
   flagged span's attributes; see `security-triage`).
3. `cost_summary(interval=week)` → per-model spend for the status update
   (see `cost-report`).

## Errors

- `unknown project` — bad `api_key` or mismatched `project_id`.
- `unknown trace` / `unknown span` — id not found *or* not in the key's
  project (no cross-project oracle).
- `unknown severity|interval|status '...'` — with the valid values listed.

## Phase 6 notes

- `get_trace` pages spans via `span_limit` (default 200, max 200) +
  `span_offset` (default 0): walk large traces with
  `span_offset=0,200,400…` until `spans_truncated=false`. The trace
  header (`start_time`/`end_time`/`status`) is stable across pages.
  Blank `trace_id`/`span_id` fail with `unknown trace ''` /
  `unknown span ''`; negative `start_date`/`end_date` are rejected.
- Discovery docs are cacheable (`Cache-Control: public, max-age=300`,
  `X-Oxly-Plugin-Phase: 6`); MCP shares the 100 req/min per-IP
  limiter (429 carries `retry_after` — back off).

## Phase 5 notes (still applies)

- Pagination: `query_traces` returns `total` + echoed `limit`/`offset`
  (max 50); `query_security_alerts` returns `total` + `count` + echoed
  `limit`/`offset` (max 100).
- `get_trace` reports true total `span_count` (separate COUNT query),
  `spans` capped at 200 with `spans_truncated` flag.
- `cost_summary` validates `start_date <= end_date` and rejects
  non-integer timestamps; `interval`/`status`/`severity` validated
  case-insensitively.
- `whoami` supports DEMO_MODE keyless calls with explicit `project_id`.

## Phase 4 notes (still applies)

- Pagination: `query_traces` and `query_security_alerts` accept `offset`;
  clamp `limit` server-side (50 traces, 100 alerts).
- `get_trace` / `get_span` / `whoami` accept optional `project_id` that
  must match the key's project (same `unknown project` contract).
- MCP shares the API 100 req/min per-IP limiter; back off on 429.
- Dashboard deep links: `/traces/{trace_id}`, `/analytics` (same origin).
