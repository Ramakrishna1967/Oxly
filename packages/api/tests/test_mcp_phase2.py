# Copyright 2026 Oxly Contributors
# SPDX-License-Identifier: Apache-2.0

"""Phase 2 tests: project-scoped read-only MCP tools over Streamable HTTP.

Done when: MCP Inspector -> http://localhost:8000/mcp -> init ok,
tools/list returns the 6 Phase 2 tools, and every tools/call is
project-scoped via the project's SDK API key (ak_...).

Seeding uses direct DB inserts (deterministic, no async-queue waits).
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

KEY_1 = "ak_phase2testkey00000000000000001"
KEY_2 = "ak_phase2testkey00000000000000002"

_call_ids = itertools.count(100)


def _parse_result(response_text: str) -> dict:
    """Extract the single JSON-RPC payload from an SSE/tools-call body."""
    payloads = []
    for line in response_text.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            payloads.append(json.loads(line[len("data:") :].strip()))
    assert payloads, f"no SSE data payload in: {response_text[:500]}"
    return payloads[-1]


@pytest.fixture()
def seeded_client(tmp_path, monkeypatch):
    """TestClient wired to a temp DB seeded with 2 projects + traces."""
    import api.db as db_module
    from api.db import Database

    db = Database(str(tmp_path / "mcp_phase2.db"))
    # init_db is async; run it synchronously before the client starts.
    import asyncio

    asyncio.run(db.init_db())

    conn_holder: dict = {}

    async def seed():
        conn = await db.get_connection()
        conn_holder["conn"] = conn
        # Retention sweep deletes spans with start_time older than 90 days,
        # so seed timestamps must be recent (ns for spans/traces, s for cost).
        now_ns = time.time_ns()
        now_s = now_ns // 1_000_000_000
        day_s = 86_400
        t = {
            "a0": now_ns - 3_600_000_000_000,
            "a1": now_ns - 3_599_000_000_000,
            "a2": now_ns - 3_598_000_000_000,
            "b0": now_ns - 1_800_000_000_000,
            "b1": now_ns - 1_799_000_000_000,
        }
        await conn.execute(
            "INSERT INTO users (id, email, hashed_password, is_active) VALUES (?, ?, ?, 1)",
            ("u1", "phase2@oxly.dev", pwd_context.hash("irrelevant")),
        )
        await conn.execute(
            "INSERT INTO projects (id, name, api_key_hash) VALUES (?, ?, ?)",
            ("p1", "Phase Two One", pwd_context.hash(KEY_1)),
        )
        await conn.execute(
            "INSERT INTO projects (id, name, api_key_hash) VALUES (?, ?, ?)",
            ("p2", "Phase Two Two", pwd_context.hash(KEY_2)),
        )
        await conn.execute(
            "INSERT INTO user_projects (user_id, project_id, role) VALUES ('u1', 'p1', 'owner')"
        )
        await conn.execute(
            "INSERT INTO user_projects (user_id, project_id, role) VALUES ('u1', 'p2', 'owner')"
        )
        # traces p1: trace-a (2 spans), trace-b (1 ERROR span); p2: trace-x
        await conn.execute(
            "INSERT INTO traces (trace_id, project_id, start_time, end_time, status)"
            " VALUES ('trace-a', 'p1', ?, ?, 'OK')",
            (t["a0"], t["a2"]),
        )
        await conn.execute(
            "INSERT INTO traces (trace_id, project_id, start_time, end_time, status)"
            " VALUES ('trace-b', 'p1', ?, ?, 'ERROR')",
            (t["b0"], t["b1"]),
        )
        await conn.execute(
            "INSERT INTO traces (trace_id, project_id, start_time, end_time, status)"
            " VALUES ('trace-x', 'p2', ?, ?, 'OK')",
            (t["a0"], t["a1"]),
        )
        spans = [
            (
                "span-a1",
                "trace-a",
                None,
                "p1",
                "root",
                t["a0"],
                t["a2"],
                "OK",
                '{"model": "gpt-4"}',
            ),
            (
                "span-a2",
                "trace-a",
                "span-a1",
                "p1",
                "llm",
                t["a1"],
                t["a2"],
                "OK",
                '{"tokens": "5"}',
            ),
            ("span-b1", "trace-b", None, "p1", "root", t["b0"], t["b1"], "ERROR", "{}"),
            ("span-x1", "trace-x", None, "p2", "root", t["a0"], t["a1"], "OK", "{}"),
        ]
        for sid, tid, parent, proj, name, st, et, status, attrs in spans:
            await conn.execute(
                """INSERT INTO spans
                   (span_id, trace_id, parent_span_id, project_id, name,
                    start_time, end_time, duration_ms, status, service_name,
                    attributes, events)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'test', ?, '[]')""",
                (sid, tid, parent, proj, name, st, et, (et - st) // 1_000_000, status, attrs),
            )
        await conn.execute(
            """INSERT INTO security_alerts
               (id, trace_id, span_id, project_id, severity, rule_name, message)
               VALUES ('al-1', 'trace-a', 'span-a2', 'p1', 'high', 'prompt_injection', 'suspect')"""
        )
        await conn.execute(
            """INSERT INTO security_alerts
               (id, trace_id, span_id, project_id, severity, rule_name, message)
               VALUES ('al-2', 'trace-b', 'span-b1', 'p1', 'low', 'pii', 'email redacted')"""
        )
        await conn.execute(
            """INSERT INTO security_alerts
               (id, trace_id, span_id, project_id, severity, rule_name, message)
               VALUES ('al-3', 'trace-x', 'span-x1', 'p2', 'critical', 'anomaly', 'other')"""
        )
        # cost_metrics timestamps are Unix seconds (see api/cost.py)
        await conn.execute(
            """INSERT INTO cost_metrics
               (project_id, model, timestamp, prompt_tokens, completion_tokens,
                total_tokens, cost_usd)
               VALUES ('p1', 'gpt-4', ?, 100, 50, 150, 0.01)""",
            (now_s - 2 * day_s,),
        )
        await conn.execute(
            """INSERT INTO cost_metrics
               (project_id, model, timestamp, prompt_tokens, completion_tokens,
                total_tokens, cost_usd)
               VALUES ('p1', 'gpt-4', ?, 200, 100, 300, 0.02)""",
            (now_s - day_s,),
        )
        await conn.execute(
            """INSERT INTO cost_metrics
               (project_id, model, timestamp, prompt_tokens, completion_tokens,
                total_tokens, cost_usd)
               VALUES ('p1', 'claude-3', ?, 10, 10, 20, 0.005)""",
            (now_s - day_s,),
        )
        await conn.commit()
        await conn.close()

    asyncio.run(seed())
    monkeypatch.setattr(db_module, "_db", db)

    from api.main import create_app

    # base_url localhost passes the MCP DNS-rebinding allowlist.
    with TestClient(create_app(), base_url="http://localhost:8000") as client:
        yield client


@pytest.fixture()
def mcp_session(seeded_client):
    """Run initialize + notifications/initialized, return (client, headers)."""
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
                "clientInfo": {"name": "phase2-test", "version": "0.0"},
            },
        },
        headers=headers,
    )
    assert init.status_code == 200, init.text[:500]
    session_id = init.headers.get("mcp-session-id")
    assert session_id, "Streamable HTTP must return mcp-session-id"
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


def _ok_text(payload: dict) -> str:
    assert "result" in payload, f"expected result, got: {json.dumps(payload)[:500]}"
    result = payload["result"]
    assert not result.get("isError"), f"tool errored: {json.dumps(payload)[:500]}"
    texts = [b.get("text", "") for b in result.get("content", []) if isinstance(b, dict)]
    assert texts, f"no text content in: {json.dumps(payload)[:500]}"
    return "\n".join(texts)


def _err_text(payload: dict) -> str:
    if "error" in payload:
        return json.dumps(payload["error"])
    result = payload.get("result", {})
    assert result.get("isError"), f"expected tool error, got: {json.dumps(payload)[:500]}"
    return json.dumps(result.get("content", []))


def test_tools_list_has_six_phase2_tools(mcp_session):
    c, authed = mcp_session
    r = c.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        headers=authed,
    )
    assert r.status_code == 200, r.text[:500]
    payload = _parse_result(r.text)
    names = {t["name"] for t in payload["result"]["tools"]}
    assert names == {
        "query_traces",
        "get_trace",
        "get_span",
        "query_security_alerts",
        "cost_summary",
        "whoami",
    }, names


def test_whoami_returns_key_project(mcp_session):
    body = json.loads(_ok_text(_call(mcp_session, "whoami", {"api_key": KEY_1})))
    assert body["project_id"] == "p1"
    assert body["project_name"] == "Phase Two One"


def test_query_traces_scoped_to_key(mcp_session):
    body = json.loads(_ok_text(_call(mcp_session, "query_traces", {"api_key": KEY_1})))
    assert body["project_id"] == "p1"
    assert body["total"] == 2
    ids = [t["trace_id"] for t in body["traces"]]
    assert ids == ["trace-b", "trace-a"]  # newest first
    counts = {t["trace_id"]: t["span_count"] for t in body["traces"]}
    assert counts == {"trace-a": 2, "trace-b": 1}


def test_get_trace_returns_spans_in_order(mcp_session):
    body = json.loads(
        _ok_text(_call(mcp_session, "get_trace", {"trace_id": "trace-a", "api_key": KEY_1}))
    )
    assert body["project_id"] == "p1"
    assert [s["span_id"] for s in body["spans"]] == ["span-a1", "span-a2"]
    assert body["spans"][0]["attributes"] == {"model": "gpt-4"}


def test_get_span_detail(mcp_session):
    body = json.loads(
        _ok_text(_call(mcp_session, "get_span", {"span_id": "span-b1", "api_key": KEY_1}))
    )
    assert body["status"] == "ERROR"
    assert body["trace_id"] == "trace-b"


def test_query_security_alerts_and_severity_filter(mcp_session):
    body = json.loads(_ok_text(_call(mcp_session, "query_security_alerts", {"api_key": KEY_1})))
    assert body["count"] == 2
    assert {a["id"] for a in body["alerts"]} == {"al-1", "al-2"}
    body = json.loads(
        _ok_text(
            _call(
                mcp_session,
                "query_security_alerts",
                {"api_key": KEY_1, "severity": "HIGH"},
            )
        )
    )
    assert [a["id"] for a in body["alerts"]] == ["al-1"]


def test_cost_summary_totals_and_by_model(mcp_session):
    body = json.loads(_ok_text(_call(mcp_session, "cost_summary", {"api_key": KEY_1})))
    assert body["project_id"] == "p1"
    assert body["total_cost_usd"] == pytest.approx(0.035)
    assert body["total_prompt_tokens"] == 310
    assert body["total_completion_tokens"] == 160
    by_model = {m["model"]: m for m in body["by_model"]}
    assert by_model["gpt-4"]["total_cost_usd"] == pytest.approx(0.03)
    assert by_model["claude-3"]["total_tokens"] == 20
    assert len(body["timeseries"]) == 2  # two distinct day buckets


def test_invalid_key_rejected_without_oracle(mcp_session):
    err = _err_text(_call(mcp_session, "whoami", {"api_key": "ak_wrongkey00000000000000000"}))
    assert "unknown project" in err
    # sibling project's trace must not resolve under this key either
    err = _err_text(_call(mcp_session, "get_trace", {"trace_id": "trace-a", "api_key": "ak_nope"}))
    assert "unknown project" in err or "unknown trace" in err


def test_cross_project_isolation(mcp_session):
    # KEY_1 must not read p2's trace, span, or alerts.
    err = _err_text(_call(mcp_session, "get_trace", {"trace_id": "trace-x", "api_key": KEY_1}))
    assert "unknown trace" in err
    err = _err_text(_call(mcp_session, "get_span", {"span_id": "span-x1", "api_key": KEY_1}))
    assert "unknown span" in err
    body = json.loads(_ok_text(_call(mcp_session, "query_security_alerts", {"api_key": KEY_2})))
    assert [a["id"] for a in body["alerts"]] == ["al-3"]
    # explicit mismatched project_id is rejected, not silently re-scoped
    err = _err_text(
        _call(
            mcp_session,
            "query_traces",
            {"api_key": KEY_1, "project_id": "p2"},
        )
    )
    assert "unknown project" in err
