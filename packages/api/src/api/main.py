# Copyright 2026 Oxly Contributors
# SPDX-License-Identifier: Apache-2.0

"""FastAPI application factory with lifespan management and CORS."""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from api.db import get_database
from api.middleware import add_cors_middleware, rate_limit_middleware
from api.retention import start_retention_sweep, stop_retention_sweep
from api.span_consumer import start_span_consumer, stop_span_consumer

logger = logging.getLogger("oxly.api")

# In-process replacement for the old Redis "spans.ingest" stream, drained by
# span_consumer.py (cost calc, security rules, SQLite writes, WS broadcast).
SPAN_QUEUE_MAXSIZE = 10_000

# Dashboard's built `dist/`, copied here by packages/api/Dockerfile's
# frontend build stage. Not present in local dev unless built and copied
# manually — the mount below is skipped when this directory is absent.
STATIC_DIR = Path(os.getenv("DASHBOARD_DIST_DIR", Path(__file__).resolve().parent / "static"))

# Prefixes reserved for the API — unmatched paths under these fall through
# to a normal 404 instead of the SPA fallback's index.html.
_RESERVED_PREFIXES = ("api/", "docs", "redoc", "openapi.json")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager for startup/shutdown events."""
    # Startup
    logger.info("Oxly API starting...")
    db = get_database()
    await db.init_db()
    logger.info("Database initialized successfully")

    # Ingestion pipeline (merged from the collector) — in-process queue, no Redis
    app.state.span_queue = asyncio.Queue(maxsize=SPAN_QUEUE_MAXSIZE)
    start_span_consumer(app)
    start_retention_sweep(app)

    yield

    # Shutdown
    await stop_span_consumer(app)
    await stop_retention_sweep(app)
    logger.info("Oxly API shutting down...")


def create_app() -> FastAPI:
    """FastAPI application factory."""
    app = FastAPI(
        title="Oxly API",
        description="Chrome DevTools for AI Agents  Observability API",
        version="0.1.0-alpha",
        lifespan=lifespan,
    )

    # Add CORS middleware
    add_cors_middleware(app)

    # Add rate limiting middleware
    app.middleware("http")(rate_limit_middleware)

    # Root endpoint
    @app.get("/", tags=["system"])
    async def root():
        """Root endpoint with API info."""
        return {
            "name": "Oxly API",
            "version": "0.1.0-alpha",
            "docs": "/docs",
            "health": "/api/v1/health",
        }

    # Import and include routers
    from api.routes import analytics, auth, health, ingest, projects, security, spans, traces, ws

    app.include_router(traces.router, prefix="/api/v1", tags=["traces"])
    app.include_router(spans.router, prefix="/api/v1", tags=["spans"])
    app.include_router(projects.router, prefix="/api/v1", tags=["projects"])
    app.include_router(security.router, prefix="/api/v1", tags=["security"])
    app.include_router(analytics.router, prefix="/api/v1", tags=["analytics"])
    app.include_router(auth.router, prefix="/api/v1", tags=["auth"])
    app.include_router(health.router, prefix="/api/v1", tags=["system"])
    app.include_router(ws.router, tags=["websocket"])
    # No /api/v1 prefix — SDK talks to /v1/traces directly, same as the
    # standalone collector did.
    app.include_router(ingest.router, tags=["ingest"])

    # Serve the dashboard's built dist/ — replaces the nginx gateway's static
    # serving + SPA fallback (try_files ... /index.html).
    if STATIC_DIR.is_dir():
        assets_dir = STATIC_DIR / "assets"
        if assets_dir.is_dir():
            app.mount("/assets", StaticFiles(directory=assets_dir), name="dashboard-assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        async def spa_fallback(full_path: str):
            if full_path.startswith(_RESERVED_PREFIXES):
                raise HTTPException(status_code=404)
            candidate = STATIC_DIR / full_path
            if candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(STATIC_DIR / "index.html")

    return app


# Create app instance
app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "api.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info",
    )
