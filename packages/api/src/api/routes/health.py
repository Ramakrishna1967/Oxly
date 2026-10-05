# Copyright 2026 Oxly Contributors
# SPDX-License-Identifier: Apache-2.0

"""Health check endpoint with dependency status reporting."""

from __future__ import annotations

import asyncio
import time

from fastapi import APIRouter, Request

from api.config import settings
from api.db import get_database

router = APIRouter()

_START_TIME = time.time()


def _task_alive(task: asyncio.Task | None) -> bool:
    """Return True if a background task exists and hasn't finished."""
    return task is not None and not task.done()


@router.get("/health")
async def health_check(request: Request):
    """Check connectivity to the API's dependencies (SQLite + in-process pipeline).

    Returns individual service status and overall health. Service keys are
    consumed by the dashboard's System Status panel.
    """
    # SQLite (API's own database)
    sqlite_ok = False
    try:
        db = get_database()
        conn = await db.get_connection()
        await conn.execute("SELECT 1")
        sqlite_ok = True
        await conn.close()
    except Exception:
        pass

    # Ingest queue depth + consumer liveness (both in-process; lifespan sets
    # them, so absence just means lifespan hasn't run, e.g. in tests).
    queue_status = "operational"
    try:
        span_queue: asyncio.Queue = request.app.state.span_queue
        backlog = span_queue.qsize()
        consumer_ok = _task_alive(getattr(request.app.state, "span_consumer_task", None))
        if not consumer_ok or backlog >= span_queue.maxsize * 0.9:
            queue_status = "degraded"
    except Exception:
        pass

    # Retention sweep liveness
    retention_ok = _task_alive(getattr(request.app.state, "retention_task", None))
    # No app.state outside lifespan (tests) — don't report red for that.
    has_lifespan = hasattr(request.app.state, "span_queue")
    retention_status = "operational" if retention_ok or not has_lifespan else "down"

    services = {
        "api": "operational",
        "sqlite": "operational" if sqlite_ok else "down",
        "queue": queue_status,
        "retention": retention_status,
    }
    overall = "healthy" if all(s != "down" for s in services.values()) else "down"

    return {
        "status": overall,
        "version": "0.1.0-alpha",
        "uptime_seconds": round(time.time() - _START_TIME, 1),
        "environment": settings.ENVIRONMENT,
        "services": services,
    }
