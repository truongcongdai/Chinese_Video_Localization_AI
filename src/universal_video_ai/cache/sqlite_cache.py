"""Small persistent JSON cache backed by SQLite.

This cache is intentionally process-independent. Translation jobs can take
hours, so an in-memory Redis fallback is not sufficient: a provider block or
application restart must not discard all batches that already succeeded.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
import sqlite3
import time
from typing import Any, Optional

__all__ = ["SQLiteCache"]

_logger = logging.getLogger(__name__)


class SQLiteCache:
    """A minimal cache implementing the same interface as RedisCache."""

    def __init__(self, path: Path | str, logger: Optional[logging.Logger] = None) -> None:
        self.path = Path(path)
        self.logger = logger or _logger
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS cache_entries (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    expires_at REAL NOT NULL
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self.path), timeout=15.0)

    def set(self, key: str, value: Any, ttl_seconds: int = 86400) -> bool:
        try:
            serialized = json.dumps(value, ensure_ascii=False)
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO cache_entries(key, value, expires_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET
                        value=excluded.value,
                        expires_at=excluded.expires_at
                    """,
                    (key, serialized, time.time() + ttl_seconds),
                )
            return True
        except Exception as exc:
            self.logger.warning("SQLite cache set failed: %s", exc)
            return False

    def get(self, key: str) -> Optional[Any]:
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT value, expires_at FROM cache_entries WHERE key = ?",
                    (key,),
                ).fetchone()
                if row is None:
                    return None
                if float(row[1]) <= time.time():
                    connection.execute("DELETE FROM cache_entries WHERE key = ?", (key,))
                    return None
                return json.loads(row[0])
        except Exception as exc:
            self.logger.warning("SQLite cache get failed: %s", exc)
            return None

    def delete(self, key: str) -> bool:
        try:
            with self._connect() as connection:
                connection.execute("DELETE FROM cache_entries WHERE key = ?", (key,))
            return True
        except Exception as exc:
            self.logger.warning("SQLite cache delete failed: %s", exc)
            return False

    def clear(self) -> bool:
        try:
            with self._connect() as connection:
                connection.execute("DELETE FROM cache_entries")
            return True
        except Exception as exc:
            self.logger.warning("SQLite cache clear failed: %s", exc)
            return False

    @staticmethod
    def make_key(prefix: str, *parts: str) -> str:
        combined = ":".join([prefix, *parts])
        if len(combined) > 100:
            return f"{prefix}:{hashlib.sha256(combined.encode('utf-8')).hexdigest()}"
        return combined
