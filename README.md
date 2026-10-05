<div align="center">

<br>

# Oxly

### The open-source observability platform for AI agents

<br>

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg?style=flat-square)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10+-3776AB.svg?style=flat-square&logo=python&logoColor=white)](https://python.org)
[![TypeScript](https://img.shields.io/badge/TypeScript-5.9-3178C6.svg?style=flat-square&logo=typescript&logoColor=white)](https://typescriptlang.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688.svg?style=flat-square&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![React 19](https://img.shields.io/badge/React-19-61DAFB.svg?style=flat-square&logo=react&logoColor=black)](https://react.dev)
[![SQLite](https://img.shields.io/badge/SQLite-3-003B57.svg?style=flat-square&logo=sqlite&logoColor=white)](https://sqlite.org)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg?style=flat-square)](https://github.com/oxly/oxly/pulls)

Oxly gives you **real-time tracing, security analysis, cost tracking, and Time Machine replay** for AI agents — without changing how you build them.

Works with **LangGraph** &nbsp;&middot;&nbsp; **CrewAI** &nbsp;&middot;&nbsp; **AutoGen** &nbsp;&middot;&nbsp; **Custom Python**

<br>

[Get Started](#get-started) &nbsp;&bull;&nbsp;
[Why Oxly](#why-oxly) &nbsp;&bull;&nbsp;
[Features](#features) &nbsp;&bull;&nbsp;
[Architecture](#architecture) &nbsp;&bull;&nbsp;
[Running It](#running-it) &nbsp;&bull;&nbsp;
[Contributing](#contributing)

<br>

---

</div>

<br>

## Get Started

**1. Run the API** (see [Running It](#running-it) for the full walkthrough — no Docker, Redis, or ClickHouse required)

```bash
git clone https://github.com/ramakrishna1967/Oxly
cd Oxly
pip install -e packages/api
export JWT_SECRET_KEY=$(openssl rand -hex 32)
export DATABASE_URL="sqlite+aiosqlite:///C:/Users/you/oxly.db"   # Windows; see Running It for Linux/Mac
uvicorn api.main:app --host 0.0.0.0 --port 8000
```

**2. Instrument your agent with one decorator**

```python
from oxly import observe, init

init(
    collector_url="http://localhost:8000",  # your running Oxly API
    api_key="ak_your_project_key",          # from POST /api/v1/projects — see Running It
)

@observe
def research_agent(query: str) -> str:
    context = search_tool(query)
    return llm.chat(f"Answer based on: {context}")

@observe(name="planning.step")
async def async_agent(objective: str) -> list[str]:
    return await llm.achat(f"Break this into steps: {objective}")
```

Every call now produces a full trace — arguments, return values, timing, exceptions, token counts, and cost — visible instantly in the dashboard at `http://localhost:8000`.

> **Zero-interference guarantee.** The `@observe` decorator will never crash your application. If Oxly encounters an internal error, your function executes normally and the SDK fails silently.

<br>

## Why Oxly

Most observability tools are designed for web services. AI agents have fundamentally different requirements:

| Challenge | Web Services | AI Agents | Oxly |
|-----------|:---:|:---:|---|
| **Request duration** | Milliseconds | Minutes to hours | Durable traces with offline fallback |
| **Cost** | Fixed infra | Variable per-token billing | Per-model cost tracking with timeseries |
| **Security** | Known attack vectors | Prompt injection, PII leakage | Real-time detection engine |
| **Debugging** | Deterministic stack traces | Non-deterministic LLM behavior | Time Machine — step-by-step replay |
| **Data sensitivity** | Headers and bodies | Full conversation text | Auto-PII sanitization before export |
| **Call structure** | Flat request/response | Deep nested trees (agent → tool → LLM → tool) | Automatic parent-child span linking |

<br>

## Features

<table>
<tr>
<td width="50%" valign="top">

**Real-time Tracing**

Automatically capture every LLM call, tool invocation, and function execution as a structured span. Supports sync, async, and deeply nested call chains with zero manual instrumentation beyond `@observe`.

</td>
<td width="50%" valign="top">

**Time Machine Replay**

Step through any past agent execution span-by-span. See exactly which LLM calls were made, what each tool returned, and which decision path was taken — without reproducing the failure.

</td>
</tr>
<tr>
<td width="50%" valign="top">

**Security Engine**

Detect prompt injection, PII leakage, and anomalous behavior in real time. Alerts are written to SQLite and pushed over WebSocket to the dashboard within seconds of occurring.

</td>
<td width="50%" valign="top">

**Automatic PII Sanitization**

Every span is scrubbed before export. SSNs, credit card numbers, emails, phone numbers, AWS keys, OpenAI keys, and generic API tokens are detected and redacted automatically — no config required.

</td>
</tr>
<tr>
<td width="50%" valign="top">

**Cost Analytics**

Per-model token counting and USD cost calculation with timeseries charts. Track spend across GPT-4, Claude, Gemini, and any other provider — broken down by hour, day, or week.

</td>
<td width="50%" valign="top">

**Framework Auto-Detection**

Native hooks for **LangGraph**, **CrewAI**, and **AutoGen**. Oxly detects your framework at import time and instruments the right entry points automatically.

</td>
</tr>
<tr>
<td width="50%" valign="top">

**Offline Resilience**

If the API is unreachable, spans are written to a local SQLite store on the SDK side and automatically retried every ~30 seconds once connectivity is restored. No data is lost due to network failures.

</td>
<td width="50%" valign="top">

**Production Hardened**

Non-root container user, JWT auth with brute-force lockout, SHA-256 API key caching, 5MB payload limits, CORS allowlists, per-IP rate limiting, and Bandit security scanning in CI.

</td>
</tr>
</table>

<br>

## Architecture

Oxly runs as **one FastAPI process**. There is no message broker, no separate ingestion service, and no analytical database — everything downstream of your agent lives inside `packages/api`.

```
  Your Application
  ( @observe decorator )
         |
         |  HTTPS POST /v1/traces
         |  (gzip-compressed, exponential backoff, local SQLite fallback if unreachable)
         v
  +--------------------------------------------------------------+
  |                 Oxly API  —  one process                |
  |                                                                |
  |   routes/ingest.py                                            |
  |     validates the API key, enforces the 5MB payload limit,    |
  |     puts each span onto an in-process asyncio.Queue           |
  |            |                                                  |
  |            v                                                  |
  |   span_consumer.py  (drains the queue)                        |
  |     - calculates per-model token cost                         |
  |     - runs prompt-injection / PII / anomaly rules              |
  |     - writes traces, spans, cost_metrics, security_alerts      |
  |            |                              |                   |
  |            v                              v                   |
  |     SQLite (oxly.db)         routes/ws.py broadcast()    |
  |                                     — pushes new alerts to     |
  |   retention.py                       every connected           |
  |     daily sweep, deletes              WebSocket client         |
  |     spans older than 90 days                                  |
  |                                                                |
  |   StaticFiles mount + SPA fallback                             |
  |     serves the dashboard's built dist/ from this same process  |
  +--------------------------------------------------------------+
                               |
                               v
                Dashboard (React) — same origin, same port
                Traces | Analytics | Security | Time Machine
```

**Components at a glance:**

| Package | Stack | Role |
|---------|-------|------|
| `packages/sdk-python` | Python 3.10+, Pydantic v2 | `@observe` decorator, context propagation, PII scrubber, batching, HTTP transport with local SQLite fallback |
| `packages/api` | FastAPI, aiosqlite | Ingestion, in-process cost/security/storage processing, REST API, WebSocket live feeds, JWT auth, trace replay, dashboard static hosting |
| `packages/dashboard` | React 19, TypeScript, Vite, Recharts | Trace viewer, analytics, Time Machine, security alerts — built to a static `dist/` and served by `packages/api` |
| `deploy/` | Docker Compose | Optional single-container deployment of `packages/api` (with the dashboard's `dist/` baked in) |

<br>

## Project Structure

```
.
├── packages/
│   ├── sdk-python/            # Python SDK (pip install oxly)
│   │   └── src/oxly/
│   │       ├── decorator.py   # @observe
│   │       ├── tracer.py      # Span / Tracer
│   │       ├── exporter.py    # batching, retry, local SQLite fallback
│   │       ├── sanitizer.py   # PII scrubbing
│   │       ├── local_store.py # offline span storage
│   │       ├── frameworks/    # langgraph.py, crewai.py, autogen.py
│   │       └── _internal/     # transport.py, buffer.py, clock.py
│   │
│   ├── api/                   # The API — ingestion, processing, REST, WS, dashboard hosting
│   │   └── src/api/
│   │       ├── main.py            # app factory, lifespan, StaticFiles mount
│   │       ├── span_consumer.py   # in-process cost/security/storage pipeline
│   │       ├── retention.py       # daily 90-day span sweep
│   │       ├── db.py              # aiosqlite connection manager + schema
│   │       ├── cost.py            # per-model pricing
│   │       ├── apikey_auth.py     # SDK-facing API key auth
│   │       ├── rules/             # injection.py, pii.py, anomaly.py
│   │       └── routes/            # ingest, traces, spans, security, analytics,
│   │                               # projects, auth, health, ws
│   │
│   └── dashboard/             # React frontend
│       └── src/
│           ├── pages/          # Dashboard, TraceView, Analytics, Security, Metrics, Settings
│           ├── hooks/          # useTraces
│           ├── components/
│           └── lib/            # api.ts, types.ts, utils.ts
│
├── deploy/
│   ├── docker-compose.yml     # single `api` service
│   └── .env.example
│
├── tests/integration/         # cross-package integration tests
├── examples/                  # example agent scripts
├── scripts/                   # benchmark/demo/seed scripts
├── .github/workflows/         # CI — lint, typecheck, bandit, build
├── LICENSE
└── README.md
```

<br>

## Running It

No Docker, Redis, or ClickHouse required — the API is a single process backed by a SQLite file.

**1. Install and configure**

```bash
git clone https://github.com/ramakrishna1967/Oxly
cd Oxly
pip install -e packages/api
```

Set the required environment variables. `DATABASE_URL` must point at an **absolute path**. The scheme prefix is `sqlite+aiosqlite:///` followed directly by your absolute path — on Windows that means no extra leading slash (the drive letter is the anchor); on Linux/Mac your path already starts with `/`, so it ends up looking like four slashes total:

```bash
# Windows
export DATABASE_URL="sqlite+aiosqlite:///C:/Users/you/oxly.db"

# Linux / macOS
export DATABASE_URL="sqlite+aiosqlite:////home/you/oxly.db"
```

```bash
export JWT_SECRET_KEY=$(openssl rand -hex 32)
export ENVIRONMENT=development
```

**2. Start it**

```bash
uvicorn api.main:app --host 0.0.0.0 --port 8000
```

The API, the dashboard (once built — see below), the WebSocket feed, and the ingestion endpoint are all now served from `http://localhost:8000`.

**3. Serve the dashboard (optional)**

`packages/api`'s `StaticFiles` mount only activates if a built dashboard is present. To build one:

```bash
cd packages/dashboard
npm install && npm run build
export DASHBOARD_DIST_DIR="$(pwd)/dist"   # set before starting uvicorn
```

Without this, the API still works fully — you just won't have the dashboard UI at `/`.

**4. Create a project and get an API key**

```bash
curl -X POST http://localhost:8000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email": "you@example.com", "password": "A-Strong-Password-12+"}'

TOKEN=$(curl -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email": "you@example.com", "password": "A-Strong-Password-12+"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

curl -X POST http://localhost:8000/api/v1/projects \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"name": "my-first-project"}'
```

The response includes an `api_key` (shown once) — this is what you pass to `oxly.init(api_key=...)`.

> Prefer to skip auth entirely for local exploration? Set `DEMO_MODE=true` — every read endpoint (traces, security alerts, analytics) works without a token, and project creation works via the seeded `demo` user.

**5. Point the SDK at it**

```python
import oxly

oxly.init(collector_url="http://localhost:8000", api_key="ak_...")

@oxly.observe
def my_agent(query: str) -> str:
    return f"answer to: {query}"

my_agent("hello")
```

This entire flow — register, login, create project, send a trace, and read it back via `GET /api/v1/traces` — was verified end-to-end while writing this doc.

<br>

## ChatGPT / MCP Plugin (Phase 5)

Oxly exposes 6 project-scoped, read-only MCP tools at `/mcp` (Streamable HTTP):

`query_traces` · `get_trace` · `get_span` · `query_security_alerts` · `cost_summary` · `whoami`

- **Auth:** pass the project `ak_...` key (from `POST /api/v1/projects`) as `api_key` on every call. An optional `project_id` must match the key's project, otherwise the tool fails with `unknown project` (bad key and wrong project are indistinguishable by design). `DEMO_MODE=true` allows keyless calls with an explicit `project_id` for local exploration.
- **Try it:** MCP Inspector → `http://localhost:8000/mcp` → `initialize` ok → `tools/list` → 6 tools. Manifest at `/plugin/plugin.json` (also `/.well-known/oxly-plugin.json` + legacy `/.well-known/ai-plugin.json`), discovery doc at `/plugin/openapi.json`, client config at `/plugin/mcp.json`, status UI at `/plugin/ui/`.
- **Skills:** `oxly-overview` (tool index) · `trace-debug` (failing-run workflow) · `security-triage` (alert queue) · `cost-report` (spend + timeseries) under `packages/plugin/skills/`.
- **Phase 5 consistency:** list tools return `total` + echoed `limit`/`offset` (`count` kept on alerts); `get_trace` reports true total `span_count` (200-span cap + `spans_truncated`); `cost_summary` validates `start_date <= end_date`; `status`/`severity`/`interval` validated case-insensitively; non-integer `limit`/`offset`/timestamps rejected with clear tool errors. Superset of Phase 4 (`query_traces(status OK|ERROR)`, alerts `limit`/`offset` paging, optional matching `project_id` on point lookups).
- **Verify:** `pytest packages/api/tests/test_mcp_phase5.py` (consistency + discovery) and `test_mcp_phase4.py` (forward-compatible shell + consistency) and `test_mcp_phase3.py` (skill pack + manifest + UI) and `test_mcp_phase2.py` (scoping + cross-project isolation) and `test_mcp_phase0.py` (shell + endpoint). See `packages/plugin/README.md` and `packages/plugin/skills/oxly-overview/SKILL.md` for tool reference.

<br>

## Environment Variables

**API** (`packages/api`, read by `api/config.py` unless noted):

| Variable | Default | Description |
|----------|---------|--------------|
| `JWT_SECRET_KEY` | *(none — auto-generated with a warning in `development`, fatal in any other environment)* | JWT signing secret |
| `ALGORITHM` | `HS256` | JWT signing algorithm |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `1440` (24h) | JWT expiry |
| `DATABASE_URL` | `oxly.db` (resolves to `/app/oxly.db` — see caveat above) | SQLite connection string — must be an absolute path (`sqlite+aiosqlite:///C:/...` on Windows, `sqlite+aiosqlite:////...` on Linux/Mac) |
| `ENVIRONMENT` | `development` | `development` relaxes the secret-key gate; anything else enforces it |
| `DEMO_MODE` | `false` | Bypasses JWT auth on read endpoints with a synthetic `demo` user |
| `CORS_ORIGINS` | `http://localhost,http://127.0.0.1,http://localhost:5173,http://127.0.0.1:5173,http://localhost:3000,http://localhost:80` | Comma-separated allowed origins (read directly via `os.getenv`, not part of `Settings`) |
| `DASHBOARD_DIST_DIR` | `<api package dir>/static` | Where `main.py` looks for the dashboard's built `dist/` |
| `PLUGIN_UI_ORIGIN` | `https://plugin.oxly.sh` | Stable HTTPS origin hosting `packages/plugin/ui/`; allow-listed via CSP `frame-ancestors`/`script-src` |

**SDK** (`packages/sdk-python`, read by `oxly/config.py`, all prefixed `OXLY_`):

| Variable | Default | Description |
|----------|---------|--------------|
| `OXLY_COLLECTOR_URL` | `http://localhost:8000` | Base URL of your running Oxly API |
| `OXLY_API_KEY` | *(empty)* | API key from `POST /api/v1/projects` |
| `OXLY_ENABLED` | `true` | Master on/off switch |
| `OXLY_BATCH_SIZE` | `64` | Max spans per export batch |
| `OXLY_EXPORT_INTERVAL` | `5000` | Max ms before a partial batch flushes |
| `OXLY_MAX_QUEUE_SIZE` | `2048` | In-memory ring buffer capacity |
| `OXLY_LOG_LEVEL` | `INFO` | Python logging level |
| `OXLY_DEBUG` | `false` | Verbose stderr logging |
| `OXLY_SERVICE_NAME` | `default` | Tagged on all spans |
| `OXLY_PROJECT_ID` | `default` | Cosmetic — the API derives the real project from your API key regardless |

**Dashboard** (`packages/dashboard`, build-time Vite vars):

| Variable | Default | Description |
|----------|---------|--------------|
| `VITE_API_URL` | *(empty — same-origin)* | Only needed for standalone `npm run dev` against a separately running API |
| `VITE_WS_URL` | derived from `window.location` | Same — only needed for standalone dev |

<br>

## Migration History

Oxly originally ran as an 8-container stack: Redis Streams for ingestion, ClickHouse for storage, a standalone Collector service, three independent Workers (ClickHouse writer, security engine, cost calculator), the API, the Dashboard, and an Nginx gateway in front of all of it.

That infrastructure has since been folded entirely into `packages/api`:

- Ingestion moved from a Redis Stream to an in-process `asyncio.Queue`.
- Cost calculation, security rules, and storage moved from three separate Worker processes into one in-process consumer (`span_consumer.py`).
- Storage moved from ClickHouse to SQLite (`aiosqlite`).
- Live alert delivery moved from a Redis Stream consumer to a direct WebSocket broadcast call.
- The dashboard is now built to a static `dist/` and served by the API itself via `StaticFiles`, rather than by its own Nginx container behind a gateway.
- A daily in-process sweep replaces ClickHouse's TTL for the 90-day span retention policy.

The result is the single-process architecture described above — the same features, running as one FastAPI app with no external infrastructure dependency.

<br>

## Known Follow-ups

Previously tracked here, now resolved (verified 2026-10-05, API tests 24 passed, SDK 63 passed):

- ~~**`DATABASE_URL` non-Docker default is broken.**~~ Fixed — `api/db.py` now resolves bare/relative values against `Path.cwd()` (container behavior unchanged since CWD is `/app`), handles Windows drive-letter URLs (`sqlite+aiosqlite:///C:/...`), and creates parent dirs on init.
- ~~**`DEMO_MODE` + create-project 500s.**~~ Fixed — `init_db()` seeds a `demo` user row when `DEMO_MODE=true`, so `POST /api/v1/projects` no longer fails its `user_projects` FK insert.
- ~~**Dashboard system-health widget stale.**~~ Fixed — `GET /api/v1/health` now returns `{api, sqlite, queue, retention}` and `Dashboard.tsx` / `lib/types.ts` render those four tiles.

No open follow-ups at this time.

<br>

## Security

| Layer | Details |
|-------|---------|
| **Containers** | The API image runs as non-root `appuser` |
| **Secrets** | Environment variables only; `.env` is gitignored |
| **Auth** | JWT (24h expiry) + pbkdf2_sha256 password hashing |
| **API Keys** | SHA-256 cache on hot path; pbkdf2 on first use |
| **Brute force** | 5 failed logins per email per 15 minutes triggers lockout |
| **Rate limiting** | 100 requests/minute per IP |
| **CORS** | Explicit allowlist via `CORS_ORIGINS` |
| **Payloads** | 5MB limit enforced on actual request body |
| **CI scanning** | Bandit static analysis on every PR |
| **PII** | Scrubbed from every span before storage |

To report a security vulnerability, please open a [GitHub Security Advisory](https://github.com/oxly/oxly/security/advisories/new) rather than a public issue.

<br>

## Contributing

Contributions are welcome — from bug reports and docs improvements to new framework integrations and features.

**Development setup:**

```bash
# API
cd packages/api
pip install -e ".[dev]"
pytest        # Run tests
uvicorn api.main:app --reload   # Run locally, see "Running It" above for env vars

# Python SDK
cd packages/sdk-python
pip install -e ".[dev]"
make test      # Run tests with coverage
make lint      # Ruff linter

# Dashboard
cd packages/dashboard
npm install
npm run dev    # Dev server with hot reload
npm run build  # Production build + TypeScript check

# Or run the API in a single container (dashboard dist baked in at build time)
cd deploy
docker compose up -d
```

Please open an issue before starting on significant changes so we can discuss the approach together.

<br>

## License

Apache 2.0 — see [LICENSE](LICENSE) for details.

Copyright 2026 Oxly Contributors.

---

<div align="center">
<br>
If Oxly is useful to you, please consider giving it a star. It helps others find the project.
<br><br>
</div>
