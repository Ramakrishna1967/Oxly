# Copyright 2026 Oxly Contributors
# SPDX-License-Identifier: Apache-2.0

"""Trace routes — list traces, get trace detail with full span tree."""

from __future__ import annotations

import json
from typing import Any

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Query

from api.db import get_db
from api.dependencies import get_current_active_user, get_user_project_ids, verify_project_ownership
from api.schemas import SpanSchema, SpanStatus, TraceDetailSchema

router = APIRouter()


@router.get("/traces", response_model=dict)
async def list_traces(
    project_id: str | None = Query(None),
    status: str | None = Query(None),
    start_date: int | None = Query(None, description="Unix timestamp in nanoseconds"),
    end_date: int | None = Query(None, description="Unix timestamp in nanoseconds"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    db: aiosqlite.Connection = Depends(get_db),
    current_user: dict = Depends(get_current_active_user),
):
    """List traces with pagination and filters, from the traces table."""
    filters = []
    params: list = []

    if project_id:
        if not await verify_project_ownership(db, current_user["id"], project_id):
            raise HTTPException(status_code=403, detail="Project not found")
        filters.append("t.project_id = ?")
        params.append(project_id)
    else:
        owned_ids = await get_user_project_ids(db, current_user["id"])
        if not owned_ids:
            return {"items": [], "total": 0, "page": page, "page_size": page_size, "total_pages": 0}
        placeholders = ", ".join("?" for _ in owned_ids)
        filters.append(f"t.project_id IN ({placeholders})")
        params.extend(owned_ids)

    if status:
        filters.append("t.status = ?")
        params.append(status)

    # start_date/end_date and traces.start_time are both Unix nanoseconds
    if start_date:
        filters.append("t.start_time >= ?")
        params.append(start_date)

    if end_date:
        filters.append("t.start_time <= ?")
        params.append(end_date)

    where_sql = f"WHERE {' AND '.join(filters)}" if filters else ""

    count_query = f"SELECT COUNT(*) FROM traces t {where_sql}"
    async with db.execute(count_query, params) as cursor:
        count_row = await cursor.fetchone()
    total = count_row[0] if count_row else 0

    offset = (page - 1) * page_size
    query = f"""
        SELECT
            t.trace_id, t.project_id, t.start_time, t.end_time, t.status,
            (SELECT COUNT(*) FROM spans s WHERE s.trace_id = t.trace_id) as span_count
        FROM traces t
        {where_sql}
        ORDER BY t.start_time DESC
        LIMIT ? OFFSET ?
    """
    async with db.execute(query, params + [page_size, offset]) as cursor:
        rows = await cursor.fetchall()

    traces = []
    for row in rows:
        start_ns = row["start_time"]
        end_ns = row["end_time"] if row["end_time"] is not None else start_ns

        traces.append(
            {
                "trace_id": row["trace_id"],
                "project_id": row["project_id"],
                "start_time": start_ns,
                "end_time": end_ns,
                "duration_ms": (end_ns - start_ns) / 1e6,
                "status": row["status"],
                "span_count": row["span_count"],
            }
        )

    return {
        "items": traces,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": (total + page_size - 1) // page_size,
    }


@router.get("/traces/{trace_id}", response_model=TraceDetailSchema)
async def get_trace_detail(
    trace_id: str,
    db: aiosqlite.Connection = Depends(get_db),
    current_user: dict = Depends(get_current_active_user),
):
    """Get full trace detail with all spans."""
    query = "SELECT * FROM spans WHERE trace_id = ? ORDER BY start_time ASC"
    async with db.execute(query, (trace_id,)) as cursor:
        span_rows: list[Any] = list(await cursor.fetchall())

    if not span_rows:
        raise HTTPException(status_code=404, detail="Trace not found")

    trace_project_id = span_rows[0]["project_id"]
    if not await verify_project_ownership(db, current_user["id"], trace_project_id):
        raise HTTPException(status_code=403, detail="Trace not found")

    spans = []
    min_start: int | None = None
    max_end: int | None = None
    final_status = SpanStatus.OK

    for row in span_rows:
        start_ns = row["start_time"]
        end_ns = row["end_time"] if row["end_time"] is not None else start_ns + 1_000_000

        if min_start is None or start_ns < min_start:
            min_start = start_ns
        if max_end is None or end_ns > max_end:
            max_end = end_ns
        if row["status"] == "ERROR":
            final_status = SpanStatus.ERROR

        spans.append(
            SpanSchema(
                span_id=row["span_id"],
                trace_id=row["trace_id"],
                parent_span_id=row["parent_span_id"] or None,
                name=row["name"],
                start_time=start_ns,
                end_time=end_ns,
                duration_ms=round(row["duration_ms"]) if row["duration_ms"] is not None else 0,
                status=SpanStatus(row["status"]),
                service_name=row["service_name"] or "default",
                attributes=json.loads(row["attributes"]) if row["attributes"] else {},
                events=json.loads(row["events"]) if row["events"] else [],
                project_id=row["project_id"],
            )
        )

    return TraceDetailSchema(
        trace_id=trace_id,
        project_id=span_rows[0]["project_id"],
        start_time=min_start if min_start is not None else 0,
        end_time=max_end,
        duration_ms=round((max_end - min_start) / 1e6) if min_start and max_end else 0,
        status=final_status,
        spans=spans,
    )


@router.get("/traces/{trace_id}/replay", response_model=list[SpanSchema])
async def get_trace_replay(
    trace_id: str,
    db: aiosqlite.Connection = Depends(get_db),
    current_user: dict = Depends(get_current_active_user),
):
    """Get ordered spans for a trace to support Time Machine replay."""
    query = "SELECT * FROM spans WHERE trace_id = ? ORDER BY start_time ASC"
    async with db.execute(query, (trace_id,)) as cursor:
        span_rows: list[Any] = list(await cursor.fetchall())

    if not span_rows:
        raise HTTPException(status_code=404, detail="Trace not found or contains no spans")

    trace_project_id = span_rows[0]["project_id"]
    if not await verify_project_ownership(db, current_user["id"], trace_project_id):
        raise HTTPException(status_code=403, detail="Trace not found")

    spans = []
    for row in span_rows:
        start_ns = row["start_time"]
        end_ns = row["end_time"] if row["end_time"] is not None else start_ns

        spans.append(
            SpanSchema(
                span_id=row["span_id"],
                trace_id=row["trace_id"],
                parent_span_id=row["parent_span_id"] or None,
                name=row["name"],
                start_time=start_ns,
                end_time=end_ns,
                duration_ms=round(row["duration_ms"]) if row["duration_ms"] is not None else 0,
                status=SpanStatus(row["status"]),
                service_name=row["service_name"] or "default",
                attributes=json.loads(row["attributes"]) if row["attributes"] else {},
                events=json.loads(row["events"]) if row["events"] else [],
                project_id=row["project_id"],
            )
        )

    return spans
