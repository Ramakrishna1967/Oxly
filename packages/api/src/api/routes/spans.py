# Copyright 2026 Oxly Contributors
# SPDX-License-Identifier: Apache-2.0

"""Span routes  individual span detail."""

from __future__ import annotations

import json

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException

from api.db import get_db
from api.dependencies import get_current_active_user, verify_project_ownership
from api.schemas import SpanSchema, SpanStatus

router = APIRouter()


@router.get("/spans/{span_id}", response_model=SpanSchema)
async def get_span_detail(
    span_id: str,
    db: aiosqlite.Connection = Depends(get_db),
    current_user: dict = Depends(get_current_active_user),
):
    """Get individual span detail by ID."""
    async with db.execute("SELECT * FROM spans WHERE span_id = ?", (span_id,)) as cursor:
        row = await cursor.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Span not found")

    if not await verify_project_ownership(db, current_user["id"], row["project_id"]):
        raise HTTPException(status_code=403, detail="Span not found")

    end_ns = row["end_time"] if row["end_time"] is not None else row["start_time"]

    return SpanSchema(
        span_id=row["span_id"],
        trace_id=row["trace_id"],
        parent_span_id=row["parent_span_id"] or None,
        name=row["name"],
        start_time=row["start_time"],
        end_time=end_ns,
        duration_ms=round(row["duration_ms"]) if row["duration_ms"] is not None else 0,
        status=SpanStatus(row["status"]),
        service_name=row["service_name"] or "default",
        attributes=json.loads(row["attributes"]) if row["attributes"] else {},
        events=json.loads(row["events"]) if row["events"] else [],
        project_id=row["project_id"],
    )
