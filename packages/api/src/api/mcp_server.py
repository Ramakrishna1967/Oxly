# Copyright 2026 Oxly Contributors
# SPDX-License-Identifier: Apache-2.0

"""Oxly MCP server (Phase 0 scaffold).

Exposes a Streamable HTTP MCP endpoint with an empty tool list.
Phase 1 will register project-scoped tools here, reusing:

- ``api.db.get_database`` / ``api.db.get_db`` for storage access
- ``api.dependencies.get_user_project_ids`` /
  ``api.dependencies.verify_project_ownership`` for auth scoping

Mounting (see ``api.main.create_app``):
    from api.mcp_server import mcp, mcp_app
    app.mount("/", mcp_app)  # keeps the endpoint at /mcp (default path)
    # host lifespan MUST enter ``mcp.session_manager.run()``

The ``session_manager`` only exists after ``streamable_http_app()`` is
called, which is why ``mcp_app`` is built at module import time.
"""

from __future__ import annotations

import logging

from api.db import get_database  # noqa: F401  (Phase 1 wiring point, re-exported)
from api.dependencies import (  # noqa: F401  (Phase 1 wiring point, re-exported)
    get_user_project_ids,
    verify_project_ownership,
)

logger = logging.getLogger("oxly.api.mcp")

__all__ = [
    "build_mcp_server",
    "get_database",
    "get_user_project_ids",
    "mcp",
    "mcp_app",
    "verify_project_ownership",
]

try:
    from mcp.server import MCPServer
except Exception as exc:  # pragma: no cover - import-time guard for minimal envs
    raise RuntimeError(
        "mcp package is required for Phase 0 (pip install mcp). See packages/api/pyproject.toml."
    ) from exc


def build_mcp_server() -> MCPServer:
    """Build a fresh Oxly MCPServer (Phase 0: no tools).

    A fresh instance per FastAPI app is required because
    ``StreamableHTTPSessionManager.run()`` can only be entered once per
    server instance — sharing one module-level server across multiple
    ``create_app()`` calls (tests, reload) breaks the second lifespan.
    """
    server = MCPServer(
        name="oxly",
        title="Oxly",
        description="Chrome DevTools for AI Agents — observability (Phase 0 scaffold, no tools yet).",
        instructions=(
            "Phase 0 scaffold. No tools are registered yet; initialize must succeed "
            "and tools/list returns an empty list."
        ),
        version="0.1.0-alpha",
    )
    # Phase 1 hook example (not registered yet):
    # @server.tool()
    # async def query_traces(project_id: str, ...) -> ...:
    #     async with (await get_database().get_connection()) as db:
    #         if not await verify_project_ownership(db, user_id, project_id):
    #             raise ValueError("unknown project")
    #         ...
    return server


# Module-level singleton for simple imports / standalone serving.
# main.create_app() builds a FRESH instance per app (see build_mcp_server)
# so tests and reloads each get a runnable session manager.
mcp = build_mcp_server()

# Build the Starlette sub-app now so `mcp.session_manager` exists for the
# host lifespan in main.py. Default path "/mcp" + Mount("/", ...) => /mcp.
mcp_app = mcp.streamable_http_app()

logger.info("Oxly MCP scaffold initialised (Phase 0, empty tool list)")
