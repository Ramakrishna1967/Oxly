# Oxly Plugin — Phase 0 scaffold

Plugin shell + live `/mcp` endpoint. Empty tool list is expected.

## Layout

```
packages/plugin/
  plugin.json            # canonical manifest (also served at /plugin/plugin.json, /.well-known/oxly-plugin.json)
  mcp.json               # local MCP client config (Inspector -> http://localhost:8000/mcp)
  skills/
    oxly-overview/       # placeholder skill (SKILL.md + skill.json)
  ui/                    # placeholder UI (index.html, app.js, styles.css)
                         # dev: served at /plugin/ui/ ; prod: stable HTTPS origin via PLUGIN_UI_ORIGIN
```

## Stable UI origin + CSP

- Env: `PLUGIN_UI_ORIGIN` (default `https://plugin.oxly.sh`).
- The API sets `Content-Security-Policy` with `frame-ancestors 'self' <origin>` and
  `script-src 'self' <origin>` so only that origin may frame / script the plugin UI.
- Host `packages/plugin/ui/` contents at that origin in production (any static host).
  Local dev does not need HTTPS — `/plugin/ui/` is served by the API itself.

## Verify Phase 0

1. `uvicorn api.main:app --port 8000` (from `packages/api/src`, or equivalent)
2. MCP Inspector -> `http://localhost:8000/mcp` -> `initialize` ok, `tools/list` -> `[]`
3. `GET /plugin/plugin.json`, `GET /.well-known/oxly-plugin.json`, `GET /plugin/ui/` -> 200
