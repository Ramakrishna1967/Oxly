# Copyright 2026 Oxly Contributors
# SPDX-License-Identifier: Apache-2.0

"""FastAPI application factory with lifespan management and CORS."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from api.db import get_database
from api.middleware import (
    add_cors_middleware,
    add_security_headers_middleware,
    rate_limit_middleware,
)
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

# Phase 0 plugin shell (packages/plugin/). Overridable via PLUGIN_DIR for tests.
# main.py lives at packages/api/src/api/main.py -> repo root is parents[4].
_REPO_ROOT = Path(__file__).resolve().parents[4]
PLUGIN_DIR = Path(os.getenv("PLUGIN_DIR", _REPO_ROOT / "packages" / "plugin"))
PLUGIN_UI_DIR = PLUGIN_DIR / "ui"

# Prefixes reserved for the API — unmatched paths under these fall through
# to a normal 404 instead of the SPA fallback's index.html.
# "mcp" (Streamable HTTP), "plugin" (manifest + UI), ".well-known" (discovery)
# must never be swallowed by the SPA fallback.
_RESERVED_PREFIXES = (
    "api/",
    "docs",
    "redoc",
    "openapi.json",
    "mcp",
    "plugin",
    ".well-known",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager for startup/shutdown events."""
    # Startup
    logger.info("Oxly API starting...")
    db = get_database()
    await db.init_db()
    logger.info("Database initialized successfully")

    # Phase 0: MCP session manager MUST run for the host app's lifetime.
    # Mounting disables the sub-app's built-in lifespan, so we enter it here.
    # The manager is built per-app in create_app() and stored on app.state.
    async with contextlib.AsyncExitStack() as stack:
        manager = getattr(app.state, "mcp_session_manager", None)
        if manager is not None:
            try:
                await stack.enter_async_context(manager.run())
                logger.info("MCP session manager started at /mcp")
            except Exception as exc:  # pragma: no cover - degraded mode
                logger.warning("MCP session manager not started: %s", exc)
        else:
            logger.warning("MCP session manager missing on app.state (MCP disabled?)")

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

    # Add CORS middleware (exposes Mcp-Session-Id for Streamable HTTP)
    add_cors_middleware(app)

    # Content-Security-Policy allow-listing the stable plugin UI origin
    add_security_headers_middleware(app)

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

    # Phase 0: plugin shell — manifest, client config, discovery, UI assets.
    # UI: local dev serves packages/plugin/ui/ at /plugin/ui/ (html=True).
    # Prod hosts the same files at the stable HTTPS origin in PLUGIN_UI_ORIGIN,
    # allow-listed by the Content-Security-Policy above.
    # NOTE: these specific routes/mounts MUST be registered BEFORE the
    # catch-all Mount("/", mcp_app) below — Starlette matches in order and
    # Mount("/") matches every path, so anything after it is unreachable.
    plugin_json = PLUGIN_DIR / "plugin.json"
    mcp_json = PLUGIN_DIR / "mcp.json"
    if plugin_json.is_file():

        @app.get("/plugin/plugin.json", include_in_schema=False)
        async def plugin_manifest():
            return FileResponse(plugin_json, media_type="application/json")

        @app.get("/.well-known/oxly-plugin.json", include_in_schema=False)
        async def plugin_well_known():
            return FileResponse(plugin_json, media_type="application/json")

    if mcp_json.is_file():

        @app.get("/plugin/mcp.json", include_in_schema=False)
        async def plugin_mcp_config():
            return FileResponse(mcp_json, media_type="application/json")

    if PLUGIN_UI_DIR.is_dir():
        app.mount(
            "/plugin/ui",
            StaticFiles(directory=PLUGIN_UI_DIR, html=True),
            name="plugin-ui",
        )

    # Phase 0: live MCP Streamable HTTP endpoint at /mcp.
    # Mount("/", ...) + default streamable_http_path "/mcp" keeps the public
    # path at /mcp. This mount matches EVERY unmatched path, so it must be
    # registered AFTER all specific routes above but BEFORE the dashboard SPA
    # fallback. Lifespan is owned by the host app (see lifespan() above).
    # A FRESH server is built per app because the session manager can only
    # run() once per instance (tests + reload would break on a singleton).
    try:
        from api.mcp_server import build_mcp_server

        _mcp = build_mcp_server()
        _mcp_app = _mcp.streamable_http_app()
        app.state.mcp_session_manager = _mcp.session_manager
        app.state.mcp_server = _mcp
        app.mount("/", _mcp_app, name="mcp")
        logger.info("Mounted MCP Streamable HTTP app at /mcp")
    except Exception as exc:  # pragma: no cover - degraded mode without MCP
        logger.warning("MCP app not mounted: %s", exc)

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
