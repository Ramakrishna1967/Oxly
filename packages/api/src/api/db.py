# Copyright 2026 Oxly Contributors
# SPDX-License-Identifier: Apache-2.0

"""Async SQLite database connection manager using aiosqlite.

Provides async context manager for database connections and ensures
schema is initialized on first run.
"""

from __future__ import annotations

import logging
import re
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import aiosqlite

from api.config import settings

logger = logging.getLogger("oxly.api")

# Default database location logic

# Matches Windows absolute paths with an optional leading slash:
# "C:/data/oxly.db" or "/C:/data/oxly.db" (the latter comes from
# "sqlite+aiosqlite:///C:/data/oxly.db" URL parsing).
_WINDOWS_ABS_PATH = re.compile(r"^/?[A-Za-z]:[\\/]")


def _get_default_db_path() -> Path:
    """Resolve settings.DATABASE_URL to a filesystem path.

    Accepts full "sqlite+aiosqlite://..." URLs (POSIX absolute, Windows
    absolute, or relative) as well as bare paths like "oxly.db".
    Relative paths resolve against the current working directory (which is
    /app in the Docker image, so container behavior is unchanged).
    """
    db_url = (settings.DATABASE_URL or "").strip()
    if "://" in db_url:
        path_part = unquote(db_url.partition("://")[2])
    elif db_url:
        path_part = db_url
    else:
        path_part = "oxly.db"

    if _WINDOWS_ABS_PATH.match(path_part):
        return Path(path_part.lstrip("/"))
    candidate = Path(path_part)
    if candidate.is_absolute():
        return candidate
    return Path.cwd() / candidate


_DEFAULT_DB_PATH = _get_default_db_path()


