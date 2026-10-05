# Copyright 2026 Oxly Contributors
# SPDX-License-Identifier: Apache-2.0

"""Background task that drains app.state.span_queue.

For each queued span: cost calculation, security rule scan, persistence to
SQLite (spans/cost_metrics/security_alerts), and live alert broadcast over
the WebSocket. Ported from workers/security_engine.py + cost_calculator.py,
minus their Redis Stream / ClickHouse plumbing.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import sqlite3
import time
import uuid

from fastapi import FastAPI

from api.cost import calculate_cost
from api.db import get_database
from api.rules import anomaly, injection, pii

logger = logging.getLogger("oxly.api")


def _extract_text(span: dict) -> str:
    """Pull LLM prompt/completion/log text out of a span for rule scanning."""
    attrs = span.get("attributes", {}) or {}
    text_content = []

    if "llm.prompts.0.content" in attrs:
        text_content.append(str(attrs["llm.prompts.0.content"]))
    if "llm.completions.0.content" in attrs:
        text_content.append(str(attrs["llm.completions.0.content"]))

    for event in span.get("events", []) or []:
        if isinstance(event, dict) and "attributes" in event:
            msg = event["attributes"].get("message", "")
            if msg:
                text_content.append(str(msg))

    return "\n".join(text_content)


def _build_alerts(span: dict) -> list[dict]:
    """Run injection/PII/anomaly rules on a span. Returns alert dicts."""
    alerts = []
    full_text = _extract_text(span)

    if full_text:
        injection_score = injection.check_injection(full_text)
        if injection_score > 50:
            alerts.append(
                {
                    "rule": "Prompt Injection",
                    "severity": "HIGH" if injection_score > 80 else "MEDIUM",
                    "score": injection_score,
                    "description": "Potential prompt injection detected in LLM input/output",
                    "evidence": full_text[:200],
                }
            )

        pii_types = pii.check_pii(full_text)
        if pii_types:
            alerts.append(
                {
                    "rule": "PII Leak",
                    "severity": "CRITICAL"
                    if ("AWS_KEY" in pii_types or "SSN" in pii_types)
                    else "HIGH",
                    "score": 100.0,
                    "description": f"Sensitive PII detected: {', '.join(pii_types)}",
                    "evidence": "REDACTED",
                }
            )

    for anom in anomaly.check_anomaly(span):
        alerts.append(
            {
                "rule": anom.split(":")[0],
                "severity": "LOW",
                "score": 30.0,
                "description": anom,
                "evidence": str(span.get("duration_ms", "N/A")),
            }
        )

    return alerts


async def _save_span(conn, span: dict) -> None:
    trace_id = span["trace_id"]
    project_id = span.get("project_id", "unknown")
    start_time = span["start_time"]
    end_time = span.get("end_time")
    duration_ms = span.get("duration_ms")
    if duration_ms is None and end_time is not None:
        duration_ms = (end_time - start_time) / 1e6

    # spans.trace_id has a FK into traces — seed the row, then roll up
    # start/end/status across every span seen for this trace so far.
    # Status uses SpanStatus's OK/ERROR (not "complete"/"error") to match
    # the spans table and TraceSchema, which traces.py will validate against
    # once it reads from here instead of ClickHouse.
    span_status = span.get("status", "OK")

    await conn.execute(
        """
        INSERT OR IGNORE INTO traces (trace_id, project_id, start_time, end_time, status)
        VALUES (?, ?, ?, ?, ?)
        """,
        (trace_id, project_id, start_time, end_time, span_status),
    )
    await conn.execute(
        """
        UPDATE traces SET
            start_time = MIN(start_time, ?),
            end_time = CASE
                WHEN end_time IS NULL THEN ?
                WHEN ? IS NULL THEN end_time
                ELSE MAX(end_time, ?)
            END,
            status = CASE WHEN status = 'ERROR' OR ? = 'ERROR' THEN 'ERROR' ELSE 'OK' END
        WHERE trace_id = ?
        """,
        (start_time, end_time, end_time, end_time, span_status, trace_id),
    )
    await conn.execute(
        """
        UPDATE traces SET
            duration_ms = CASE WHEN end_time IS NOT NULL THEN (end_time - start_time) / 1000000.0 ELSE NULL END
        WHERE trace_id = ?
        """,
        (trace_id,),
    )

    attrs = span.get("attributes", {}) or {}
    await conn.execute(
        """
        INSERT OR IGNORE INTO spans (
            span_id, trace_id, parent_span_id, project_id, name,
            start_time, end_time, duration_ms, status, service_name,
            attributes, events
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            span["span_id"],
            trace_id,
            span.get("parent_span_id"),
            project_id,
            span["name"],
            start_time,
            end_time,
            duration_ms,
            span.get("status", "OK"),
            span.get("service_name"),
            json.dumps(attrs),
            json.dumps(span.get("events", [])),
        ),
    )


