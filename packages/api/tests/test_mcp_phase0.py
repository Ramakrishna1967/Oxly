# Copyright 2026 Oxly Contributors
# SPDX-License-Identifier: Apache-2.0

"""Phase 0 scaffold tests: plugin shell + live /mcp endpoint.

Done when: MCP Inspector -> http://localhost:8000/mcp -> init ok,
tool list loads. (Phase 0 expected an empty list; Phase 2 registers the
read-only observability tools — see test_mcp_phase2.py for tool coverage.)
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def _client():
    from api.main import create_app

    # base_url localhost passes the MCP DNS-rebinding allowlist
    # (testserver would 421).
    return TestClient(create_app(), base_url="http://localhost:8000")


def test_plugin_shell_files_served():
    with _client() as c:
        for path in (
            "/plugin/plugin.json",
            "/plugin/mcp.json",
            "/.well-known/oxly-plugin.json",
            "/plugin/ui/",
        ):
            r = c.get(path)
            assert r.status_code == 200, f"{path} -> {r.status_code}"
        manifest = c.get("/plugin/plugin.json").json()
        assert manifest["name"] == "oxly"
        assert manifest["mcp"]["endpoint"] == "/mcp"


def test_csp_allows_stable_ui_origin():
    with _client() as c:
        csp = c.get("/plugin/plugin.json").headers.get("content-security-policy", "")
        assert "frame-ancestors" in csp
        assert "https://plugin.oxly.sh" in csp


def test_mcp_init_ok_empty_tools():
    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    init_payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "phase0-test", "version": "0.0"},
        },
    }
    with _client() as c:
        init = c.post("/mcp", json=init_payload, headers=headers)
        assert init.status_code == 200, init.text[:500]
        assert '"oxly"' in init.text
        session_id = init.headers.get("mcp-session-id")
        assert session_id, "Streamable HTTP must return mcp-session-id"

        authed = {**headers, "mcp-session-id": session_id}
        c.post(
            "/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"}, headers=authed
        )
        tools = c.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            headers=authed,
        )
        assert tools.status_code == 200, tools.text[:500]
        # Phase 2: the endpoint serves the read-only observability toolset.
        assert '"query_traces"' in tools.text


def test_mcp_reuses_project_helpers():
    # Phase 0 wiring point for Phase 1 tools.
    from api.mcp_server import get_user_project_ids, verify_project_ownership

    assert callable(get_user_project_ids)
    assert callable(verify_project_ownership)
