# Copyright 2026 Oxly Contributors
# SPDX-License-Identifier: Apache-2.0

"""Trace ingestion endpoint — merged from packages/collector.

Validates API key + payload and puts spans onto the in-process asyncio.Queue
(created in main.py's lifespan, replacing the old Redis "spans.ingest"
stream) for later processing. Cost/security/storage processing is untouched
in this step — only the network endpoint and its auth have moved into the
API process; nothing drains the queue yet.
"""

from __future__ import annotations

import asyncio
import gzip
import io
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from api.apikey_auth import verify_api_key

logger = logging.getLogger("oxly.api")

router = APIRouter()

MAX_PAYLOAD_BYTES = 5 * 1024 * 1024
MAX_DECOMPRESSED_BYTES = 50 * 1024 * 1024


def _validate_span(span_data: object) -> None:
    """Raise ValueError if a span entry isn't a dict, or is missing required fields/types."""
    if not isinstance(span_data, dict):
        raise ValueError(f"Span entry must be a JSON object, got {type(span_data).__name__}")

    required_fields = ["span_id", "trace_id", "name", "start_time", "end_time"]
    missing = [f for f in required_fields if f not in span_data]
    if missing:
        raise ValueError(f"Missing required span fields: {', '.join(missing)}")

    if not isinstance(span_data["span_id"], str) or not span_data["span_id"]:
        raise ValueError("span_id must be a non-empty string")
    if not isinstance(span_data["trace_id"], str) or not span_data["trace_id"]:
        raise ValueError("trace_id must be a non-empty string")
    if not isinstance(span_data["start_time"], (int, float)):
        raise ValueError("start_time must be a number")
    if not isinstance(span_data["end_time"], (int, float)):
        raise ValueError("end_time must be a number")


@router.post("/v1/traces", status_code=202, tags=["ingest"])
async def ingest_traces(
    request: Request,
    project_id: str = Depends(verify_api_key),
):
    """Ingest trace spans from the SDK.

    - Validates payload schema
    - Rejects payloads > 5MB (checks actual body, not Content-Length header)
    - Queues spans on the in-process asyncio.Queue (app.state.span_queue)
    - Returns 202 Accepted on success
    """
    body_bytes = await request.body()
    if len(body_bytes) > MAX_PAYLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Payload too large (max 5MB)")

    content_encoding = request.headers.get("Content-Encoding", "").lower()
    if content_encoding == "gzip":
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(body_bytes)) as gz:
                body_bytes = gz.read(MAX_DECOMPRESSED_BYTES + 1)
        except Exception as exc:
            raise HTTPException(status_code=400, detail="Invalid gzip payload") from exc
        if len(body_bytes) > MAX_DECOMPRESSED_BYTES:
            raise HTTPException(status_code=413, detail="Decompressed payload too large (max 50MB)")

    try:
        body = json.loads(body_bytes)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON") from exc

    spans_input = (
        body.get("spans", [])
        if isinstance(body, dict)
        else (body if isinstance(body, list) else [body])
    )
    if not isinstance(spans_input, list):
        spans_input = [spans_input]

    span_queue: asyncio.Queue | None = getattr(request.app.state, "span_queue", None)
    if span_queue is None:
        raise HTTPException(status_code=503, detail="Ingest pipeline not ready")

    queued_count = 0
    for span_data in spans_input:
        try:
            _validate_span(span_data)
        except ValueError as e:
            logger.warning("Invalid span dropped: %s", e)
            continue

        span_data["project_id"] = project_id
        try:
            span_queue.put_nowait(span_data)
        except asyncio.QueueFull:
            logger.warning("Span queue full (maxsize reached), dropping span")
            continue
        queued_count += 1

    return JSONResponse(
        status_code=202,
        content={
            "status": "accepted",
            "spans_queued": queued_count,
            "project_id": project_id,
        },
    )
