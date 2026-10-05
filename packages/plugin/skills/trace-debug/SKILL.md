# Trace Debug (Phase 6)

Debug a failing agent run using Oxly's project-scoped, read-only MCP tools
at `/mcp` (Streamable HTTP). Auth is identical on every call: pass the
project's SDK API key (`ak_...`, from `POST /api/v1/projects`) as `api_key`.
Calls scope to the key's project; an optional `project_id` must match or the
tool fails with `unknown project`. `DEMO_MODE=true` allows keyless calls with
an explicit `project_id` for local exploration only.

## Tool chain

1. `whoami(api_key)` — confirm which project the key is scoped to.
   Start here when unsure which key you hold.
2. `query_traces(api_key, project_id?, status?, limit?=20, offset?=0)` —
   list traces newest-first with `span_count` (limit max 50).
   Use `status="ERROR"` to isolate failing runs (anything else fails with
   `unknown status '...'` listing `OK | ERROR`).
3. `get_trace(trace_id, api_key, project_id?, span_limit?=200, span_offset?=0)` —
   full trace with spans in replay order (paged; `span_count` is the true
   total, `spans_truncated=true` when pages remain — advance `span_offset`
   by `span_limit` until `spans_truncated=false`).
4. `get_span(span_id, api_key, project_id?)` — one span: timing (`start_time`/`end_time`
   are nanoseconds, `duration_ms` derived), `status`, `service_name`,
   `attributes` (model, tokens), `events`.

## Reading a trace

- `duration_ms` on the trace is `(max(end) - min(start)) / 1e6`.
- Trace `status` is `ERROR` if any span is `ERROR`, else `OK`.
- Spans arrive ordered by `start_time` — that is the replay order.
- `parent_span_id=null` marks the root; follow `parent_span_id` for the
  agent → tool → LLM → tool chain.

## Errors (no cross-project oracle)

- `unknown project` — bad `api_key` or mismatched `project_id`.
- `unknown trace` / `unknown span` — id not found *or* not in the key's
  project. Never confirm whether an id exists in another project.

## Phase 6 notes

- `get_trace` spans page via `span_limit`/`span_offset` (echoed);
  blank `trace_id` fails with `unknown trace ''`.

## Phase 5 notes (still applies)

- `query_traces` returns `total` + echoed `limit`/`offset` (max 50).
- `get_trace` `span_count` is the true total; `spans_truncated=true`
  when capped at 200.
