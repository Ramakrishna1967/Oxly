# Copyright 2026 Oxly Contributors
# SPDX-License-Identifier: Apache-2.0

"""Daily sweep that deletes spans older than 90 days.

Replaces the TTL clause on ClickHouse's spans table
(deploy/clickhouse/init.sql) now that spans live in SQLite, which has
no built-in TTL.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time

from fastapi import FastAPI

from api.db import get_database

logger = logging.getLogger("oxly.api")

SPAN_RETENTION_DAYS = 90
SWEEP_INTERVAL_SECONDS = 24 * 60 * 60


async def _sweep_once() -> None:
    cutoff_ns = time.time_ns() - SPAN_RETENTION_DAYS * 86400 * 1_000_000_000
    db = get_database()
    conn = await db.get_connection()
    try:
        cursor = await conn.execute("DELETE FROM spans WHERE start_time < ?", (cutoff_ns,))
        await conn.commit()
        if cursor.rowcount:
            logger.info(
                "Retention sweep deleted %d spans older than %d days",
                cursor.rowcount,
                SPAN_RETENTION_DAYS,
            )
    finally:
        await conn.close()


async def _sweep_loop() -> None:
    while True:
        try:
            await _sweep_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Retention sweep failed")
        await asyncio.sleep(SWEEP_INTERVAL_SECONDS)


def start_retention_sweep(app: FastAPI) -> None:
    """Start the daily span-retention background task."""
    app.state.retention_task = asyncio.create_task(_sweep_loop())


async def stop_retention_sweep(app: FastAPI) -> None:
    """Cancel the retention sweep task."""
    task = getattr(app.state, "retention_task", None)
    if task:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        app.state.retention_task = None
