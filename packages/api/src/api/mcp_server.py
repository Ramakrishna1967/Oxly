# Copyright 2026 Oxly Contributors
# SPDX-License-Identifier: Apache-2.0

"""Oxly MCP server (Phase 5: production-hardened project-scoped tools).

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
_VALID_STATUSES = ("OK", "ERROR")

# Phase 5: consistent pagination caps shared by list tools.
_QUERY_TRACES_MAX_LIMIT = 50
_QUERY_ALERTS_MAX_LIMIT = 100
_GET_TRACE_MAX_SPANS = 200


def _coerce_limit(value: Any, default: int, max_value: int, name: str) -> int:
    """Coerce limit/offset-style args to int with a clear tool error.

    MCP clients occasionally send numbers as strings; reject anything
    non-numeric with the same no-oracle ``unknown ...`` style error
    model instead of raising an unhandled TypeError (which surfaces as
    a generic ``Error executing tool`` without context).
    """
    if value is None:
        return default
    try:
        iv = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        _fail(f"unknown {name} '{value}': must be an integer")
    return iv


def _coerce_offset(value: Any) -> int:
    if value is None:
        return 0
    try:
        iv = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        _fail(f"unknown offset '{value}': must be an integer")
    return max(0, iv)


def _coerce_unix_ts(value: Any, name: str) -> int | None:
    if value is None:
        return None
    try:
        iv = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        _fail(f"unknown {name} '{value}': must be Unix seconds")
    return iv


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
    """Build a fresh Oxly MCPServer with the Phase 5 read-only toolset.

    Phase 5 keeps the 6 Phase-2 tools with a consistent pagination and
    validation contract:
    - ``query_traces`` / ``query_security_alerts`` return ``total`` plus
      echoed ``limit``/``offset`` (``count`` kept on alerts for compat);
      ``status``/``severity`` validated case-insensitively; non-integer
      ``limit``/``offset`` rejected with a clear tool error.
    - ``get_trace`` reports the true total span count via a separate
      COUNT query (``span_count`` is the total, ``spans`` capped at 200
      with ``spans_truncated`` flag).
    - ``cost_summary`` validates ``start_date <= end_date`` and rejects
      non-integer timestamps.
    - ``get_trace`` / ``get_span`` / ``whoami`` accept an optional
      ``project_id`` that must match the key's project (same no-oracle
      ``unknown project`` contract); ``whoami`` also supports DEMO_MODE
      keyless calls with an explicit ``project_id``.

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
        api_key: str | None = None,
        project_id: str | None = None,
        status: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> dict:
        """List traces for the key's project, newest first, with span counts."""
        scope = await _resolve_scope(api_key, project_id)
        limit = _coerce_limit(limit, 20, _QUERY_TRACES_MAX_LIMIT, "limit")
        limit = max(1, min(limit, _QUERY_TRACES_MAX_LIMIT))
        offset = _coerce_offset(offset)
        filters = ["t.project_id = ?"]
        params: list[Any] = [scope]
        if status:
            st = status.strip().upper()
            if st not in _VALID_STATUSES:
                _fail(f"unknown status '{status}': use one of {', '.join(_VALID_STATUSES)}")
            filters.append("t.status = ?")
            params.append(st)
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
        return {
            "project_id": scope,
            "total": total,
            "limit": limit,
            "offset": offset,
            "traces": items,
        }

    @server.tool()
    async def get_trace(
        trace_id: str, api_key: str | None = None, project_id: str | None = None
    ) -> dict:
        """Get one trace with all its spans ordered by start time (replay order)."""
        key = _require_api_key(api_key)
        scope: str | None = None
        if project_id is not None:
            scope = await _resolve_scope(api_key, project_id)
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
            if scope is not None:
                if trace_project != scope:
                    _fail("unknown trace")
            elif key:
                key_project = await _project_id_for_key(key)
                if trace_project != key_project:
                    _fail("unknown trace")
            elif not await verify_project_ownership(conn, "demo", trace_project):
                _fail("unknown trace")
            # Phase 5: true total span count (page is capped at 200 + probe row).
            async with conn.execute(
                "SELECT COUNT(*) FROM spans WHERE trace_id = ?",
                (trace_id,),
            ) as cursor:
                count_row = await cursor.fetchone()
            total_spans = int(count_row[0]) if count_row else len(rows)
        finally:
            await conn.close()
        spans = [_span_to_dict(r) for r in rows[:_GET_TRACE_MAX_SPANS]]
        truncated = total_spans > _GET_TRACE_MAX_SPANS
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
            "span_count": total_spans,
            "spans_truncated": truncated,
            "spans": spans,
        }

    @server.tool()
    async def get_span(
        span_id: str, api_key: str | None = None, project_id: str | None = None
    ) -> dict:
        """Get a single span by id (arguments, timing, status, events)."""
        key = _require_api_key(api_key)
        scope: str | None = None
        if project_id is not None:
            scope = await _resolve_scope(api_key, project_id)
        db = get_database()
        conn = await db.get_connection()
        try:
            async with conn.execute("SELECT * FROM spans WHERE span_id = ?", (span_id,)) as cursor:
                row = await cursor.fetchone()
            if row is None:
                _fail("unknown span")
            if scope is not None:
                if row["project_id"] != scope:
                    _fail("unknown span")
            elif key:
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
        api_key: str | None = None,
        project_id: str | None = None,
        severity: str | None = None,
        limit: int = 25,
        offset: int = 0,
    ) -> dict:
        """List security alerts (prompt injection, PII, anomaly), newest first."""
        scope = await _resolve_scope(api_key, project_id)
        limit = _coerce_limit(limit, 25, _QUERY_ALERTS_MAX_LIMIT, "limit")
        limit = max(1, min(limit, _QUERY_ALERTS_MAX_LIMIT))
        offset = _coerce_offset(offset)
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
            f" FROM security_alerts {where_sql} ORDER BY created_at DESC LIMIT ? OFFSET ?"
        )
        db = get_database()
        conn = await db.get_connection()
        try:
            async with conn.execute(
                f"SELECT COUNT(*) FROM security_alerts {where_sql}",  # nosec B608
                params,
            ) as cursor:
                count_row = await cursor.fetchone()
            total = int(count_row[0]) if count_row else 0
            async with conn.execute(
                alerts_sql,
                params + [limit, offset],
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
        return {
            "project_id": scope,
            "total": total,
            "count": len(alerts),
            "limit": limit,
            "offset": offset,
            "alerts": alerts,
        }

    @server.tool()
    async def cost_summary(
        api_key: str | None = None,
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
        start_ts = _coerce_unix_ts(start_date, "start_date")
        end_ts = _coerce_unix_ts(end_date, "end_date")
        if start_ts is not None and end_ts is not None and start_ts > end_ts:
            _fail(f"unknown date range '{start_date}..{end_date}': start_date must be <= end_date")
        clauses = ["project_id = ?"]
        params: list[Any] = [scope]
        if start_ts is not None:
            clauses.append("timestamp >= ?")
            params.append(start_ts)
        if end_ts is not None:
            clauses.append("timestamp <= ?")
            params.append(end_ts)
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
    async def whoami(api_key: str | None = None, project_id: str | None = None) -> dict:
        """Confirm which project an API key is scoped to (id, name, created_at)."""
        key = _require_api_key(api_key)
        if not key:
            # Phase 5: DEMO_MODE keyless path mirrors _resolve_scope — an
            # explicit project_id resolves against the synthetic demo owner.
            if not project_id:
                _fail("project_id is required when no api_key is given")
            db_demo = get_database()
            conn_demo = await db_demo.get_connection()
            try:
                if not await verify_project_ownership(conn_demo, "demo", project_id):
                    _fail("unknown project")
                async with conn_demo.execute(
                    "SELECT id, name, created_at FROM projects WHERE id = ?",
                    (project_id,),
                ) as cursor:
                    demo_row = await cursor.fetchone()
            finally:
                await conn_demo.close()
            if demo_row is None:
                _fail("unknown project")
            return {
                "project_id": demo_row["id"],
                "project_name": demo_row["name"],
                "created_at": str(demo_row["created_at"]),
            }
        project_id_resolved = await _project_id_for_key(key)
        if project_id is not None and project_id != project_id_resolved:
            _fail("unknown project")
        project_id = project_id_resolved
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

logger.info("Oxly MCP initialised (Phase 5: 6 project-scoped read-only tools)")
