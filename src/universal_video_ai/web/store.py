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
    password_hash TEXT,
    credits INTEGER NOT NULL DEFAULT 10,
    is_admin INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    source_url TEXT NOT NULL,
    target_language TEXT NOT NULL,
    source_language TEXT DEFAULT 'auto',
    status TEXT NOT NULL,           -- queued | running | done | error
    progress_note TEXT,
    error TEXT,
    title TEXT,
    final_video_path TEXT,
    logo_path TEXT,
    logo_corner TEXT DEFAULT 'bottom_right',
    logo_size_px INTEGER DEFAULT 120,
    enable_anti_copyright INTEGER DEFAULT 0,
    letterbox TEXT,
    zoom_factor REAL DEFAULT 1.0,
    flip_horizontal INTEGER DEFAULT 0,
    speed_factor REAL DEFAULT 1.0,
    brightness REAL DEFAULT 0.0,
    contrast REAL DEFAULT 1.0,
    saturation REAL DEFAULT 1.0,
    noise_amount INTEGER DEFAULT 0,
    rotation_degrees REAL DEFAULT 0.0,
    crop TEXT,
    target_platform TEXT DEFAULT 'none',
    target_aspect_ratio TEXT DEFAULT 'auto',
    target_resolution TEXT,
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

-- Short-lived CSRF state for the IDENTITY "Sign in with ..." flow (login/
-- register, as opposed to oauth_states above which is for per-user social
-- "connect my account to publish" after already being logged in). Doesn't
-- carry a user_id since the whole point is we don't know who this is yet.
CREATE TABLE IF NOT EXISTS identity_oauth_states (
    state TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    created_at REAL NOT NULL
);
"""

_MIGRATIONS = [
    ("users", "credits", "ALTER TABLE users ADD COLUMN credits INTEGER NOT NULL DEFAULT 10"),
    ("users", "is_admin", "ALTER TABLE users ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0"),
    # Alternative sign-up/sign-in identifiers alongside the original
    # `username`. All three of email/phone/username are optional at the SQL
    # level (a user might only have one of them, e.g. a Google-only login
    # has no password/username at all) — application code enforces that
    # every user has AT LEAST ONE way to log in.
    ("users", "email", "ALTER TABLE users ADD COLUMN email TEXT"),
    ("users", "phone", "ALTER TABLE users ADD COLUMN phone TEXT"),
    # Which identity provider created this account via "Sign in with ..."
    # (NULL for a plain username/password account), and that provider's own
    # unique id for the person, so a later login from the same provider
    # finds the same row again instead of creating a duplicate account.
    ("users", "oauth_provider", "ALTER TABLE users ADD COLUMN oauth_provider TEXT"),
    ("users", "oauth_id", "ALTER TABLE users ADD COLUMN oauth_id TEXT"),
    # Per-job source language + optional brand-logo overlay settings,
    # added after jobs already existed in the wild.
    ("jobs", "source_language", "ALTER TABLE jobs ADD COLUMN source_language TEXT DEFAULT 'auto'"),
    ("jobs", "logo_path", "ALTER TABLE jobs ADD COLUMN logo_path TEXT"),
    ("jobs", "logo_corner", "ALTER TABLE jobs ADD COLUMN logo_corner TEXT DEFAULT 'bottom_right'"),
    ("jobs", "logo_size_px", "ALTER TABLE jobs ADD COLUMN logo_size_px INTEGER DEFAULT 120"),
    # Anti-copyright filter options
    ("jobs", "enable_anti_copyright", "ALTER TABLE jobs ADD COLUMN enable_anti_copyright INTEGER DEFAULT 0"),
    ("jobs", "letterbox", "ALTER TABLE jobs ADD COLUMN letterbox TEXT"),
    ("jobs", "zoom_factor", "ALTER TABLE jobs ADD COLUMN zoom_factor REAL DEFAULT 1.0"),
    ("jobs", "flip_horizontal", "ALTER TABLE jobs ADD COLUMN flip_horizontal INTEGER DEFAULT 0"),
    ("jobs", "speed_factor", "ALTER TABLE jobs ADD COLUMN speed_factor REAL DEFAULT 1.0"),
    ("jobs", "brightness", "ALTER TABLE jobs ADD COLUMN brightness REAL DEFAULT 0.0"),
    ("jobs", "contrast", "ALTER TABLE jobs ADD COLUMN contrast REAL DEFAULT 1.0"),
    ("jobs", "saturation", "ALTER TABLE jobs ADD COLUMN saturation REAL DEFAULT 1.0"),
    ("jobs", "noise_amount", "ALTER TABLE jobs ADD COLUMN noise_amount INTEGER DEFAULT 0"),
    ("jobs", "rotation_degrees", "ALTER TABLE jobs ADD COLUMN rotation_degrees REAL DEFAULT 0.0"),
    ("jobs", "crop", "ALTER TABLE jobs ADD COLUMN crop TEXT"),
    # Platform-specific optimization
    ("jobs", "target_platform", "ALTER TABLE jobs ADD COLUMN target_platform TEXT DEFAULT 'none'"),
    ("jobs", "target_aspect_ratio", "ALTER TABLE jobs ADD COLUMN target_aspect_ratio TEXT DEFAULT 'auto'"),
    ("jobs", "target_resolution", "ALTER TABLE jobs ADD COLUMN target_resolution TEXT"),
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
    source_language: str = "auto"
    logo_path: Optional[str] = None
    logo_corner: str = "bottom_right"
    logo_size_px: int = 120
    # Anti-copyright options
    enable_anti_copyright: bool = False
    letterbox: Optional[str] = None
    zoom_factor: float = 1.0
    flip_horizontal: bool = False
    speed_factor: float = 1.0
    brightness: float = 0.0
    contrast: float = 1.0
    saturation: float = 1.0
    noise_amount: int = 0
    rotation_degrees: float = 0.0
    crop: Optional[str] = None
    # Platform-specific optimization
    target_platform: str = "none"
    target_aspect_ratio: str = "auto"
    target_resolution: Optional[str] = None

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
                for table in ("users", "jobs")
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
    def create_user(
        self, username: str, password_hash: str, is_admin: bool = False,
        credits: int = 10, email: Optional[str] = None, phone: Optional[str] = None,
    ) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO users (username, password_hash, credits, is_admin, email, phone, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (username, password_hash, credits, int(is_admin), email, phone, time.time()),
            )
            return cur.lastrowid

    def create_user_oauth(
        self, username: str, oauth_provider: str, oauth_id: str,
        email: Optional[str] = None, is_admin: bool = False, credits: int = 10,
    ) -> int:
        """
        Create an account for someone who signed up/in via "Sign in with
        Google/GitHub/Facebook" rather than a username+password form.

        `password_hash` still gets a real (but unusable/never-shared)
        bcrypt hash of a random token — rather than NULL — purely so this
        works unmodified against an existing database that still has the
        original `password_hash TEXT NOT NULL` constraint from before OAuth
        login existed; the value itself can never be used to log in since
        nobody knows it.
        """
        import secrets
        from .auth import hash_password
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO users (username, password_hash, credits, is_admin, email, "
                "oauth_provider, oauth_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (username, hash_password(secrets.token_urlsafe(32)), credits, int(is_admin),
                 email, oauth_provider, oauth_id, time.time()),
            )
            return cur.lastrowid

    def get_user_by_username(self, username: str) -> Optional[sqlite3.Row]:
        with self._connect() as conn:
            cur = conn.execute("SELECT * FROM users WHERE username = ?", (username,))
            return cur.fetchone()

    def get_user_by_email(self, email: str) -> Optional[sqlite3.Row]:
        with self._connect() as conn:
            cur = conn.execute("SELECT * FROM users WHERE email = ?", (email,))
            return cur.fetchone()

    def get_user_by_phone(self, phone: str) -> Optional[sqlite3.Row]:
        with self._connect() as conn:
            cur = conn.execute("SELECT * FROM users WHERE phone = ?", (phone,))
            return cur.fetchone()

    def get_user_by_identifier(self, identifier: str) -> Optional[sqlite3.Row]:
        """Look up a user by whichever of username/email/phone matches —
        used at login time so one input box can accept any of the three."""
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT * FROM users WHERE username = ? OR email = ? OR phone = ?",
                (identifier, identifier, identifier),
            )
            return cur.fetchone()

    def get_user_by_oauth(self, provider: str, oauth_id: str) -> Optional[sqlite3.Row]:
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT * FROM users WHERE oauth_provider = ? AND oauth_id = ?",
                (provider, oauth_id),
            )
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
    def create_job(
        self, user_id: int, source_url: str, target_language: str,
        source_language: str = "auto", logo_path: Optional[str] = None,
        logo_corner: str = "bottom_right", logo_size_px: int = 120,
        enable_anti_copyright: bool = False, letterbox: Optional[str] = None,
        zoom_factor: float = 1.0, flip_horizontal: bool = False,
        speed_factor: float = 1.0, brightness: float = 0.0,
        contrast: float = 1.0, saturation: float = 1.0,
        noise_amount: int = 0, rotation_degrees: float = 0.0,
        crop: Optional[str] = None, target_platform: str = "none",
        target_aspect_ratio: str = "auto", target_resolution: Optional[str] = None,
    ) -> Job:
        job_id = uuid.uuid4().hex[:12]
        now = time.time()
        job = Job(
            id=job_id, user_id=user_id, source_url=source_url,
            target_language=target_language, status="queued",
            progress_note="Đã xếp hàng chờ xử lý", error=None, title=None,
            final_video_path=None, created_at=now, updated_at=now,
            source_language=source_language, logo_path=logo_path,
            logo_corner=logo_corner, logo_size_px=logo_size_px,
            enable_anti_copyright=enable_anti_copyright, letterbox=letterbox,
            zoom_factor=zoom_factor, flip_horizontal=flip_horizontal,
            speed_factor=speed_factor, brightness=brightness,
            contrast=contrast, saturation=saturation,
            noise_amount=noise_amount, rotation_degrees=rotation_degrees,
            crop=crop, target_platform=target_platform,
            target_aspect_ratio=target_aspect_ratio, target_resolution=target_resolution,
        )
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO jobs (id, user_id, source_url, target_language, source_language, status, "
                "progress_note, error, title, final_video_path, logo_path, logo_corner, logo_size_px, "
                "enable_anti_copyright, letterbox, zoom_factor, flip_horizontal, speed_factor, "
                "brightness, contrast, saturation, noise_amount, rotation_degrees, crop, "
                "target_platform, target_aspect_ratio, target_resolution, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (job.id, job.user_id, job.source_url, job.target_language, job.source_language,
                 job.status, job.progress_note, job.error, job.title, job.final_video_path,
                 job.logo_path, job.logo_corner, job.logo_size_px,
                 int(job.enable_anti_copyright), job.letterbox, job.zoom_factor,
                 int(job.flip_horizontal), job.speed_factor, job.brightness,
                 job.contrast, job.saturation, job.noise_amount,
                 job.rotation_degrees, job.crop, job.target_platform,
                 job.target_aspect_ratio, job.target_resolution, job.created_at, job.updated_at),
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

    def search_jobs_for_user(
        self,
        user_id: int,
        query: Optional[str] = None,
        date_from: Optional[float] = None,
        date_to: Optional[float] = None,
        limit: int = 200,
    ) -> List[Job]:
        """
        Same as `list_jobs_for_user` but filterable — used by the history
        panel's search box (matches job title/source URL, case-insensitive)
        and date-range filter (both as Unix timestamps, inclusive).
        Always scoped to `user_id`, so one account can never see or search
        another account's history.
        """
        clauses = ["user_id = ?"]
        params: List[Any] = [user_id]

        if query:
            clauses.append("(title LIKE ? OR source_url LIKE ?)")
            like = f"%{query}%"
            params.extend([like, like])
        if date_from is not None:
            clauses.append("created_at >= ?")
            params.append(date_from)
        if date_to is not None:
            clauses.append("created_at <= ?")
            params.append(date_to)

        where = " AND ".join(clauses)
        params.append(limit)
        with self._connect() as conn:
            cur = conn.execute(
                f"SELECT * FROM jobs WHERE {where} ORDER BY created_at DESC LIMIT ?",
                params,
            )
            return [Job(**dict(row)) for row in cur.fetchall()]

    def delete_job(self, job_id: str, user_id: int) -> bool:
        """
        Delete a history entry. Scoped to `user_id` so one account can never
        delete another account's job even if it guesses/enumerates job ids.
        Returns True if a row was actually deleted.
        """
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM jobs WHERE id = ? AND user_id = ?", (job_id, user_id)
            )
            return cur.rowcount > 0

    def delete_jobs(self, job_ids: List[str], user_id: int) -> int:
        """Bulk-delete history entries (e.g. from a multi-select checkbox
        list in the UI). Scoped to `user_id` same as delete_job. Returns how
        many rows were actually deleted."""
        if not job_ids:
            return 0
        with self._connect() as conn:
            placeholders = ",".join("?" for _ in job_ids)
            cur = conn.execute(
                f"DELETE FROM jobs WHERE user_id = ? AND id IN ({placeholders})",
                [user_id, *job_ids],
            )
            return cur.rowcount

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

    # ---- identity oauth state (CSRF protection for "Sign in with ..." login/register) ----
    def create_identity_oauth_state(self, state: str, provider: str) -> None:
        now = time.time()
        with self._connect() as conn:
            conn.execute("DELETE FROM identity_oauth_states WHERE created_at < ?", (now - 3600,))
            conn.execute(
                "INSERT INTO identity_oauth_states (state, provider, created_at) VALUES (?,?,?)",
                (state, provider, now),
            )

    def consume_identity_oauth_state(self, state: str) -> Optional[sqlite3.Row]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM identity_oauth_states WHERE state = ?", (state,)
            ).fetchone()
            if row:
                conn.execute("DELETE FROM identity_oauth_states WHERE state = ?", (state,))
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