async def _save_cost(conn, span: dict) -> None:
    cost_row = calculate_cost(span)
    if not cost_row:
        return
    await conn.execute(
        """
        INSERT INTO cost_metrics (
            project_id, model, span_kind, timestamp,
            prompt_tokens, completion_tokens, total_tokens, cost_usd
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            cost_row["project_id"],
            cost_row["model"],
            cost_row["span_kind"],
            cost_row["timestamp"],
            cost_row["prompt_tokens"],
            cost_row["completion_tokens"],
            cost_row["total_tokens"],
            cost_row["cost_usd"],
        ),
    )


async def _save_alerts_and_broadcast(conn, span: dict) -> None:
    alerts = _build_alerts(span)
    if not alerts:
        return

    from api.routes import ws  # lazy import — mirrors main.py's lifespan pattern

    trace_id = span.get("trace_id", "unknown")
    span_id = span.get("span_id", "unknown")
    project_id = span.get("project_id", "unknown")

    for alert in alerts:
        alert_id = str(uuid.uuid4())
        created_at = time.time()

        await conn.execute(
            """
            INSERT INTO security_alerts (
                id, trace_id, span_id, project_id, severity,
                rule_name, message, metadata
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                alert_id,
                trace_id,
                span_id,
                project_id,
                alert["severity"],
                alert["rule"],
                alert["description"],
                json.dumps({"score": alert["score"], "evidence": alert["evidence"]}),
            ),
        )

        await ws.broadcast(
            {
                "type": "alert",
                "data": {
                    "id": alert_id,
                    "project_id": str(project_id),
                    "trace_id": str(trace_id),
                    "span_id": str(span_id),
                    "rule": str(alert["rule"]),
                    "severity": str(alert["severity"]),
                    "description": str(alert["description"]),
                    "created_at": str(created_at),
                },
            }
        )


async def _process_span(span: dict) -> None:
    db = get_database()
    conn = await db.get_connection()
    try:
        await _save_span(conn, span)
        await _save_cost(conn, span)
        await _save_alerts_and_broadcast(conn, span)
        await conn.commit()
    finally:
        await conn.close()


async def _consume_loop(queue: asyncio.Queue) -> None:
    while True:
        span = await queue.get()
        try:
            await _process_span(span)
        except asyncio.CancelledError:
            raise
        except sqlite3.IntegrityError:
            # FK violation on project_id: the project was deleted between
            # the request's API-key check and this span being drained off
            # the queue (revoked/stale key raced a delete). Not a bug --
            # reject it distinctly so it doesn't read as an unknown crash.
            logger.warning(
                "Rejected span %s for project_id=%s: project no longer exists, dropping",
                span.get("span_id", "?"),
                span.get("project_id", "?"),
            )
        except Exception:
            logger.exception("Failed to process span %s, dropping", span.get("span_id", "?"))
        finally:
            queue.task_done()


def start_span_consumer(app: FastAPI) -> None:
    """Start the background task draining app.state.span_queue."""
    app.state.span_consumer_task = asyncio.create_task(_consume_loop(app.state.span_queue))


async def stop_span_consumer(app: FastAPI) -> None:
    """Cancel the background span consumer task."""
    task = getattr(app.state, "span_consumer_task", None)
    if task:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        app.state.span_consumer_task = None
