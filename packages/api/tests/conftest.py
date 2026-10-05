# Copyright 2026 Oxly Contributors
# SPDX-License-Identifier: Apache-2.0

"""Test fixtures and shared configuration for API integration tests."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

# Add src directories to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from api.db import Database

# Use a temp database for tests
_test_db_path = None
_test_db = None


@pytest.fixture(scope="session")
def event_loop():
    """Create a session-scoped event loop."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(autouse=True)
def reset_db():
    """Reset the global database singleton between tests."""
    import api.db as db_module

    db_module._db = None
    # The IP rate limiter is a module-global store; TestClient requests all
    # share one IP, so without a reset any single pytest process running
    # 100+ requests trips 429s (flaky suite). Test-only reset, no prod impact.
    import api.middleware as middleware_module

    middleware_module._rate_limit_store.clear()
    yield
    db_module._db = None
    middleware_module._rate_limit_store.clear()


@pytest_asyncio.fixture
async def test_db(tmp_path):
    """Create a fresh test database."""
    db_path = tmp_path / "test_oxly.db"
    db = Database(str(db_path))
    await db.init_db()
    return db


@pytest_asyncio.fixture
async def app(test_db):
    """Create a test FastAPI application with test database."""
    import api.db as db_module

    # Override the global database instance
    db_module._db = test_db

    from api.main import create_app

    test_app = create_app()
    return test_app


@pytest_asyncio.fixture
async def client(app):
    """Create an async test client."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


@pytest_asyncio.fixture
async def auth_headers(client):
    """Register a test user and return auth headers."""
    # Register
    await client.post(
        "/api/v1/auth/register",
        json={"email": "test@oxly.dev", "password": "TestPassword123"},
    )

    # Login
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": "test@oxly.dev", "password": "TestPassword123"},
    )
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def test_project(client, auth_headers):
    """Create a test project and return (project, api_key)."""
    response = await client.post(
        "/api/v1/projects",
        json={"name": "Test Project"},
        headers=auth_headers,
    )
    data = response.json()
    return data["project"], data["api_key"]


@pytest_asyncio.fixture
def sample_spans():
    """Sample span payloads for ingestion tests."""
    return {
        "spans": [
            {
                "span_id": "span-001",
                "trace_id": "trace-001",
                "parent_span_id": None,
                "name": "root_agent",
                "start_time": 1000000000,
                "end_time": 2000000000,
                "duration_ms": 1000,
                "status": "OK",
                "service_name": "test-service",
                "attributes": {"model": "gpt-4"},
                "events": [],
            },
            {
                "span_id": "span-002",
                "trace_id": "trace-001",
                "parent_span_id": "span-001",
                "name": "llm_call",
                "start_time": 1100000000,
                "end_time": 1900000000,
                "duration_ms": 800,
                "status": "OK",
                "service_name": "test-service",
                "attributes": {"tokens": "150"},
                "events": [],
            },
        ],
    }
