# Changelog

All notable changes to Oxly are documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Versioning follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- Single-process architecture: ingestion, cost/security/storage pipeline, REST, WebSocket, and dashboard static hosting folded into `packages/api` (SQLite + in-process `asyncio.Queue`); Redis/ClickHouse/collector/workers removed
- ChatGPT plugin shell (`packages/plugin/`): canonical `plugin.json` served at
  `/plugin/plugin.json` + `/.well-known/oxly-plugin.json`, MCP client config at
  `/plugin/mcp.json`, 4-skill pack (`oxly-overview`, `trace-debug`,
  `security-triage`, `cost-report`) and status UI at `/plugin/ui/`
  (prod hosts UI at `PLUGIN_UI_ORIGIN`, allow-listed via CSP
  `frame-ancestors`/`script-src`)
- Live MCP endpoint at `/mcp` (Streamable HTTP, protocol `2025-06-18`):
  `initialize` ok, `tools/list` serves the 6 project-scoped read-only tools
- MCP tools (all scoped via the project `ak_...` key, no cross-project oracle):
  `query_traces`, `get_trace` (max 200 spans, `spans_truncated` flag),
  `get_span`, `query_security_alerts`, `cost_summary`, `whoami`;
  `DEMO_MODE=true` allows keyless calls with explicit `project_id`
- `POST /v1/traces` returns 503 `Ingest pipeline not ready` when lifespan hasn't started the span queue (was unhandled 500 `AttributeError`)
- `GET /api/v1/health` returns `{api, sqlite, queue, retention}` for the dashboard System Status panel
- `DATABASE_URL` accepts Windows drive-letter URLs and bare/relative paths (resolved against CWD); parent dirs created on init
- `DEMO_MODE=true` seeds a `demo` user row so `POST /api/v1/projects` no longer 500s on the `user_projects` FK
- Dashboard Total Cost KPI reads real `/analytics/cost` timeseries instead of hardcoded mock data

### Security
- Removed hardcoded Redis password from `redis.conf`; password now injected at runtime via entrypoint script
- Fixed auth bypass: JWT with non-existent `sub` no longer falls back to demo user
- Added authentication to `GET /projects`, `GET /projects/{id}`, `GET /traces`, `GET /traces/{trace_id}`, `GET /spans/{id}`, `GET /analytics/cost`, `GET /security/alerts`
- Scoped project listing to authenticated user via `user_projects` table
- Blocked collector from accepting caller-supplied `project_id` in span payload
- Added decompressed size limit (50MB) after gzip inflate to prevent gzip bomb OOM
- Added `OPENAI_KEY_V2`, `ANTHROPIC_KEY`, `HUGGINGFACE_TOKEN` patterns to PII detector
- Added rate limiting middleware to collector endpoint
- Redis and ClickHouse ports now bind to `127.0.0.1` instead of `0.0.0.0`

### Fixed
- `get_trace_replay` endpoint now queries ClickHouse (was querying empty SQLite spans table)
- Added missing `import time` in `traces.py`
- Redis URL no longer logged with embedded password
- ClickHouse writer buffer capped at 50,000 entries to prevent OOM on CH outage
- DLQ stream now has `MAXLEN=100,000` and stores error reason per message
- `alerts.live` Redis stream now has `MAXLEN=10,000`
- CI pytest step now fails the job on test failure (was silenced with `|| echo`)

### Changed
- Docker Compose: all services use `condition: service_healthy` in `depends_on`
- Docker Compose: added ClickHouse healthcheck
- Docker Compose: added healthchecks to all three worker containers
- Pricing table updated with current models: `gpt-4o-mini`, `claude-3-5-sonnet`, `claude-3-5-haiku`, `claude-opus-4`, `claude-sonnet-4`, Gemini 1.5/2.0, embedding models

### Added
- `CONTRIBUTING.md` — contributor guide
- `mypy` type checking added to CI

## [0.1.0-alpha] - 2026-06-27

### Added
- Initial release
- `@observe` decorator for LangGraph, CrewAI, AutoGen, and custom Python agents
- Real-time trace streaming via WebSocket
- Security engine: prompt injection and PII detection
- Cost tracking per model and project
- Time Machine trace replay
- Self-hosted Docker Compose deployment
