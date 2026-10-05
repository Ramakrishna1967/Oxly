# Copyright 2026 Oxly Contributors
# SPDX-License-Identifier: Apache-2.0

"""Phase 6 tests: operational readiness + large-trace paging + submission polish.

Done when: plugin.json declares phase 6 (superset of phase 5) with
caching + submission blocks, openapi.json documents span paging +
rate-limit + cache-header schemas, discovery docs are served with
Cache-Control + X-Oxly-Plugin-Phase, get_trace pages spans via
span_limit/span_offset with a stable header and true total span_count,
blank trace_id/span_id are rejected, cost_summary rejects negative
timestamps, and the Phase 5 contract still holds.
"""

from __future__ import annotations

import itertools
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest
from fastapi.testclient import TestClient
from passlib.hash import pbkdf2_sha256 as pwd_context

PLUGIN_DIR = Path(__file__).resolve().parents[3] / "packages" / "plugin"
EXPECTED_TOOLS = {
    "query_traces",
    "get_trace",
    "get_span",
    "query_security_alerts",
    "cost_summary",
    "whoami",
}
EXPECTED_SKILLS = ("oxly-overview", "trace-debug", "security-triage", "cost-report")

KEY_1 = "ak_phase6testkey00000000000000001"
KEY_2 = "ak_phase6testkey00000000000000002"

_call_ids = itertools.count(600)


def _client():
    from api.main import create_app

    return TestClient(create_app(), base_url="http://localhost:8000")


def _parse_result(response_text: str) -> dict:
    payloads = [
        json.loads(line[len("data:") :].strip())
        for line in response_text.splitlines()
        if line.strip().startswith("data:")
    ]
    assert payloads, f"no SSE data payload in: {response_text[:500]}"
    return payloads[-1]


@pytest.fixture()
def seeded_client(tmp_path, monkeypatch):
    import api.db as db_module
    from api.db import Database

    db = Database(str(tmp_path / "mcp_phase6.db"))
    import asyncio

    asyncio.run(db.init_db())

    async def seed():
        conn = await db.get_connection()
        now_ns = time.time_ns()
        now_s = now_ns // 1_000_000_000
        await conn.execute(
            "INSERT INTO users (id, email, hashed_password, is_active) VALUES (?, ?, ?, 1)",
            ("u1", "phase6@oxly.dev", pwd_context.hash("irrelevant")),
        )
        await conn.execute(
            "INSERT INTO projects (id, name, api_key_hash) VALUES (?, ?, ?)",
            ("p1", "Phase Six One", pwd_context.hash(KEY_1)),
        )
        await conn.execute(
            "INSERT INTO projects (id, name, api_key_hash) VALUES (?, ?, ?)",
            ("p2", "Phase Six Two", pwd_context.hash(KEY_2)),
        )
        await conn.execute(
            "INSERT INTO user_projects (user_id, project_id, role) VALUES ('u1', 'p1', 'owner')"
        )
        await conn.execute(
            "INSERT INTO user_projects (user_id, project_id, role) VALUES ('u1', 'p2', 'owner')"
        )
        await conn.execute(
            "INSERT INTO traces (trace_id, project_id, start_time, end_time, status)"
            " VALUES ('trace-a', 'p1', ?, ?, 'OK')",
            (now_ns - 3_600_000_000_000, now_ns - 3_598_000_000_000),
        )
        await conn.execute(
            """INSERT INTO spans
               (span_id, trace_id, parent_span_id, project_id, name,
                start_time, end_time, duration_ms, status, service_name,
                attributes, events)
               VALUES ('span-a1', 'trace-a', NULL, 'p1', 'root', ?, ?, ?, 'OK', 'test', '{}', '[]')""",
            (
                now_ns - 3_600_000_000_000,
                now_ns - 3_598_000_000_000,
                2_000,
            ),
        )
        # Multi-span trace for paging: 5 spans, one ERROR so the header
        # status is stable across pages.
        await conn.execute(
            "INSERT INTO traces (trace_id, project_id, start_time, end_time, status)"
            " VALUES ('trace-big', 'p1', ?, ?, 'ERROR')",
            (now_ns - 1_800_000_000_000, now_ns - 1_799_000_000_000),
        )
        for i in range(5):
            st = now_ns - 1_800_000_000_000 + i * 100_000_000
            et = st + 50_000_000
            status = "ERROR" if i == 3 else "OK"
            await conn.execute(
                """INSERT INTO spans
                   (span_id, trace_id, parent_span_id, project_id, name,
                    start_time, end_time, duration_ms, status, service_name,
                    attributes, events)
                   VALUES (?, 'trace-big', NULL, 'p1', ?, ?, ?, ?, ?, 'test', '{}', '[]')""",
                (f"span-big-{i}", f"step-{i}", st, et, 50, status),
            )
        await conn.execute(
            """INSERT INTO security_alerts
               (id, trace_id, span_id, project_id, severity, rule_name, message)
               VALUES ('al-1', 'trace-big', 'span-big-3', 'p1', 'high', 'prompt_injection', 'suspect')"""
        )
        await conn.execute(
            """INSERT INTO cost_metrics
               (project_id, model, timestamp, prompt_tokens, completion_tokens,
                total_tokens, cost_usd)
               VALUES ('p1', 'gpt-4', ?, 100, 50, 150, 0.01)""",
            (now_s - 86_400,),
        )
        await conn.commit()
        await conn.close()

    import asyncio as _asyncio

    _asyncio.run(seed())
    monkeypatch.setattr(db_module, "_db", db)

    from api.main import create_app

    with TestClient(create_app(), base_url="http://localhost:8000") as client:
        yield client


