# src/universal_video_ai/web/store.py
"""
Lightweight SQLite-backed storage for the web UI: user accounts (for login)
and localization jobs (for history/status/preview/download).

Deliberately stdlib-only (sqlite3) rather than an ORM — this app has two
small tables and doesn't need one.
"""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, List, Dict, Any


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    credits INTEGER NOT NULL DEFAULT 10,
    is_admin INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    source_url TEXT NOT NULL,
    target_language TEXT NOT NULL,
    status TEXT NOT NULL,           -- queued | running | done | error
    progress_note TEXT,
    error TEXT,
    title TEXT,
    final_video_path TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS publish_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    platform TEXT NOT NULL,
    success INTEGER NOT NULL,
    message TEXT,
    remote_url TEXT,
    created_at REAL NOT NULL
);

-- Per-user OAuth connections to social platforms. Unlike the old
-- single-shared-.env-token model, each row here belongs to exactly one
-- app user, so multiple people using this server each connect their own
-- TikTok/Facebook/YouTube account and publish as themselves.
CREATE TABLE IF NOT EXISTS social_accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    platform TEXT NOT NULL,          -- tiktok | facebook | youtube
    access_token TEXT,
    refresh_token TEXT,
    expires_at REAL,
    account_name TEXT,               -- display name shown in the UI ("Connected as ...")
    account_ref TEXT,                -- platform-specific id (e.g. FB page id, open_id)
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    UNIQUE(user_id, platform)
);