class Database:
    """Async SQLite database manager with schema migration support."""

    _LATEST_VERSION = 1

    # Migration definitions: version -> (name, SQL statements)
    _MIGRATIONS: dict[int, tuple[str, list[str]]] = {
        1: (
            "initial_schema",
            [
                # All CREATE TABLE IF NOT EXISTS statements are idempotent,
                # so running them as a migration is safe for both fresh and existing DBs
            ],
        ),
    }

    def __init__(self, db_path: str | Path = _DEFAULT_DB_PATH):
        self.db_path = str(db_path)
        self._initialized = False

    async def _apply_migrations(self, conn: aiosqlite.Connection, from_version: int) -> None:
        """Apply pending schema migrations sequentially."""
        for version in range(from_version + 1, self._LATEST_VERSION + 1):
            if version in self._MIGRATIONS:
                name, statements = self._MIGRATIONS[version]
                for stmt in statements:
                    await conn.execute(stmt)
                await conn.execute(
                    "INSERT INTO _schema_version (version, name) VALUES (?, ?)",
                    (version, name),
                )
                await conn.commit()
                logger.info(f"Applied schema migration v{version}: {name}")

    async def init_db(self) -> None:
        """Initialize database schema on first run with version tracking."""
        if self._initialized:
            return

        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.db_path) as conn:
            # Enable WAL mode for better concurrency
            await conn.execute("PRAGMA journal_mode=WAL")
            await conn.execute("PRAGMA synchronous=NORMAL")
            await conn.execute("PRAGMA busy_timeout=5000")
            await conn.execute("PRAGMA foreign_keys=ON")

            # Schema version tracking
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS _schema_version (
                    version INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Check current schema version
            cursor = await conn.execute("SELECT MAX(version) FROM _schema_version")
            row: Any = await cursor.fetchone()
            current_version = row[0] if row[0] is not None else 0

            if current_version == 0:
                # Fresh install  apply all migrations
                await self._apply_migrations(conn, from_version=0)
            elif current_version < self._LATEST_VERSION:
                # Existing DB  apply pending migrations
                logger.info(f"Upgrading schema from v{current_version} to v{self._LATEST_VERSION}")
                await self._apply_migrations(conn, from_version=current_version)

            # Projects table
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    api_key_hash TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Users table (for dashboard auth)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    email TEXT UNIQUE NOT NULL,
                    hashed_password TEXT NOT NULL,
                    is_active BOOLEAN DEFAULT 1,
                    failed_login_attempts INTEGER DEFAULT 0,
                    locked_until TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # HIGH-5 FIX: User-project ownership table
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS user_projects (
                    user_id TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'owner',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (user_id, project_id),
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
                )
            """)

            # DEMO_MODE FIX: the demo identity ("demo") has no users row, so
            # POST /projects 500s on the user_projects FK above. Seed a dummy
            # demo user so project CRUD works end-to-end in demo mode.
            # Dashboard login is bypassed in demo mode, so this placeholder
            # password hash is never actually verified.
            if settings.DEMO_MODE:
                await conn.execute(
                    """
                    INSERT OR IGNORE INTO users (id, email, hashed_password, is_active)
                    VALUES ('demo', 'demo@oxly.sh', 'DEMO_MODE_NO_LOGIN', 1)
                    """
                )

            # Traces table
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS traces (
                    trace_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    start_time INTEGER NOT NULL,
                    end_time INTEGER,
                    duration_ms INTEGER,
                    status TEXT DEFAULT 'OK',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
                )
            """)
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_traces_project
                ON traces(project_id, created_at DESC)
            """)
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_traces_status
                ON traces(status, created_at DESC)
            """)
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_traces_starttime
                ON traces(project_id, start_time DESC)
            """)

            # Spans table
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS spans (
                    span_id TEXT PRIMARY KEY,
                    trace_id TEXT NOT NULL,
                    parent_span_id TEXT,
                    project_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    start_time INTEGER NOT NULL,
                    end_time INTEGER,
                    duration_ms INTEGER,
                    status TEXT DEFAULT 'OK',
                    service_name TEXT,
                    attributes TEXT,
                    events TEXT,
                    api_key_hash TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (trace_id) REFERENCES traces(trace_id) ON DELETE CASCADE,
                    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
                )
            """)
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_spans_trace
                ON spans(trace_id, start_time ASC)
            """)
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_spans_project
                ON spans(project_id, created_at DESC)
            """)

            # Security alerts table — column named rule_name (not alert_type) to
            # match deploy/clickhouse/init.sql and workers/security_engine.py,
            # which is the actual write path. API layer aliases rule_name back
            # to alert_type at query time to keep the external schema stable.
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS security_alerts (
                    id TEXT PRIMARY KEY,
                    trace_id TEXT NOT NULL,
                    span_id TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    rule_name TEXT NOT NULL,
                    message TEXT NOT NULL,
                    metadata TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (trace_id) REFERENCES traces(trace_id) ON DELETE CASCADE,
                    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
                )
            """)
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_alerts_project
                ON security_alerts(project_id, severity, created_at DESC)
            """)

            # Cost metrics table — mirrors deploy/clickhouse/init.sql's cost_metrics
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS cost_metrics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id TEXT NOT NULL,
                    model TEXT NOT NULL,
                    span_kind TEXT,
                    timestamp TIMESTAMP NOT NULL,
                    prompt_tokens INTEGER NOT NULL DEFAULT 0,
                    completion_tokens INTEGER NOT NULL DEFAULT 0,
                    total_tokens INTEGER NOT NULL DEFAULT 0,
                    cost_usd REAL NOT NULL DEFAULT 0,
                    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
                )
            """)
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_cost_metrics_project
                ON cost_metrics(project_id, timestamp DESC)
            """)

            try:
                await conn.execute(
                    "ALTER TABLE users ADD COLUMN failed_login_attempts INTEGER DEFAULT 0"
                )
                await conn.execute("ALTER TABLE users ADD COLUMN locked_until TIMESTAMP")
            except Exception:
                pass

            await conn.commit()
            self._initialized = True
            logger.info(f"Database initialized at {self.db_path}")

    async def get_connection(self) -> aiosqlite.Connection:
        """Get a new database connection with correct pragmas."""
        if not self._initialized:
            await self.init_db()

        conn = await aiosqlite.connect(self.db_path, timeout=5.0)
        conn.row_factory = aiosqlite.Row

        # Enforce pragmas on every new connection
        await conn.execute("PRAGMA foreign_keys=ON")
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA synchronous=NORMAL")
        await conn.execute("PRAGMA busy_timeout=5000")

        return conn


# Global database instance
_db: Database | None = None


def get_database(db_path: str | Path = _DEFAULT_DB_PATH) -> Database:
    """Get the global Database instance (singleton)."""
    global _db
    if _db is None:
        _db = Database(db_path)
    return _db


async def get_db() -> AsyncGenerator[aiosqlite.Connection, None]:
    """Dependency injection: get database connection for FastAPI routes.

    Usage in routes:
        @router.get("/...")
        async def my_route(db: aiosqlite.Connection = Depends(get_db)):
            ...
    """
    db = get_database()
    conn = await db.get_connection()
    try:
        yield conn
    finally:
        await conn.close()
