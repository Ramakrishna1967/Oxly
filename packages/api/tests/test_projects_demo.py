# Copyright 2026 Oxly Contributors
# SPDX-License-Identifier: Apache-2.0

"""Regression tests for DEMO_MODE project creation.

Covers the FK 500 fix: the synthetic `demo` user previously had no row in
`users`, so POST /api/v1/projects failed its insert into `user_projects`.
init_db() now seeds the demo user when DEMO_MODE is on.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

# Add src directories to path (mirrors conftest.py)
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from api.db import Database


@pytest_asyncio.fixture
async def demo_db(tmp_path, monkeypatch):
    """Fresh database with DEMO_MODE enabled before init."""
    from api.config import settings

    monkeypatch.setattr(settings, "DEMO_MODE", True)
    db = Database(str(tmp_path / "demo_oxly.db"))
    await db.init_db()
    return db


@pytest_asyncio.fixture
async def demo_app(demo_db):
    """FastAPI app wired to the demo database."""
    import api.db as db_module

    db_module._db = demo_db
    from api.main import create_app

    return create_app()


@pytest_asyncio.fixture
async def demo_client(demo_app):
    """Async test client (no auth headers — demo mode bypasses login)."""
    transport = ASGITransport(app=demo_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


class TestDemoModeProjects:
    """Project CRUD without auth while DEMO_MODE is on."""

    @pytest.mark.asyncio
    async def test_create_project_no_auth(self, demo_client):
        response = await demo_client.post("/api/v1/projects", json={"name": "demo-proj"})
        assert response.status_code == 200
        data = response.json()
        assert data["project"]["name"] == "demo-proj"
        assert data["api_key"].startswith("ak_")

    @pytest.mark.asyncio
    async def test_created_project_visible_in_list_and_detail(self, demo_client):
        created = await demo_client.post("/api/v1/projects", json={"name": "demo-proj"})
        project_id = created.json()["project"]["id"]

        listed = await demo_client.get("/api/v1/projects")
        assert listed.status_code == 200
        assert [p["id"] for p in listed.json()] == [project_id]

        detail = await demo_client.get(f"/api/v1/projects/{project_id}")
        assert detail.status_code == 200
        assert detail.json()["name"] == "demo-proj"