-- Short-lived CSRF state for the OAuth redirect dance (state -> which
-- app user + which platform initiated it). Rows are deleted once consumed
-- by the callback, and stale ones (>1h) are swept on write.
CREATE TABLE IF NOT EXISTS oauth_states (
    state TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    platform TEXT NOT NULL,
    created_at REAL NOT NULL
);
"""

_MIGRATIONS = [
    ("users", "credits", "ALTER TABLE users ADD COLUMN credits INTEGER NOT NULL DEFAULT 10"),
    ("users", "is_admin", "ALTER TABLE users ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0"),
]


@dataclass
class Job:
    id: str
    user_id: int
    source_url: str
    target_language: str
    status: str
    progress_note: Optional[str]
    error: Optional[str]
    title: Optional[str]
    final_video_path: Optional[str]
    created_at: float
    updated_at: float

    def to_dict(self) -> Dict[str, Any]:
        d = self.__dict__.copy()
        d["has_video"] = bool(self.final_video_path and Path(self.final_video_path).exists())
        return d


class Store:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(SCHEMA)
            existing_cols = {
                table: {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
                for table in ("users",)
            }
            ran_is_admin_migration = False
            for table, column, ddl in _MIGRATIONS:
                if column not in existing_cols.get(table, set()):
                    conn.execute(ddl)
                    if column == "is_admin":
                        ran_is_admin_migration = True

            if ran_is_admin_migration:
                # Upgrading a database created before admin/credits existed:
                # nobody has is_admin=1 yet (the column just got added with
                # a default of 0), which would lock whoever was already
                # using this server out of the new admin dashboard. Promote
                # the earliest-created account — i.e. whoever originally
                # set this server up — so continuity is preserved.
                any_admin = conn.execute("SELECT 1 FROM users WHERE is_admin = 1 LIMIT 1").fetchone()
                if not any_admin:
                    first_user = conn.execute(
                        "SELECT id FROM users ORDER BY created_at ASC LIMIT 1"
                    ).fetchone()
                    if first_user:
                        conn.execute("UPDATE users SET is_admin = 1 WHERE id = ?", (first_user["id"],))

    # ---- users ----
    def create_user(self, username: str, password_hash: str, is_admin: bool = False,
                     credits: int = 10) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO users (username, password_hash, credits, is_admin, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (username, password_hash, credits, int(is_admin), time.time()),
            )
            return cur.lastrowid

    def get_user_by_username(self, username: str) -> Optional[sqlite3.Row]:
        with self._connect() as conn:
            cur = conn.execute("SELECT * FROM users WHERE username = ?", (username,))
            return cur.fetchone()

    def get_user_by_id(self, user_id: int) -> Optional[sqlite3.Row]:
        with self._connect() as conn:
            cur = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,))
            return cur.fetchone()

    def any_users_exist(self) -> bool:
        with self._connect() as conn:
            cur = conn.execute("SELECT COUNT(*) AS c FROM users")
            return cur.fetchone()["c"] > 0

    def list_users(self) -> List[sqlite3.Row]:
        with self._connect() as conn:
            cur = conn.execute("SELECT * FROM users ORDER BY created_at ASC")
            return cur.fetchall()

    def adjust_credits(self, user_id: int, delta: int) -> int:
        """Add (or, with a negative delta, subtract) credits. Returns the new balance."""
        with self._connect() as conn:
            conn.execute("UPDATE users SET credits = credits + ? WHERE id = ?", (delta, user_id))
            row = conn.execute("SELECT credits FROM users WHERE id = ?", (user_id,)).fetchone()
            return row["credits"] if row else 0

    def set_credits(self, user_id: int, value: int) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE users SET credits = ? WHERE id = ?", (value, user_id))

    def set_admin(self, user_id: int, is_admin: bool) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE users SET is_admin = ? WHERE id = ?", (int(is_admin), user_id))

    def create_user_by_admin(self, username: str, password_hash: str, credits: int = 10) -> int:
        return self.create_user(username, password_hash, is_admin=False, credits=credits)

    # ---- jobs ----
    def create_job(self, user_id: int, source_url: str, target_language: str) -> Job:
        job_id = uuid.uuid4().hex[:12]
        now = time.time()
        job = Job(
            id=job_id, user_id=user_id, source_url=source_url,
            target_language=target_language, status="queued",
            progress_note="Đã xếp hàng chờ xử lý", error=None, title=None,
            final_video_path=None, created_at=now, updated_at=now,
        )
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO jobs (id, user_id, source_url, target_language, status, "
                "progress_note, error, title, final_video_path, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (job.id, job.user_id, job.source_url, job.target_language, job.status,
                 job.progress_note, job.error, job.title, job.final_video_path,
                 job.created_at, job.updated_at),
            )
        return job

    def update_job(self, job_id: str, **fields: Any) -> None:
        if not fields:
            return
        fields["updated_at"] = time.time()
        set_clause = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [job_id]
        with self._connect() as conn:
            conn.execute(f"UPDATE jobs SET {set_clause} WHERE id = ?", values)

    def get_job(self, job_id: str) -> Optional[Job]:
        with self._connect() as conn:
            cur = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
            row = cur.fetchone()
            return Job(**dict(row)) if row else None

    def list_jobs_for_user(self, user_id: int, limit: int = 100) -> List[Job]:
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT * FROM jobs WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
                (user_id, limit),
            )
            return [Job(**dict(row)) for row in cur.fetchall()]

    # ---- publish log ----
    def log_publish(self, job_id: str, platform: str, success: bool, message: str,
                     remote_url: Optional[str] = None) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO publish_log (job_id, platform, success, message, remote_url, created_at) "
                "VALUES (?,?,?,?,?,?)",
                (job_id, platform, int(success), message, remote_url, time.time()),
            )

    # ---- social accounts (per-user OAuth connections) ----
    def upsert_social_account(
        self, user_id: int, platform: str, access_token: Optional[str],
        refresh_token: Optional[str] = None, expires_at: Optional[float] = None,
        account_name: Optional[str] = None, account_ref: Optional[str] = None,
    ) -> None:
        now = time.time()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO social_accounts
                    (user_id, platform, access_token, refresh_token, expires_at,
                     account_name, account_ref, created_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?)
                ON CONFLICT(user_id, platform) DO UPDATE SET
                    access_token=excluded.access_token,
                    refresh_token=COALESCE(excluded.refresh_token, social_accounts.refresh_token),
                    expires_at=excluded.expires_at,
                    account_name=excluded.account_name,
                    account_ref=excluded.account_ref,
                    updated_at=excluded.updated_at
                """,
                (user_id, platform, access_token, refresh_token, expires_at,
                 account_name, account_ref, now, now),
            )

    def get_social_account(self, user_id: int, platform: str) -> Optional[sqlite3.Row]:
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT * FROM social_accounts WHERE user_id = ? AND platform = ?",
                (user_id, platform),
            )
            return cur.fetchone()

    def list_social_accounts(self, user_id: int) -> List[sqlite3.Row]:
        with self._connect() as conn:
            cur = conn.execute("SELECT * FROM social_accounts WHERE user_id = ?", (user_id,))
            return cur.fetchall()

    def delete_social_account(self, user_id: int, platform: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM social_accounts WHERE user_id = ? AND platform = ?",
                (user_id, platform),
            )

    # ---- oauth state (CSRF protection for the connect redirect flow) ----
    def create_oauth_state(self, state: str, user_id: int, platform: str) -> None:
        now = time.time()
        with self._connect() as conn:
            # Sweep anything older than an hour — these are single-use and
            # short-lived, no reason to let abandoned ones pile up.
            conn.execute("DELETE FROM oauth_states WHERE created_at < ?", (now - 3600,))
            conn.execute(
                "INSERT INTO oauth_states (state, user_id, platform, created_at) VALUES (?,?,?,?)",
                (state, user_id, platform, now),
            )

    def consume_oauth_state(self, state: str) -> Optional[sqlite3.Row]:
        """Look up and delete a state token in one call (single-use)."""
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM oauth_states WHERE state = ?", (state,)).fetchone()
            if row:
                conn.execute("DELETE FROM oauth_states WHERE state = ?", (state,))
            return row

    # ---- admin / stats ----
    def admin_stats(self) -> Dict[str, Any]:
        with self._connect() as conn:
            total_users = conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
            jobs_by_status = {
                row["status"]: row["c"]
                for row in conn.execute("SELECT status, COUNT(*) c FROM jobs GROUP BY status")
            }
            total_jobs = sum(jobs_by_status.values())
            publishes_by_platform = {
                row["platform"]: row["c"]
                for row in conn.execute(
                    "SELECT platform, COUNT(*) c FROM publish_log WHERE success = 1 GROUP BY platform"
                )
            }
            jobs_last_7d = conn.execute(
                "SELECT COUNT(*) c FROM jobs WHERE created_at > ?", (time.time() - 7 * 86400,)
            ).fetchone()["c"]
            total_credits_issued = conn.execute("SELECT COALESCE(SUM(credits),0) s FROM users").fetchone()["s"]
            return {
                "total_users": total_users,
                "total_jobs": total_jobs,
                "jobs_by_status": jobs_by_status,
                "jobs_last_7d": jobs_last_7d,
                "publishes_by_platform": publishes_by_platform,
                "total_credits_outstanding": total_credits_issued,
            }
