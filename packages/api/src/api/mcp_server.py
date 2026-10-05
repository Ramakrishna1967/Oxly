# Copyright 2026 Oxly Contributors
# SPDX-License-Identifier: Apache-2.0

"""Oxly MCP server (Phase 2: project-scoped observability tools).

Exposes a Streamable HTTP MCP endpoint with read-only observability tools:

- ``query_traces`` / ``get_trace`` / ``get_span`` — trace & span inspection
- ``query_security_alerts`` — PII / injection / anomaly alerts
- ``cost_summary`` — per-model cost + token aggregation with timeseries
- ``whoami`` — confirm which project an API key is scoped to

Auth model (Phase 1 scope, kept in Phase 2):

- Every tool takes the project's SDK API key (``ak_...``, issued once by
  ``POST /api/v1/projects``). The key is verified against
  ``projects.api_key_hash`` with the same pbkdf2 scan as
  ``api.apikey_auth.verify_api_key`` — the MCP audience reuses the SDK
  audience, so no JWT plumbing is needed over the MCP transport.
- An optional ``project_id`` must match the key's project, otherwise the
  tool fails with ``unknown project`` (no existence oracle). When omitted
  it defaults to the key's project.
- ``DEMO_MODE=true`` allows keyless calls with an explicit ``project_id``
  (the synthetic demo identity owns all projects, mirroring
  ``api.dependencies.verify_project_ownership``).

The toolset is intentionally read-only: no project creation, deletion, or
ingestion over MCP. Those stay on the REST API where JWT auth applies.

Mounting (see ``api.main.create_app``):
    from api.mcp_server import build_mcp_server
    _mcp = build_mcp_server()
    _mcp_app = _mcp.streamable_http_app()
    app.mount("/", _mcp_app)  # keeps the endpoint at /mcp (default path)
    # host lifespan MUST enter ``_mcp.session_manager.run()``

The ``session_manager`` only exists after ``streamable_http_app()`` is
called, which is why each built server immediately builds its sub-app in
``create_app``. A fresh instance per FastAPI app is required because
``StreamableHTTPSessionManager.run()`` can only be entered once per
server instance.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, NoReturn

from passlib.hash import pbkdf2_sha256 as pwd_context

from api.config import settings
from api.db import get_database  # noqa: F401  (Phase 1 wiring point, re-exported)
from api.dependencies import (  # noqa: F401  (Phase 1 wiring point, re-exported)
    get_user_project_ids,
    verify_project_ownership,
)

logger = logging.getLogger("oxly.api.mcp")

__all__ = [
    "build_mcp_server",
    "get_database",
    "get_user_project_ids",
    "mcp",
    "mcp_app",
    "verify_project_ownership",
]

try:
    from mcp.server import MCPServer
    from mcp.server.mcpserver.exceptions import ToolError
except Exception as exc:  # pragma: no cover - import-time guard for minimal envs
    raise RuntimeError(
        "mcp package is required (pip install mcp). See packages/api/pyproject.toml."
    ) from exc

_VALID_SEVERITIES = ("low", "medium", "high", "critical")
_VALID_INTERVALS = ("hour", "day", "week")


def _fail(message: str) -> NoReturn:
    """Raise an anticipated tool failure whose text reaches the MCP client.

    The MCP SDK wraps plain exceptions (e.g. ``ValueError``) in a generic
    ``Error executing tool <name>`` message, dropping our text. Raising
    ``ToolError`` keeps the message after that prefix as an ``isError``
    result, so models see *why* a call was rejected.
    """
    raise ToolError(message)


def _require_api_key(api_key: str | None) -> str:
    """Return a stripped API key or raise a usage error (not an oracle)."""
    key = (api_key or "").strip()
    if not key:
        if settings.DEMO_MODE:
            return ""
        _fail(
            "api_key is required: pass the project key (ak_...) from "
            "POST /api/v1/projects. Set DEMO_MODE=true only for local exploration."
        )
    if not key.startswith("ak_"):
        _fail("unknown project")
    return key


async def _project_id_for_key(api_key: str) -> str:
    """Resolve an SDK API key to its project id via pbkdf2 scan.

    Mirrors ``api.apikey_auth.verify_api_key`` (offloaded to the thread
    pool so pbkdf2 does not block the event loop). Raises
    ``ValueError("unknown project")`` on any mismatch — never distinguish
    bad-key from wrong-project.
    """
    db = get_database()
    conn = await db.get_connection()
    try:
        async with conn.execute("SELECT id, api_key_hash FROM projects") as cursor:
            rows = await cursor.fetchall()
        loop = asyncio.get_running_loop()
        for row in rows:
            valid = await loop.run_in_executor(
                None, pwd_context.verify, api_key, row["api_key_hash"]
            )
            if valid:
                return row["id"]
    finally:
        await conn.close()
    _fail("unknown project")


async def _resolve_scope(api_key: str | None, project_id: str | None) -> str:
    """Return the authorized project id for this call.

    Keyed calls scope to the key's project (explicit ``project_id`` must
    match). Keyless calls are demo-only and require an explicit
    ``project_id``.
    """
    key = _require_api_key(api_key)
    if not key:
        # DEMO_MODE keyless path: demo identity owns all projects.
        if not project_id:
            _fail("project_id is required when no api_key is given")
        db = get_database()
        conn = await db.get_connection()
        try:
            if not await verify_project_ownership(conn, "demo", project_id):
                _fail("unknown project")
        finally:
            await conn.close()
        return project_id
    key_project = await _project_id_for_key(key)
    if project_id and project_id != key_project:
        _fail("unknown project")
    return project_id or key_project


def _parse_json_object(raw: Any) -> dict:
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, ValueError):
        return {}


def _span_to_dict(row: Any) -> dict:
    end_ns = row["end_time"] if row["end_time"] is not None else row["start_time"]
    return {
        "span_id": row["span_id"],
        "trace_id": row["trace_id"],
        "parent_span_id": row["parent_span_id"],
        "name": row["name"],
        "start_time": row["start_time"],
        "end_time": end_ns,
        "duration_ms": round(row["duration_ms"]) if row["duration_ms"] is not None else 0,
        "status": row["status"],
        "service_name": row["service_name"] or "default",
        "attributes": _parse_json_object(row["attributes"]),
        "events": json.loads(row["events"]) if row["events"] else [],
        "project_id": row["project_id"],
    }


def _time_bucket_expr(interval: str) -> str:
    if interval == "hour":
        return "strftime('%Y-%m-%dT%H:00:00', timestamp, 'unixepoch')"
    if interval == "week":
        # Week start = Sunday, mirroring the REST analytics route.
        return "strftime('%Y-%m-%dT00:00:00', timestamp, 'unixepoch', 'weekday 0', '-6 days')"
    return "strftime('%Y-%m-%dT00:00:00', timestamp, 'unixepoch')"


def build_mcp_server() -> MCPServer:
    """Build a fresh Oxly MCPServer with the Phase 2 read-only toolset.

    A fresh instance per FastAPI app is required because
    ``StreamableHTTPSessionManager.run()`` can only be entered once per
    server instance — sharing one module-level server across multiple
    ``create_app()`` calls (tests, reload) breaks the second lifespan.
    """
    server = MCPServer(
        name="oxly",
        title="Oxly",
        description=(
            "Chrome DevTools for AI Agents — trace, cost, and security "
            "observability. All tools are read-only and project-scoped via "
            "the project's SDK API key (ak_...)."
        ),
        instructions=(
            "Pass the project's SDK API key (ak_...) as api_key on every call. "
            "Use whoami to confirm the key's project. Read-only tools: "
            "query_traces, get_trace, get_span, query_security_alerts, cost_summary."
        ),
        version="0.1.0-alpha",
    )

    @server.tool()
    async def query_traces(
        api_key: str,
        project_id: str | None = None,
        status: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> dict:
        """List traces for the key's project, newest first, with span counts."""
        scope = await _resolve_scope(api_key, project_id)
        limit = max(1, min(limit, 50))
        offset = max(0, offset)
        filters = ["t.project_id = ?"]
        params: list[Any] = [scope]
        if status:
            filters.append("t.status = ?")
            params.append(status)
        where_sql = f"WHERE {' AND '.join(filters)}"
        # where_sql uses only static fragments; values are bound via params.
        count_sql = f"SELECT COUNT(*) FROM traces t {where_sql}"  # nosec B608
        list_sql = (  # nosec B608
            "SELECT t.trace_id, t.project_id, t.start_time, t.end_time,"
            " t.status,"
            " (SELECT COUNT(*) FROM spans s WHERE s.trace_id = t.trace_id) AS span_count"
            f" FROM traces t {where_sql} ORDER BY t.start_time DESC LIMIT ? OFFSET ?"
        )
        db = get_database()
        conn = await db.get_connection()
        try:
            async with conn.execute(count_sql, params) as cursor:
                count_row = await cursor.fetchone()
            total = count_row[0] if count_row else 0
            async with conn.execute(
                list_sql,
                params + [limit, offset],
            ) as cursor:
                rows = await cursor.fetchall()
        finally:
            await conn.close()
        items = []
        for row in rows:
            start_ns = row["start_time"]
            end_ns = row["end_time"] if row["end_time"] is not None else start_ns
            items.append(
                {
                    "trace_id": row["trace_id"],
                    "project_id": row["project_id"],
                    "start_time": start_ns,
                    "end_time": end_ns,
                    "duration_ms": (end_ns - start_ns) / 1e6,
                    "status": row["status"],
                    "span_count": row["span_count"],
                }
            )
        return {"project_id": scope, "total": total, "traces": items}

    @server.tool()
    async def get_trace(trace_id: str, api_key: str) -> dict:
        """Get one trace with all its spans ordered by start time (replay order)."""
        key = _require_api_key(api_key)
        db = get_database()
        conn = await db.get_connection()
        try:
            async with conn.execute(
                "SELECT * FROM spans WHERE trace_id = ? ORDER BY start_time ASC LIMIT 201",
                (trace_id,),
            ) as cursor:
                rows = list(await cursor.fetchall())
            if not rows:
                _fail("unknown trace")
            trace_project = rows[0]["project_id"]
            if key:
                key_project = await _project_id_for_key(key)
                if trace_project != key_project:
                    _fail("unknown trace")
            elif not await verify_project_ownership(conn, "demo", trace_project):
                _fail("unknown trace")
        finally:
            await conn.close()
        spans = [_span_to_dict(r) for r in rows[:200]]
        truncated = len(rows) > 200
        starts = [s["start_time"] for s in spans]
        ends = [s["end_time"] for s in spans]
        statuses = {s["status"] for s in spans}
        return {
            "trace_id": trace_id,
            "project_id": rows[0]["project_id"],
            "start_time": min(starts),
            "end_time": max(ends),
            "duration_ms": (max(ends) - min(starts)) / 1e6,
            "status": "ERROR" if "ERROR" in statuses else "OK",
            "span_count": len(rows),
            "spans_truncated": truncated,
            "spans": spans,
        }

    @server.tool()
    async def get_span(span_id: str, api_key: str) -> dict:
        """Get a single span by id (arguments, timing, status, events)."""
        key = _require_api_key(api_key)
        db = get_database()
        conn = await db.get_connection()
        try:
            async with conn.execute("SELECT * FROM spans WHERE span_id = ?", (span_id,)) as cursor:
                row = await cursor.fetchone()
            if row is None:
                _fail("unknown span")
            if key:
                key_project = await _project_id_for_key(key)
                if row["project_id"] != key_project:
                    _fail("unknown span")
            elif not await verify_project_ownership(conn, "demo", row["project_id"]):
                _fail("unknown span")
        finally:
            await conn.close()
        return _span_to_dict(row)

    @server.tool()
    async def query_security_alerts(
        api_key: str,
        project_id: str | None = None,
        severity: str | None = None,
        limit: int = 25,
    ) -> dict:
        """List security alerts (prompt injection, PII, anomaly), newest first."""
        scope = await _resolve_scope(api_key, project_id)
        limit = max(1, min(limit, 100))
        filters = ["project_id = ?"]
        params: list[Any] = [scope]
        if severity:
            sev = severity.strip().lower()
            if sev not in _VALID_SEVERITIES:
                _fail(f"unknown severity '{severity}': use one of {', '.join(_VALID_SEVERITIES)}")
            filters.append("severity = ?")
            params.append(sev)
        where_sql = f"WHERE {' AND '.join(filters)}"
        # where_sql is static ("project_id = ?", "severity = ?"); values bound.
        alerts_sql = (  # nosec B608
            "SELECT id, trace_id, span_id, project_id, severity,"
            " rule_name AS alert_type, message, metadata, created_at"
            f" FROM security_alerts {where_sql} ORDER BY created_at DESC LIMIT ?"
        )
        db = get_database()
        conn = await db.get_connection()
        try:
            async with conn.execute(
                alerts_sql,
                params + [limit],
            ) as cursor:
                rows = await cursor.fetchall()
        finally:
            await conn.close()
        alerts = [
            {
                "id": r["id"],
                "trace_id": r["trace_id"],
                "span_id": r["span_id"],
                "project_id": r["project_id"],
                "severity": str(r["severity"]).lower(),
                "alert_type": r["alert_type"],
                "message": r["message"],
                "metadata": _parse_json_object(r["metadata"]),
                "created_at": str(r["created_at"]),
            }
            for r in rows
        ]
        return {"project_id": scope, "count": len(alerts), "alerts": alerts}

    @server.tool()
    async def cost_summary(
        api_key: str,
        project_id: str | None = None,
        interval: str = "day",
        start_date: int | None = None,
        end_date: int | None = None,
    ) -> dict:
        """Summarise cost and token usage per model plus a timeseries.

        start_date/end_date are Unix seconds (same as GET /analytics/cost).
        """
        scope = await _resolve_scope(api_key, project_id)
        iv = (interval or "day").strip().lower()
        if iv not in _VALID_INTERVALS:
            _fail(f"unknown interval '{interval}': use one of {', '.join(_VALID_INTERVALS)}")
        clauses = ["project_id = ?"]
        params: list[Any] = [scope]
        if start_date is not None:
            clauses.append("timestamp >= ?")
            params.append(start_date)
        if end_date is not None:
            clauses.append("timestamp <= ?")
            params.append(end_date)
        where_sql = f"WHERE {' AND '.join(clauses)}"
        bucket = _time_bucket_expr(iv)
        # bucket is allowlisted, where_sql uses static fragments; values bound.
        cost_sql = (  # nosec B608
            f"SELECT {bucket} AS time_bucket, model,"
            " SUM(prompt_tokens) AS prompt_tokens,"
            " SUM(completion_tokens) AS completion_tokens,"
            " SUM(total_tokens) AS total_tokens, SUM(cost_usd) AS cost_usd"
            f" FROM cost_metrics {where_sql} GROUP BY time_bucket, model"
            " ORDER BY time_bucket ASC"
        )
        db = get_database()
        conn = await db.get_connection()
        try:
            async with conn.execute(
                cost_sql,
                params,
            ) as cursor:
                rows = await cursor.fetchall()
        finally:
            await conn.close()
        timeseries: dict[str, dict] = {}
        by_model: dict[str, dict] = {}
        total_cost = 0.0
        total_prompt = 0
        total_completion = 0
        for r in rows:
            ts = r["time_bucket"]
            model = r["model"]
            cost = float(r["cost_usd"] or 0)
            pt = int(r["prompt_tokens"] or 0)
            ct = int(r["completion_tokens"] or 0)
            bucket_row = timeseries.setdefault(
                ts,
                {
                    "timestamp": ts,
                    "total_cost_usd": 0.0,
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                },
            )
            bucket_row[model] = round(bucket_row.get(model, 0.0) + cost, 6)
            bucket_row["total_cost_usd"] = round(bucket_row["total_cost_usd"] + cost, 6)
            bucket_row["prompt_tokens"] += pt
            bucket_row["completion_tokens"] += ct
            model_row = by_model.setdefault(
                model,
                {
                    "model": model,
                    "total_cost_usd": 0.0,
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                },
            )
            model_row["total_cost_usd"] = round(model_row["total_cost_usd"] + cost, 6)
            model_row["prompt_tokens"] += pt
            model_row["completion_tokens"] += ct
            model_row["total_tokens"] += int(r["total_tokens"] or 0)
            total_cost += cost
            total_prompt += pt
            total_completion += ct
        return {
            "project_id": scope,
            "interval": iv,
            "total_cost_usd": round(total_cost, 6),
            "total_prompt_tokens": total_prompt,
            "total_completion_tokens": total_completion,
            "by_model": sorted(by_model.values(), key=lambda m: m["model"]),
            "timeseries": list(timeseries.values()),
        }

    @server.tool()
    async def whoami(api_key: str) -> dict:
        """Confirm which project an API key is scoped to (id, name, created_at)."""
        key = _require_api_key(api_key)
        if not key:
            _fail("project_id is required when no api_key is given")
        project_id = await _project_id_for_key(key)
        db = get_database()
        conn = await db.get_connection()
        try:
            async with conn.execute(
                "SELECT id, name, created_at FROM projects WHERE id = ?",
                (project_id,),
            ) as cursor:
                row = await cursor.fetchone()
        finally:
            await conn.close()
        if row is None:  # pragma: no cover - key valid but project deleted
            _fail("unknown project")
        return {
            "project_id": row["id"],
            "project_name": row["name"],
            "created_at": str(row["created_at"]),
        }

    return server


# Module-level singleton for simple imports / standalone serving.
# main.create_app() builds a FRESH instance per app (see build_mcp_server)
# so tests and reloads each get a runnable session manager.
mcp = build_mcp_server()

# Build the Starlette sub-app now so `mcp.session_manager` exists for the
# host lifespan in main.py. Default path "/mcp" + Mount("/", ...) => /mcp.
mcp_app = mcp.streamable_http_app()

logger.info("Oxly MCP initialised (Phase 2: 6 project-scoped read-only tools)")
