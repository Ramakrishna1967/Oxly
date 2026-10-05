# Copyright 2026 Oxly Contributors
# SPDX-License-Identifier: Apache-2.0

"""API key auth for SDK trace ingestion (X-API-Key header).

Separate audience from the dashboard's JWT auth in dependencies.py: SDK/agent
clients get a long-lived static key with no session/expiry concept, verified
against `projects.api_key_hash`. Merged from packages/collector/auth.py.

Two-tier lookup: SHA-256 fast hash for O(1) cache checks on repeat keys,
falling back to a full pbkdf2 scan of the projects table on first use of a
given key (offloaded to the thread pool so pbkdf2 doesn't block the event loop).
"""

from __future__ import annotations

import asyncio
import hashlib

import aiosqlite
from fastapi import Depends, Header, HTTPException
from passlib.hash import pbkdf2_sha256 as pwd_context

from api.db import get_db

_verified_keys_cache: dict[str, str] = {}
_CACHE_MAX_SIZE = 1000


def _fast_hash(api_key: str) -> str:
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()


async def verify_api_key(
    x_api_key: str = Header(..., alias="X-API-Key"),
    db: aiosqlite.Connection = Depends(get_db),
) -> str:
    """Verify API key and return project_id. Raises 401 if invalid."""
    if not x_api_key or not x_api_key.startswith("ak_"):
        raise HTTPException(status_code=401, detail="Invalid API key format")

    fast_key = _fast_hash(x_api_key)
    cached_project_id = _verified_keys_cache.get(fast_key)
    if cached_project_id is not None:
        return cached_project_id

    async with db.execute("SELECT id, api_key_hash FROM projects") as cursor:
        rows = await cursor.fetchall()

    loop = asyncio.get_event_loop()
    for row in rows:
        is_valid = await loop.run_in_executor(
            None, pwd_context.verify, x_api_key, row["api_key_hash"]
        )
        if is_valid:
            project_id = row["id"]
            if len(_verified_keys_cache) < _CACHE_MAX_SIZE:
                _verified_keys_cache[fast_key] = project_id
            return project_id

    raise HTTPException(status_code=401, detail="Invalid API key")


def invalidate_key_cache(api_key: str | None = None) -> None:
    """Invalidate the key cache (call on project deletion/rotation)."""
    if api_key:
        _verified_keys_cache.pop(_fast_hash(api_key), None)
    else:
        _verified_keys_cache.clear()
