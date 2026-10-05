# Copyright 2026 Oxly Contributors
# SPDX-License-Identifier: Apache-2.0

"""WebSocket endpoint for real-time trace streaming and alerts."""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()
logger = logging.getLogger("oxly.api")

# Connected WebSocket clients
_connections: set[WebSocket] = set()


async def broadcast(message: dict) -> None:
    """Broadcast a message to all connected WebSocket clients."""
    if not _connections:
        return

    data = json.dumps(message)
    disconnected = set()

    for ws in list(_connections):
        try:
            await ws.send_text(data)
        except Exception:
            disconnected.add(ws)

    # Clean up disconnected clients
    _connections.difference_update(disconnected)


@router.websocket("/ws/traces")
async def ws_trace_feed(websocket: WebSocket, token: str | None = None):
    """WebSocket endpoint with JWT authentication.

    Alerts are pushed in-process via broadcast() from span_consumer.py.

    Args:
        token: JWT token can be passed as query parameter ?token=xxx
    """
    # Validate JWT token before accepting connection
    from jose import JWTError, jwt

    from api.config import settings

    try:
        # Try to get token from query param or subprotocol
        if not token:
            token = websocket.query_params.get("token")

        if not token and not settings.DEMO_MODE:
            await websocket.close(code=1008, reason="Authentication required")
            return

        if token:
            try:
                payload = jwt.decode(
                    token, settings.JWT_SECRET_KEY, algorithms=[settings.ALGORITHM]
                )
                user_id = payload.get("sub")
                if not user_id and not settings.DEMO_MODE:
                    await websocket.close(code=1008, reason="Invalid token")
                    return
            except JWTError:
                if not settings.DEMO_MODE:
                    await websocket.close(code=1008, reason="Invalid token")
                    return
        elif not settings.DEMO_MODE:
            await websocket.close(code=1008, reason="Authentication required")
            return

    except Exception:
        if not settings.DEMO_MODE:
            await websocket.close(code=1011, reason="Internal error")
            return

    await websocket.accept()
    _connections.add(websocket)
    logger.info("WebSocket client connected. Total: %d", len(_connections))

    try:
        while True:
            # Listen for ping/filters
            try:
                data = await asyncio.wait_for(websocket.receive_text(), timeout=30.0)

                # --- HIGH-3 FIX: Reject oversized messages ---
                if len(data) > 4096:
                    await websocket.close(code=1009, reason="Message too large")
                    break

                try:
                    message = json.loads(data)
                except json.JSONDecodeError:
                    logger.warning("Received invalid JSON on websocket")
                    # Send an error back instead of closing
                    await websocket.send_text(
                        json.dumps({"type": "error", "message": "Invalid JSON format"})
                    )
                    continue

                if message.get("type") == "ping":
                    await websocket.send_text(json.dumps({"type": "pong"}))
            except asyncio.TimeoutError:
                # Send keepalive ping
                await websocket.send_text(json.dumps({"type": "ping"}))
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error("WebSocket error: %s", type(e).__name__)
    finally:
        _connections.discard(websocket)
        logger.info("WebSocket client disconnected. Total: %d", len(_connections))
