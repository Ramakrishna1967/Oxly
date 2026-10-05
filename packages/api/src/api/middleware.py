# Copyright 2026 Oxly Contributors
# SPDX-License-Identifier: Apache-2.0

"""Middleware for CORS and rate limiting."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections import defaultdict
from collections.abc import Callable

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

logger = logging.getLogger("oxly.api.middleware")

# Simple in-memory rate limiter with bounded memory
_rate_limit_store: dict[str, list[float]] = defaultdict(list)
_rate_limit_lock = asyncio.Lock()
_RATE_LIMIT_REQUESTS = 100
_RATE_LIMIT_WINDOW = 60  # seconds
_EVICTION_COUNTER = 0
_EVICTION_INTERVAL = 100  # Evict stale IPs every N requests
_MAX_TRACKED_IPS = 10000  # SECURITY: Prevent memory exhaustion from DDoS
_RATE_LIMIT_CLEANUP_INTERVAL = 300  # Cleanup old IPs every 5 minutes (in seconds)
_last_cleanup_time = time.time()


def add_cors_middleware(app: FastAPI) -> None:
    """Add CORS middleware with configurable origins via CORS_ORIGINS env var."""
    # Included http://localhost (port 80) for gateway access
    default_origins = (
        "http://localhost,http://127.0.0.1,"
        "http://localhost:5173,http://127.0.0.1:5173,"
        "http://localhost:3000,http://localhost:80"
    )
    origins = os.getenv("CORS_ORIGINS", default_origins).split(",")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in origins],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


async def rate_limit_middleware(request: Request, call_next: Callable):
    """Simple rate limiting middleware (100 req/min per IP).

    In production, use Redis or similar for distributed rate limiting.
    Includes memory bounds to prevent DDoS attacks.
    """
    global _EVICTION_COUNTER, _last_cleanup_time
    client_ip = request.client.host if request.client else "unknown"
    current_time = time.time()

    async with _rate_limit_lock:
        # SECURITY: Periodic cleanup to prevent memory exhaustion
        if current_time - _last_cleanup_time > _RATE_LIMIT_CLEANUP_INTERVAL:
            stale_ips = [
                ip
                for ip, timestamps in _rate_limit_store.items()
                if not timestamps or (current_time - max(timestamps)) > _RATE_LIMIT_WINDOW
            ]
            for ip in stale_ips:
                del _rate_limit_store[ip]
            _last_cleanup_time = current_time
            if len(stale_ips) > 0:
                logger.debug(f"Rate limiter cleanup: removed {len(stale_ips)} stale IPs")

        # SECURITY: Check max tracked IPs to prevent memory exhaustion
        if len(_rate_limit_store) >= _MAX_TRACKED_IPS and client_ip not in _rate_limit_store:
            # Evict oldest entry to make room
            oldest_ip = min(
                _rate_limit_store.keys(),
                key=lambda k: max(_rate_limit_store[k]) if _rate_limit_store[k] else 0,
            )
            del _rate_limit_store[oldest_ip]
            logger.warning(f"Rate limiter at capacity, evicted IP: {oldest_ip}")

        # Clean old entries for this IP
        _rate_limit_store[client_ip] = [
            timestamp
            for timestamp in _rate_limit_store.get(client_ip, [])
            if current_time - timestamp < _RATE_LIMIT_WINDOW
        ]

        # Periodically evict stale IPs (per-request cleanup)
        _EVICTION_COUNTER += 1
        if _EVICTION_COUNTER >= _EVICTION_INTERVAL:
            _EVICTION_COUNTER = 0
            stale_ips = [
                ip
                for ip, timestamps in _rate_limit_store.items()
                if not timestamps or (current_time - max(timestamps)) > _RATE_LIMIT_WINDOW
            ]
            for ip in stale_ips:
                del _rate_limit_store[ip]

        # Check rate limit
        if len(_rate_limit_store[client_ip]) >= _RATE_LIMIT_REQUESTS:
            return JSONResponse(
                status_code=429,
                content={
                    "detail": "Rate limit exceeded. Try again later.",
                    "retry_after": _RATE_LIMIT_WINDOW,
                },
            )

        # Record request
        _rate_limit_store[client_ip].append(current_time)

    response = await call_next(request)
    return response
