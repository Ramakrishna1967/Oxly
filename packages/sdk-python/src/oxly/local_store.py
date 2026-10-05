# Copyright 2026 Oxly Contributors
# SPDX-License-Identifier: Apache-2.0

"""Local SQLite storage for offline span persistence.

When the Collector is unreachable (network down, server crashed), the SDK
falls back to storing spans in a local SQLite database (.oxly.db).
When connectivity is restored, the BatchSpanProcessor replays unsent spans.

This module also provides JSON export for debugging / manual inspection.

Thread safety: Each method opens its own connection and uses autocommit,
making it safe to call from any thread or async task.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from contextlib import closing
from pathlib import Path

from oxly.models import SpanModel

logger = logging.getLogger("oxly")

# Default database file location
_DEFAULT_DB_NAME = ".oxly.db"


class LocalStore:
    """SQLite-backed local span storage for offline fallback.

    Stores serialized spans when the remote Collector is unreachable.
    Spans can be replayed when connectivity is restored.

    Args:
        db_path: Path to the SQLite database file. Defaults to .oxly.db
                 in the current working directory.
    """

    def __init__(self, db_path: str | Path | None = None) -> None:
        if db_path is None:
            db_path = Path.cwd() / _DEFAULT_DB_NAME
        self._db_path = str(db_path)
        self._lock = threading.Lock()
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        """Create a new SQLite connection (one per call for thread safety)."""
        conn = sqlite3.connect(self._db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _init_db(self) -> None:
        """Create the spans table if it doesn't exist."""
        try:
            with closing(self._get_conn()) as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS spans (
                        span_id TEXT PRIMARY KEY,
                        trace_id TEXT NOT NULL,
                        data TEXT NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        sent INTEGER DEFAULT 0
                    )
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_spans_unsent
                    ON spans (sent) WHERE sent = 0
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_spans_trace
                    ON spans (trace_id)
                """)
                conn.commit()
        except Exception:
            logger.debug("Failed to initialize local store at %s", self._db_path, exc_info=True)

    def save_span(self, span: SpanModel) -> bool:
        """Persist a span to local SQLite storage.

        Args:
            span: The SpanModel to save.

        Returns:
            True if saved successfully, False on error.
        """
        try:
            data = json.dumps(span.to_export_dict())
            with self._lock, closing(self._get_conn()) as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO spans (span_id, trace_id, data, sent) VALUES (?, ?, ?, 0)",
                    (span.span_id, span.trace_id, data),
                )
                conn.commit()
            return True
        except Exception:
            logger.debug("Failed to save span %s to local store", span.span_id, exc_info=True)
            return False

    def save_spans(self, spans: list[SpanModel]) -> int:
        """Persist multiple spans in a single transaction.

        Args:
            spans: List of SpanModels to save.

        Returns:
            Number of spans successfully saved.
        """
        if not spans:
            return 0
        try:
            rows = [
                (s.span_id, s.trace_id, json.dumps(s.to_export_dict()), 0)
                for s in spans
            ]
            with self._lock, closing(self._get_conn()) as conn:
                conn.executemany(
                    "INSERT OR REPLACE INTO spans (span_id, trace_id, data, sent) VALUES (?, ?, ?, ?)",
                    rows,
                )
                conn.commit()
            return len(rows)
        except Exception:
            logger.debug("Failed to batch save spans to local store", exc_info=True)
            return 0

    def get_unsent_spans(self, limit: int = 100) -> list[SpanModel]:
        """Retrieve spans that haven't been sent to the Collector yet.

        Args:
            limit: Maximum number of spans to return.

        Returns:
            List of SpanModel objects ready for retry.
        """
        try:
            with self._lock, closing(self._get_conn()) as conn:
                cursor = conn.execute(
                    "SELECT span_id, data FROM spans WHERE sent = 0 ORDER BY created_at ASC LIMIT ?",
                    (limit,),
                )
                rows = cursor.fetchall()

            spans = []
            for span_id, data in rows:
                try:
                    span_dict = json.loads(data)
                    spans.append(SpanModel(**span_dict))
                except Exception:
                    logger.debug("Failed to deserialize span %s", span_id, exc_info=True)
            return spans
        except Exception:
            logger.debug("Failed to get unsent spans", exc_info=True)
            return []

    def mark_as_sent(self, span_ids: list[str]) -> int:
        """Mark spans as successfully sent to the Collector.

        Args:
            span_ids: List of span_id strings to mark.

        Returns:
            Number of spans marked.
        """
        if not span_ids:
            return 0
        try:
            placeholders = ",".join("?" for _ in span_ids)
            with self._lock, closing(self._get_conn()) as conn:
                cursor = conn.execute(
                    f"UPDATE spans SET sent = 1 WHERE span_id IN ({placeholders})",  # nosec B608
                    span_ids,
                )
                count = cursor.rowcount
                conn.commit()
            return count
        except Exception:
            logger.debug("Failed to mark spans as sent", exc_info=True)
            return 0

    def delete_sent(self) -> int:
        """Delete all spans that have been successfully sent.

        Returns:
            Number of spans deleted.
        """
        try:
            with self._lock, closing(self._get_conn()) as conn:
                cursor = conn.execute("DELETE FROM spans WHERE sent = 1")
                count = cursor.rowcount
                conn.commit()
            return count
        except Exception:
            logger.debug("Failed to delete sent spans", exc_info=True)
            return 0

    def export_to_json(self, path: str | Path) -> int:
        """Export all spans to a JSON file for debugging.

        Args:
            path: File path to write the JSON output.

        Returns:
            Number of spans exported.
        """
        try:
            with self._lock, closing(self._get_conn()) as conn:
                cursor = conn.execute(
                    "SELECT data FROM spans ORDER BY created_at ASC"
                )
                rows = cursor.fetchall()

            spans_data = [json.loads(row[0]) for row in rows]
            with open(path, "w", encoding="utf-8") as f:
                json.dump(spans_data, f, indent=2)
            return len(spans_data)
        except Exception:
            logger.debug("Failed to export spans to %s", path, exc_info=True)
            return 0

    @property
    def unsent_count(self) -> int:
        """Number of unsent spans in the store."""
        try:
            with self._lock, closing(self._get_conn()) as conn:
                cursor = conn.execute("SELECT COUNT(*) FROM spans WHERE sent = 0")
                count = cursor.fetchone()[0]
            return count
        except Exception:
            return 0

    @property
    def total_count(self) -> int:
        """Total number of spans in the store."""
        try:
            with self._lock, closing(self._get_conn()) as conn:
                cursor = conn.execute("SELECT COUNT(*) FROM spans")
                count = cursor.fetchone()[0]
            return count
        except Exception:
            return 0

    def close(self) -> None:
        """Clean up (no-op since we use connection-per-call)."""
        pass

    def __repr__(self) -> str:
        return f"LocalStore(db={self._db_path}, unsent={self.unsent_count})"


#  Module-level singleton

_store: LocalStore | None = None


def get_local_store() -> LocalStore:
    """Return the global LocalStore singleton (lazy-initialized)."""
    global _store
    if _store is None:
        _store = LocalStore()
    return _store


def reset_local_store() -> None:
    """Reset the global store singleton. Primarily for testing."""
    global _store
    _store = None
