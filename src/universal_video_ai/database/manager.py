# src/universal_video_ai/database/manager.py
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import logging
import sqlite3
import threading
import time
from typing import Any, Dict, List, Optional

from universal_video_ai.config import TEMP_DIR

__all__ = ["DatabaseManager", "DownloadRecord"]

_DEFAULT_DB_FILENAME = "database.sqlite3"
_logger = logging.getLogger(__name__)


@dataclass
class DownloadRecord:
    """
    Represents a persisted download/job record.
    """
    id: Optional[int]
    url: str
    platform: str
    status: str
    video_path: Optional[str]
    title: str
    created_at: float
    updated_at: float
    metadata: Dict[str, Any]


class DatabaseManager:
    """
    Simple SQLite-based database manager for download records.

    Notes:
    - Uses a single-file SQLite database stored under the provided db_path.
    - Provides basic CRUD operations for DownloadRecord.
    - Thread-safe for typical concurrent access via an internal Lock.
    """

    def __init__(self, db_path: Optional[Path] = None, logger: Optional[logging.Logger] = None) -> None:
        """
        Initialize DatabaseManager.

        :param db_path: Path to sqlite db file. If None, uses TEMP_DIR / 'database.sqlite3'.
        :param logger: optional logger; if None module logger is used.
        """
        self.logger = logger or _logger
        db_path = Path(db_path) if db_path is not None else (TEMP_DIR / _DEFAULT_DB_FILENAME)
        self.db_path: Path = db_path.resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        # sqlite connection; check_same_thread=False so we can share across threads but we still protect with a Lock
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()

        self.logger.debug("DatabaseManager initialized with db_path=%s", str(self.db_path))

    def init_schema(self) -> None:
        """
        Create schema if not exists.
        """
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS downloads (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    url TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    status TEXT NOT NULL,
                    video_path TEXT,
                    title TEXT,
                    metadata TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
            self._conn.commit()
            self.logger.info("Database schema ensured at %s", self.db_path)

    def _now(self) -> float:
        return time.time()

    def add_download(
        self,
        url: str,
        platform: str,
        status: str = "pending",
        title: str = "",
        video_path: Optional[Path] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> int:
        """
        Insert a new download record and return its id.
        """
        metadata = metadata or {}
        created = self._now()
        updated = created
        video_path_str = str(video_path) if video_path is not None else None

        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                INSERT INTO downloads (url, platform, status, video_path, title, metadata, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    url,
                    platform,
                    status,
                    video_path_str,
                    title,
                    json.dumps(metadata, ensure_ascii=False),
                    created,
                    updated,
                ),
            )
            self._conn.commit()
            row_id = cur.lastrowid
            self.logger.debug("Added download record id=%s url=%s", row_id, url)
            return int(row_id)

    def get_download(self, record_id: int) -> Optional[DownloadRecord]:
        """
        Retrieve a DownloadRecord by id.
        """
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("SELECT * FROM downloads WHERE id = ?", (record_id,))
            row = cur.fetchone()
            if row is None:
                self.logger.debug("Download record id=%s not found", record_id)
                return None
            return self._row_to_record(row)

    def update_download(self, record_id: int, **fields: Any) -> None:
        """
        Update fields of a download record. Allowed fields: url, platform, status, video_path, title, metadata.

        Example:
            update_download(1, status="completed", video_path="/tmp/out.mp4")
        """
        allowed = {"url", "platform", "status", "video_path", "title", "metadata"}
        set_items = []
        params: List[Any] = []

        for k, v in fields.items():
            if k not in allowed:
                raise ValueError(f"Field not allowed for update: {k}")
            if k == "metadata":
                params.append(json.dumps(v, ensure_ascii=False))
            else:
                params.append(str(v) if v is not None else None)
            set_items.append(f"{k} = ?")

        if not set_items:
            return

        # always update updated_at
        set_items.append("updated_at = ?")
        params.append(self._now())

        params.append(record_id)

        sql = f"UPDATE downloads SET {', '.join(set_items)} WHERE id = ?"
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(sql, tuple(params))
            self._conn.commit()
            self.logger.debug("Updated download id=%s fields=%s", record_id, list(fields.keys()))

    def list_downloads(self, limit: int = 100, offset: int = 0) -> List[DownloadRecord]:
        """
        List download records paginated.
        """
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("SELECT * FROM downloads ORDER BY created_at DESC LIMIT ? OFFSET ?", (limit, offset))
            rows = cur.fetchall()
            return [self._row_to_record(r) for r in rows]

    def delete_download(self, record_id: int) -> None:
        """
        Delete a download record by id.
        """
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("DELETE FROM downloads WHERE id = ?", (record_id,))
            self._conn.commit()
            self.logger.debug("Deleted download id=%s", record_id)

    def _row_to_record(self, row: sqlite3.Row) -> DownloadRecord:
        metadata_text = row["metadata"] or "{}"
        try:
            metadata = json.loads(metadata_text)
        except Exception:
            metadata = {}
        return DownloadRecord(
            id=int(row["id"]),
            url=str(row["url"]),
            platform=str(row["platform"]),
            status=str(row["status"]),
            video_path=row["video_path"],
            title=row["title"] or "",
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
            metadata=metadata,
        )

    def close(self) -> None:
        """
        Close the underlying sqlite connection.
        """
        try:
            with self._lock:
                self._conn.close()
                self.logger.debug("Closed database connection to %s", self.db_path)
        except Exception:
            self.logger.exception("Error closing database connection")