@pytest.fixture()
def mcp_session(seeded_client):
    c = seeded_client
    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    init = c.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "phase6-test", "version": "0.0"},
            },
        },
        headers=headers,
    )
    assert init.status_code == 200, init.text[:500]
    session_id = init.headers.get("mcp-session-id")
    assert session_id
    authed = {**headers, "mcp-session-id": session_id}
    c.post(
        "/mcp",
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        headers=authed,
    )
    return c, authed


def _call(session, name: str, arguments: dict) -> dict:
    c, authed = session
    call_id = next(_call_ids)
    r = c.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": call_id,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
        headers=authed,
    )
    assert r.status_code == 200, r.text[:500]
    return _parse_result(r.text)


def _ok(payload: dict) -> dict:
    assert "result" in payload, f"expected result, got: {json.dumps(payload)[:500]}"
    result = payload["result"]
    assert not result.get("isError"), f"tool errored: {json.dumps(payload)[:500]}"
    texts = [b.get("text", "") for b in result.get("content", []) if isinstance(b, dict)]
    assert texts
    return json.loads("\n".join(texts))


def _err(payload: dict) -> str:
    if "error" in payload:
        return json.dumps(payload["error"])
    result = payload.get("result", {})
    assert result.get("isError"), f"expected tool error, got: {json.dumps(payload)[:500]}"
    return json.dumps(result.get("content", []))


def test_manifest_is_phase6():
    manifest = json.loads((PLUGIN_DIR / "plugin.json").read_text())
    assert manifest["phase"] == 6
    assert manifest["openapi"] == "/plugin/openapi.json"
    assert manifest["skills"] == list(EXPECTED_SKILLS)
    assert "caching" in manifest and "submission" in manifest
    for skill in EXPECTED_SKILLS:
        meta = json.loads((PLUGIN_DIR / "skills" / skill / "skill.json").read_text())
        assert meta["phase"] >= 6, f"{skill} not bumped to phase 6"


def test_openapi_documents_phase6():
    openapi = json.loads((PLUGIN_DIR / "openapi.json").read_text())
    assert openapi["openapi"].startswith("3.")
    assert "/mcp" in openapi["paths"]
    schemas = openapi.get("components", {}).get("schemas", {})
    for name in (
        "QueryTracesArgs",
        "QueryAlertsArgs",
        "GetTraceArgs",
        "GetSpanArgs",
        "CostSummaryArgs",
        "ErrorModel",
        "Pagination",
        "SpanPagination",
        "RateLimit",
        "CacheHeaders",
    ):
        assert name in schemas, f"openapi missing schema: {name}"
    get_trace_args = json.dumps(schemas["GetTraceArgs"])
    assert "span_limit" in get_trace_args and "span_offset" in get_trace_args
    blob = json.dumps(openapi)
    for tool in EXPECTED_TOOLS:
        assert tool in blob, f"openapi missing tool: {tool}"
    assert "429" in blob, "openapi missing 429 rate-limit documentation"


