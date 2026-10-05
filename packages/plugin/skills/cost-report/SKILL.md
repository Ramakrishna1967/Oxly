# Cost Report (Phase 5)

Summarise spend and token usage using Oxly's project-scoped, read-only MCP
tools at `/mcp`. Auth: pass the project's SDK API key (`ak_...`) as `api_key`
on every call; optional `project_id` must match or the tool fails with
`unknown project`.

## Tool

`cost_summary(api_key, project_id?, interval?=day, start_date?, end_date?)`

- `interval` in `hour | day | week` (anything else fails with
  `unknown interval '...'`).
- `start_date`/`end_date` are Unix seconds — same convention as
  `GET /api/v1/analytics/cost`.
- Week buckets start Sunday, mirroring the REST analytics route.

## Reading the result

- Top level: `total_cost_usd` (rounded to 6dp), `total_prompt_tokens`,
  `total_completion_tokens`.
- `by_model` (sorted by model name): per-model `total_cost_usd`,
  `prompt_tokens`, `completion_tokens`, `total_tokens`.
- `timeseries`: one entry per bucket with `timestamp`, `total_cost_usd`,
  `prompt_tokens`, `completion_tokens`, plus one key per model holding that
  bucket's cost for the model.

## Typical flow

`cost_summary(interval=week)` for the status update → per-model rows for the
spend breakdown → `timeseries` for the chart. Narrow with `start_date` /
`end_date` when the stakeholder asks about a specific window.

## Phase 5 notes

- `start_date`/`end_date` are Unix seconds; `start_date <= end_date`
  enforced, non-integer timestamps rejected with a clear tool error.
