# Copyright 2026 Oxly Contributors
# SPDX-License-Identifier: Apache-2.0

"""Security alert routes  query alerts by severity and project."""

from __future__ import annotations

import json

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Query

from api.db import get_db
from api.dependencies import get_current_active_user, get_user_project_ids, verify_project_ownership
from api.schemas import SecurityAlertSchema, SecurityAlertSeverity

router = APIRouter()


@router.get("/security/alerts", response_model=list[SecurityAlertSchema])
async def list_security_alerts(
    project_id: str | None = Query(None),
    severity: SecurityAlertSeverity | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    db: aiosqlite.Connection = Depends(get_db),
    current_user: dict = Depends(get_current_active_user),
):
    """List security alerts with optional filters.

    Filters: project_id, severity.
    Returns most recent alerts first.
    """
    filters = []
    params: list = []

    if project_id:
        if not await verify_project_ownership(db, current_user["id"], project_id):
            raise HTTPException(status_code=403, detail="Project not found")
        filters.append("project_id = ?")
        params.append(project_id)
    else:
        owned_ids = await get_user_project_ids(db, current_user["id"])
        if not owned_ids:
            return []
        placeholders = ", ".join("?" for _ in owned_ids)
        filters.append(f"project_id IN ({placeholders})")
        params.extend(owned_ids)

    if severity:
        filters.append("severity = ?")
        params.append(severity.value)

    where_sql = f"WHERE {' AND '.join(filters)}" if filters else ""

    query = f"""
        SELECT id, trace_id, span_id, project_id, severity, rule_name as alert_type, message, metadata, created_at
        FROM security_alerts
        {where_sql}
        ORDER BY created_at DESC
        LIMIT ?
    """
    params.append(limit)

    async with db.execute(query, params) as cursor:
        rows = await cursor.fetchall()

    alerts = []
    for row in rows:
        metadata = json.loads(row["metadata"]) if row["metadata"] else {}

        alerts.append(
            SecurityAlertSchema(
                id=row["id"],
                trace_id=row["trace_id"],
                span_id=row["span_id"],
                project_id=row["project_id"],
                severity=SecurityAlertSeverity(row["severity"].lower()), # Normalize case
                alert_type=row["alert_type"],
                message=row["message"],
                metadata=metadata,
                created_at=row["created_at"],
            )
        )

    return alerts