def test_shell_serves_phase6_endpoints_with_cache_headers():
    with _client() as c:
        for path in (
            "/plugin/plugin.json",
            "/plugin/mcp.json",
            "/plugin/openapi.json",
            "/.well-known/oxly-plugin.json",
            "/.well-known/ai-plugin.json",
            "/plugin/ui/",
        ):
            r = c.get(path)
            assert r.status_code == 200, f"{path} -> {r.status_code}"
        for path in (
            "/plugin/plugin.json",
            "/plugin/mcp.json",
            "/plugin/openapi.json",
            "/.well-known/oxly-plugin.json",
            "/.well-known/ai-plugin.json",
        ):
            r = c.get(path)
            assert r.headers.get("cache-control") == "public, max-age=300", path
            assert r.headers.get("x-oxly-plugin-phase") == "6", path
        ui = c.get("/plugin/ui/").text
        for marker in (
            "Phase 6",
            "Phase 5",
            "Phase 4",
            "MCP live",
            "Dashboard",
            "Status",
            "trace-debug",
            "cost-report",
        ):
            assert marker in ui, f"UI missing marker: {marker}"
        for tool in EXPECTED_TOOLS:
            assert tool in ui, f"UI missing tool: {tool}"


def test_get_trace_default_page_keeps_phase5_shape(mcp_session):
    body = _ok(_call(mcp_session, "get_trace", {"trace_id": "trace-a", "api_key": KEY_1}))
    assert body["span_count"] == 1
    assert body["spans_truncated"] is False
    assert len(body["spans"]) == 1
    assert body["span_limit"] == 200 and body["span_offset"] == 0


def test_get_trace_span_paging_with_stable_header(mcp_session):
    first = _ok(
        _call(
            mcp_session,
            "get_trace",
            {"trace_id": "trace-big", "api_key": KEY_1, "span_limit": 2, "span_offset": 0},
        )
    )
    assert first["span_count"] == 5
    assert first["span_limit"] == 2 and first["span_offset"] == 0
    assert len(first["spans"]) == 2
    assert first["spans_truncated"] is True
    assert [s["span_id"] for s in first["spans"]] == ["span-big-0", "span-big-1"]

    second = _ok(
        _call(
            mcp_session,
            "get_trace",
            {"trace_id": "trace-big", "api_key": KEY_1, "span_limit": 2, "span_offset": 2},
        )
    )
    assert second["span_count"] == 5
    assert [s["span_id"] for s in second["spans"]] == ["span-big-2", "span-big-3"]
    assert second["spans_truncated"] is True

    last = _ok(
        _call(
            mcp_session,
            "get_trace",
            {"trace_id": "trace-big", "api_key": KEY_1, "span_limit": 2, "span_offset": 4},
        )
    )
    assert [s["span_id"] for s in last["spans"]] == ["span-big-4"]
    assert last["spans_truncated"] is False

    # Header is stable across pages (page-independent summary).
    for page in (first, second, last):
        assert page["status"] == "ERROR"
        assert page["start_time"] == first["start_time"]
        assert page["end_time"] == first["end_time"]


def test_get_trace_rejects_blank_id(mcp_session):
    err = _err(_call(mcp_session, "get_trace", {"trace_id": "   ", "api_key": KEY_1}))
    assert "unknown trace" in err


def test_get_span_rejects_blank_id(mcp_session):
    err = _err(_call(mcp_session, "get_span", {"span_id": "", "api_key": KEY_1}))
    assert "unknown span" in err


def test_cost_summary_rejects_negative_dates(mcp_session):
    err = _err(_call(mcp_session, "cost_summary", {"api_key": KEY_1, "start_date": -5}))
    assert "start_date" in err
    err = _err(_call(mcp_session, "cost_summary", {"api_key": KEY_1, "end_date": -5}))
    assert "end_date" in err
    err = _err(
        _call(mcp_session, "cost_summary", {"api_key": KEY_1, "start_date": 200, "end_date": 100})
    )
    assert "start_date" in err and "end_date" in err


def test_phase5_pagination_contract_kept(mcp_session):
    body = _ok(_call(mcp_session, "query_traces", {"api_key": KEY_1}))
    assert body["total"] == 2
    assert body["limit"] == 20 and body["offset"] == 0
    alerts = _ok(_call(mcp_session, "query_security_alerts", {"api_key": KEY_1}))
    assert alerts["total"] == 1
    assert alerts["count"] == 1
    assert alerts["limit"] == 25 and alerts["offset"] == 0
