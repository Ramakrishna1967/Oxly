# Copyright 2026 Oxly Contributors
# SPDX-License-Identifier: Apache-2.0

"""Analytics routes  cost tracking and timeseries data."""

from __future__ import annotations

from typing import Literal

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Query

from api.db import get_db
from api.dependencies import get_current_active_user, get_user_project_ids, verify_project_ownership

router = APIRouter()


@router.get("/analytics/cost")
async def get_cost_timeseries(
    project_id: str | None = Query(None),
    interval: Literal["hour", "day", "week"] = Query("day"),
    start_date: int | None = Query(None, description="Unix timestamp in seconds"),
    end_date: int | None = Query(None, description="Unix timestamp in seconds"),
    db: aiosqlite.Connection = Depends(get_db),
    current_user: dict = Depends(get_current_active_user),
):
    """Get cost metrics over time.

    Groups by time interval and sums cost_usd. cost_metrics.timestamp is
    stored as Unix seconds (see api/cost.py), so bucketing uses strftime
    with the 'unixepoch' modifier.
    """
    where_clauses = []
    params: list = []

    if project_id:
        if not await verify_project_ownership(db, current_user["id"], project_id):
            raise HTTPException(status_code=403, detail="Project not found")
        where_clauses.append("project_id = ?")
        params.append(project_id)
    else:
        owned_ids = await get_user_project_ids(db, current_user["id"])
        if not owned_ids:
            return {"interval": interval, "project_id": None, "data": []}
        placeholders = ", ".join("?" for _ in owned_ids)
        where_clauses.append(f"project_id IN ({placeholders})")
        params.extend(owned_ids)

    if start_date:
        where_clauses.append("timestamp >= ?")
        params.append(start_date)

    if end_date:
        where_clauses.append("timestamp <= ?")
        params.append(end_date)

    where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

    # Time bucket expression, keyed off Unix-seconds timestamp column
    if interval == "hour":
        time_func = "strftime('%Y-%m-%dT%H:00:00', timestamp, 'unixepoch')"
    elif interval == "day":
        time_func = "strftime('%Y-%m-%dT00:00:00', timestamp, 'unixepoch')"
    else:
        # Week start = Sunday, to mirror ClickHouse's default toStartOfWeek
        time_func = "strftime('%Y-%m-%dT00:00:00', timestamp, 'unixepoch', 'weekday 0', '-6 days')"

    query = f"""
        SELECT
            {time_func} AS time_bucket,
            model,
            SUM(prompt_tokens) as prompt_tokens,
            SUM(completion_tokens) as completion_tokens,
            SUM(total_tokens) as total_tokens,
            SUM(cost_usd) as cost_usd
        FROM cost_metrics
        {where_sql}
        GROUP BY time_bucket, model
        ORDER BY time_bucket ASC
    """

    try:
        async with db.execute(query, params) as cursor:
            rows = await cursor.fetchall()
    except Exception:
        # If table doesn't exist or other error, return empty
        return {"data": []}

    # Format for frontend
    # Recharts expects array of objects. We might want to group by timestamp.
    # [{timestamp: ..., "gpt-4": 1.2, "claude-3": 0.5}, ...]

    processed = {}

    for row in rows:
        ts = row["time_bucket"]

        if ts not in processed:
            processed[ts] = {"timestamp": ts, "total_cost": 0, "prompt_tokens": 0, "completion_tokens": 0}

        model = row["model"]
        cost = row["cost_usd"]

        processed[ts][model] = cost
        processed[ts]["total_cost"] += cost
        processed[ts]["prompt_tokens"] += row["prompt_tokens"] or 0
        processed[ts]["completion_tokens"] += row["completion_tokens"] or 0

    results = list(processed.values())

    return {
        "interval": interval,
        "project_id": project_id,
        "data": results,
    }
