# Copyright 2026 Oxly Contributors
# SPDX-License-Identifier: Apache-2.0

"""Phase 3 tests: skill pack + status UI + manifest (tools unchanged).

Done when: packages/plugin ships 4 skills (oxly-overview, trace-debug,
security-triage, cost-report), /plugin/ui/ documents the 6-tool set with
live health + manifest status, plugin.json declares phase 3, and /mcp
still serves exactly the 6 Phase-2 tools (scoping covered in
test_mcp_phase2.py).
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

PLUGIN_DIR = Path(__file__).resolve().parents[3] / "packages" / "plugin"
EXPECTED_SKILLS = ("oxly-overview", "trace-debug", "security-triage", "cost-report")
EXPECTED_TOOLS = {
    "query_traces",
    "get_trace",
    "get_span",
    "query_security_alerts",
    "cost_summary",
    "whoami",
}


def _client():
    from api.main import create_app

    return TestClient(create_app(), base_url="http://localhost:8000")


def test_plugin_manifest_is_phase3():
    manifest = json.loads((PLUGIN_DIR / "plugin.json").read_text())
    assert manifest["name"] == "oxly"
    assert manifest["phase"] >= 3
    assert manifest["mcp"]["endpoint"] == "/mcp"
    assert manifest["skills"] == list(EXPECTED_SKILLS)


def test_skill_pack_present_and_phase3():
    for skill in EXPECTED_SKILLS:
        skill_dir = PLUGIN_DIR / "skills" / skill
        assert (skill_dir / "SKILL.md").is_file(), f"{skill}/SKILL.md missing"
        meta = json.loads((skill_dir / "skill.json").read_text())
        assert meta["name"] == skill
        assert meta["phase"] >= 3
        assert meta["entry"] == "SKILL.md"
        body = (skill_dir / "SKILL.md").read_text()
        assert "api_key" in body
        assert "unknown project" in body


def test_skill_pack_covers_all_tools():
    corpus = "\n".join(
        (PLUGIN_DIR / "skills" / skill / "SKILL.md").read_text() for skill in EXPECTED_SKILLS
    )
    for tool in EXPECTED_TOOLS:
        assert tool in corpus, f"{tool} not covered by skill pack"


def test_plugin_shell_still_served():
    with _client() as c:
        for path in (
            "/plugin/plugin.json",
            "/plugin/mcp.json",
            "/.well-known/oxly-plugin.json",
            "/plugin/ui/",
        ):
            r = c.get(path)
            assert r.status_code == 200, f"{path} -> {r.status_code}"
        ui = c.get("/plugin/ui/").text
        for marker in ("Tools", "Auth", "Skills", "Status", "trace-debug", "cost-report"):
            assert marker in ui, f"UI missing marker: {marker}"
        for tool in EXPECTED_TOOLS:
            assert tool in ui, f"UI missing tool: {tool}"


def test_mcp_still_serves_six_tools():
    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    with _client() as c:
        init = c.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "phase3-test", "version": "0.0"},
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
        tools = c.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            headers=authed,
        )
        assert tools.status_code == 200, tools.text[:500]
        assert '"query_traces"' in tools.text
        payloads = [
            json.loads(line[len("data:") :].strip())
            for line in tools.text.splitlines()
            if line.strip().startswith("data:")
        ]
        assert payloads, tools.text[:500]
        names = {t["name"] for t in payloads[-1]["result"]["tools"]}
        assert names == EXPECTED_TOOLS, names
