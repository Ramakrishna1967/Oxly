# Copyright 2026 Oxly Contributors
# SPDX-License-Identifier: Apache-2.0

"""Integration tests for trace and project API endpoints."""

from __future__ import annotations

import pytest


class TestHealth:
    """Health check endpoint tests."""

    @pytest.mark.asyncio
    async def test_health_check(self, client):
        response = await client.get("/api/v1/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert "version" in data
        # Contract consumed by Dashboard System Status panel
        assert set(data["services"]) == {"api", "sqlite", "queue", "retention"}

    @pytest.mark.asyncio
    async def test_root_endpoint(self, client):
        response = await client.get("/")
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Oxly API"


class TestAuth:
    """Authentication endpoint tests."""

    @pytest.mark.asyncio
    async def test_register_user(self, client):
        response = await client.post(
            "/api/v1/auth/register",
            json={"email": "newuser@test.com", "password": "TestPassword123"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["email"] == "newuser@test.com"
        assert data["is_active"] is True

    @pytest.mark.asyncio
    async def test_register_duplicate_email(self, client):
        await client.post(
            "/api/v1/auth/register",
            json={"email": "dup@test.com", "password": "TestPassword123"},
        )
        response = await client.post(
            "/api/v1/auth/register",
            json={"email": "dup@test.com", "password": "TestPassword456"},
        )
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_login_success(self, client):
        await client.post(
            "/api/v1/auth/register",
            json={"email": "login@test.com", "password": "TestPassword123"},
        )
        response = await client.post(
            "/api/v1/auth/login",
            json={"email": "login@test.com", "password": "TestPassword123"},
        )
        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"

    @pytest.mark.asyncio
    async def test_login_wrong_password(self, client):
        await client.post(
            "/api/v1/auth/register",
            json={"email": "wrong@test.com", "password": "TestPassword123"},
        )
        response = await client.post(
            "/api/v1/auth/login",
            json={"email": "wrong@test.com", "password": "wrongpassword"},
        )
        assert response.status_code == 401


class TestProjects:
    """Project CRUD endpoint tests."""

    @pytest.mark.asyncio
    async def test_create_project(self, client, auth_headers):
        response = await client.post(
            "/api/v1/projects",
            json={"name": "My Project"},
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["project"]["name"] == "My Project"
        assert data["api_key"].startswith("ak_")

    @pytest.mark.asyncio
    async def test_list_projects(self, client, auth_headers):
        # Create a project first
        await client.post(
            "/api/v1/projects",
            json={"name": "List Test"},
            headers=auth_headers,
        )
        response = await client.get("/api/v1/projects", headers=auth_headers)
        assert response.status_code == 200
        projects = response.json()
        assert len(projects) >= 1

    @pytest.mark.asyncio
    async def test_get_project(self, client, auth_headers, test_project):
        project, _ = test_project
        response = await client.get(
            f"/api/v1/projects/{project['id']}",
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert response.json()["name"] == "Test Project"

    @pytest.mark.asyncio
    async def test_delete_project(self, client, auth_headers, test_project):
        project, _ = test_project
        response = await client.delete(
            f"/api/v1/projects/{project['id']}",
            headers=auth_headers,
        )
        assert response.status_code == 204

    @pytest.mark.asyncio
    async def test_unauthorized_access(self, client):
        response = await client.get("/api/v1/projects")
        assert response.status_code in [401, 403]


class TestTraces:
    """Trace endpoint tests."""

    @pytest.mark.asyncio
    async def test_list_traces_empty(self, client, auth_headers):
        response = await client.get("/api/v1/traces", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["items"] == []
        assert data["total"] == 0

    @pytest.mark.asyncio
    async def test_get_trace_not_found(self, client, auth_headers):
        response = await client.get(
            "/api/v1/traces/nonexistent",
            headers=auth_headers,
        )
        assert response.status_code == 404


class TestSpans:
    """Span endpoint tests."""

    @pytest.mark.asyncio
    async def test_get_span_not_found(self, client, auth_headers):
        response = await client.get(
            "/api/v1/spans/nonexistent",
            headers=auth_headers,
        )
        assert response.status_code == 404


class TestSecurityAlerts:
    """Security alert endpoint tests."""

    @pytest.mark.asyncio
    async def test_list_alerts_empty(self, client, auth_headers):
        response = await client.get(
            "/api/v1/security/alerts",
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert response.json() == []
