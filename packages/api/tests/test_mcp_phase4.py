# Copyright 2026 Oxly Contributors
# SPDX-License-Identifier: Apache-2.0

"""Phase 4 tests: submission-ready shell + MCP consistency (tools still 6).

Done when: plugin.json declares phase 4 with legal/openapi metadata,
/.well-known/ai-plugin.json aliases the canonical manifest,
/plugin/openapi.json is served, query_traces validates status OK/ERROR,
query_security_alerts pages with offset, and get_trace/get_span/whoami
accept an optional matching project_id (mismatch -> unknown project).
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

KEY_1 = "ak_phase4testkey00000000000000001"
KEY_2 = "ak_phase4testkey00000000000000002"

_call_ids = itertools.count(500)


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

    db = Database(str(tmp_path / "mcp_phase4.db"))
    import asyncio

    asyncio.run(db.init_db())

    async def seed():
        conn = await db.get_connection()
        now_ns = time.time_ns()
        now_s = now_ns // 1_000_000_000
        await conn.execute(
            "INSERT INTO users (id, email, hashed_password, is_active) VALUES (?, ?, ?, 1)",
            ("u1", "phase4@oxly.dev", pwd_context.hash("irrelevant")),
        )
        await conn.execute(
            "INSERT INTO projects (id, name, api_key_hash) VALUES (?, ?, ?)",
            ("p1", "Phase Four One", pwd_context.hash(KEY_1)),
        )
        await conn.execute(
            "INSERT INTO projects (id, name, api_key_hash) VALUES (?, ?, ?)",
            ("p2", "Phase Four Two", pwd_context.hash(KEY_2)),
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
            "INSERT INTO traces (trace_id, project_id, start_time, end_time, status)"
            " VALUES ('trace-b', 'p1', ?, ?, 'ERROR')",
            (now_ns - 1_800_000_000_000, now_ns - 1_799_000_000_000),
        )
        spans = [
            (
                "span-a1",
                "trace-a",
                None,
                "p1",
                "root",
                now_ns - 3_600_000_000_000,
                now_ns - 3_598_000_000_000,
                "OK",
            ),
            (
                "span-b1",
                "trace-b",
                None,
                "p1",
                "root",
                now_ns - 1_800_000_000_000,
                now_ns - 1_799_000_000_000,
                "ERROR",
            ),
        ]
        for sid, tid, parent, proj, name, st, et, status in spans:
            await conn.execute(
                """INSERT INTO spans
                   (span_id, trace_id, parent_span_id, project_id, name,
                    start_time, end_time, duration_ms, status, service_name,
                    attributes, events)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'test', '{}', '[]')""",
                (sid, tid, parent, proj, name, st, et, (et - st) // 1_000_000, status),
            )
        await conn.execute(
            """INSERT INTO security_alerts
               (id, trace_id, span_id, project_id, severity, rule_name, message)
               VALUES ('al-1', 'trace-a', 'span-a1', 'p1', 'high', 'prompt_injection', 'suspect')"""
        )
        await conn.execute(
            """INSERT INTO security_alerts
               (id, trace_id, span_id, project_id, severity, rule_name, message)
               VALUES ('al-2', 'trace-b', 'span-b1', 'p1', 'low', 'pii', 'email redacted')"""
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
                "clientInfo": {"name": "phase4-test", "version": "0.0"},
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


def test_manifest_is_phase4_with_submission_fields():
    manifest = json.loads((PLUGIN_DIR / "plugin.json").read_text())
    assert manifest["phase"] == 4
    assert manifest["openapi"] == "/plugin/openapi.json"
    assert manifest["legacyWellKnown"] == "/.well-known/ai-plugin.json"
    assert "privacy_policy_url" in manifest["legal"]
    assert manifest["skills"] == list(EXPECTED_SKILLS)
    openapi = json.loads((PLUGIN_DIR / "openapi.json").read_text())
    assert openapi["openapi"].startswith("3.")
    assert "/mcp" in openapi["paths"]


def test_shell_serves_phase4_endpoints():
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
        assert c.get("/.well-known/ai-plugin.json").json() == c.get("/plugin/plugin.json").json()
        ui = c.get("/plugin/ui/").text
        for marker in ("Phase 4", "MCP live", "Dashboard", "Status", "trace-debug", "cost-report"):
            assert marker in ui, f"UI missing marker: {marker}"
        for tool in EXPECTED_TOOLS:
            assert tool in ui, f"UI missing tool: {tool}"


def test_status_filter_validated(mcp_session):
    body = _ok(_call(mcp_session, "query_traces", {"api_key": KEY_1, "status": "error"}))
    assert body["total"] == 1
    assert [t["trace_id"] for t in body["traces"]] == ["trace-b"]
    err = _err(_call(mcp_session, "query_traces", {"api_key": KEY_1, "status": "bogus"}))
    assert "unknown status" in err


def test_alerts_offset_paging(mcp_session):
    first = _ok(
        _call(mcp_session, "query_security_alerts", {"api_key": KEY_1, "limit": 1, "offset": 0})
    )
    second = _ok(
        _call(mcp_session, "query_security_alerts", {"api_key": KEY_1, "limit": 1, "offset": 1})
    )
    assert first["count"] == 1 and second["count"] == 1
    assert first["alerts"][0]["id"] != second["alerts"][0]["id"]


def test_point_lookups_accept_matching_project_id(mcp_session):
    body = _ok(
        _call(
            mcp_session,
            "get_trace",
            {"trace_id": "trace-a", "api_key": KEY_1, "project_id": "p1"},
        )
    )
    assert body["project_id"] == "p1"
    body = _ok(
        _call(mcp_session, "get_span", {"span_id": "span-a1", "api_key": KEY_1, "project_id": "p1"})
    )
    assert body["span_id"] == "span-a1"
    body = _ok(_call(mcp_session, "whoami", {"api_key": KEY_1, "project_id": "p1"}))
    assert body["project_id"] == "p1"


def test_point_lookups_reject_mismatched_project_id(mcp_session):
    err = _err(
        _call(
            mcp_session,
            "get_trace",
            {"trace_id": "trace-a", "api_key": KEY_1, "project_id": "p2"},
        )
    )
    assert "unknown project" in err
    err = _err(
        _call(mcp_session, "get_span", {"span_id": "span-a1", "api_key": KEY_1, "project_id": "p2"})
    )
    assert "unknown project" in err
    err = _err(_call(mcp_session, "whoami", {"api_key": KEY_1, "project_id": "p2"}))
    assert "unknown project" in err
