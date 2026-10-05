# Copyright 2026 Oxly Contributors
# SPDX-License-Identifier: Apache-2.0

"""Tests for the /v1/traces ingestion endpoint.

Uses its own register/login/create-project flow with a password that
passes the app's validator, rather than conftest.py's auth_headers/
test_project fixtures -- those use "testpassword123" (no uppercase),
which fails validation and is a separate pre-existing, out-of-scope bug.
"""

import asyncio

import pytest_asyncio


@pytest_asyncio.fixture
async def api_key(client, app):
    """Register, login, create a project, return its API key.

    Also seeds app.state.span_queue directly -- the test `app` fixture
    never runs main.py's lifespan (which normally creates it), and
    ingest.py reads it unconditionally.
    """
    app.state.span_queue = asyncio.Queue()

    await client.post(
        "/api/v1/auth/register",
        json={"email": "ingest-test@oxly.dev", "password": "TestPassword123"},
    )
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": "ingest-test@oxly.dev", "password": "TestPassword123"},
    )
    token = login.json()["access_token"]
    project = await client.post(
        "/api/v1/projects",
        json={"name": "ingest-test-project"},
        headers={"Authorization": f"Bearer {token}"},
    )
    return project.json()["api_key"]


async def test_ingest_skips_non_dict_span_without_500ing_batch(client, api_key):
    """A malformed (non-dict) entry must not crash the whole batch.

    _validate_span used to assume every entry was a dict; a bare int/
    bool/null in the spans array raised an uncaught TypeError (`"x" not
    in 123`), which only `except ValueError` was there to catch --
    turning the whole request into an unhandled 500 and dropping every
    span in the batch, including the valid ones.
    """
    good_span = {
        "span_id": "good-1",
        "trace_id": "trace-1",
        "name": "ok",
        "start_time": 1,
        "end_time": 2,
    }
    response = await client.post(
        "/v1/traces",
        headers={"X-API-Key": api_key},
        json={"spans": [123, good_span, None, True]},
    )
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "accepted"
    assert body["spans_queued"] == 1


async def test_ingest_without_queue_returns_503(client, api_key, app):
    """Ingest before lifespan must 503, not 500.

    Regression: ingest.py read request.app.state.span_queue unconditionally,
    so any app without lifespan (or a startup ordering bug) raised an
    unhandled AttributeError -> 500. It must return 503 instead.
    """
    del app.state.span_queue
    response = await client.post(
        "/v1/traces",
        headers={"X-API-Key": api_key},
        json={
            "spans": [
                {
                    "span_id": "s1",
                    "trace_id": "t1",
                    "name": "r",
                    "start_time": 1,
                    "end_time": 2,
                }
            ]
        },
    )
    assert response.status_code == 503
    assert response.json()["detail"] == "Ingest pipeline not ready"
