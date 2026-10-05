# Oxly Plugin — Phase 4

Plugin shell + live `/mcp` endpoint with 6 project-scoped read-only tools:
`query_traces`, `get_trace`, `get_span`, `query_security_alerts`,
`cost_summary`, `whoami` (all scoped via the project's `ak_...` key),
plus a 4-skill pack, a functional status UI, and submission-ready manifests.

## Layout

```
packages/plugin/
  plugin.json            # canonical manifest (also served at /plugin/plugin.json, /.well-known/oxly-plugin.json + legacy /.well-known/ai-plugin.json)
  mcp.json               # local MCP client config (Inspector -> http://localhost:8000/mcp)
  openapi.json           # minimal discovery doc (served at /plugin/openapi.json)
  skills/
    oxly-overview/       # tool index + auth + error model (SKILL.md + skill.json)
    trace-debug/         # failing-run workflow (SKILL.md + skill.json)
    security-triage/     # alert queue workflow (SKILL.md + skill.json)
    cost-report/         # spend workflow (SKILL.md + skill.json)
  ui/                    # status UI (index.html, app.js, styles.css)
                         # dev: served at /plugin/ui/ ; prod: stable HTTPS origin via PLUGIN_UI_ORIGIN
```

## Stable UI origin + CSP

- Env: `PLUGIN_UI_ORIGIN` (default `https://plugin.oxly.sh`).
- The API sets `Content-Security-Policy` with `frame-ancestors 'self' <origin>` and
  `script-src 'self' <origin>` so only that origin may frame / script the plugin UI.
- Host `packages/plugin/ui/` contents at that origin in production (any static host).
  Local dev does not need HTTPS — `/plugin/ui/` is served by the API itself.

## Phase 4 deltas (vs Phase 3)

- Manifests: `plugin.json` phase 4 with `legal`, `openapi`, `rateLimits` notes;
  legacy `/.well-known/ai-plugin.json` alias + `/plugin/openapi.json` served.
- MCP consistency: `query_traces(status)` validates `OK | ERROR`;
  `query_security_alerts` gains `offset`; `get_trace` / `get_span` / `whoami`
  accept optional `project_id` (must match, else `unknown project`).
- UI: live MCP `initialize` reachability, OpenAPI + manifest status,
  dashboard deep links (`/traces/{trace_id}`, `/analytics`).
- Skills: phase 4 bump + pagination / rate-limit / deep-link notes.

## Verify Phase 4

1. `uvicorn api.main:app --port 8000` (from `packages/api/src`, or equivalent)
2. MCP Inspector -> `http://localhost:8000/mcp` -> `initialize` ok, `tools/list` -> 6 tools
3. `GET /plugin/plugin.json`, `GET /.well-known/oxly-plugin.json`, `GET /.well-known/ai-plugin.json`, `GET /plugin/openapi.json`, `GET /plugin/ui/` -> 200
4. `pytest packages/api/tests/test_mcp_phase4.py` — phase 4 shell + consistency
5. `pytest packages/api/tests/test_mcp_phase2.py packages/api/tests/test_mcp_phase3.py` — tools/scoping unchanged
