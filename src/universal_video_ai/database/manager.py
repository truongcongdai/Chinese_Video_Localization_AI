
# src/universal_video_ai/database/manager.py
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import logging
import sqlite3
import threading
import time
from typing import Any, Dict, List, Optional, Callable

from universal_video_ai.config import TEMP_DIR

__all__ = ["DatabaseManager", "DownloadRecord", "UserCredit"]

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


@dataclass
class UserCredit:
    """Represents a user's credit balance and subscription tier."""
    user_id: int  # e.g. Telegram chat id
    credits: float
    total_used: float
    created_at: float
    updated_at: float
    subscription_tier: str = "free"  # free, pro, enterprise
    tier_reset_at: Optional[float] = None  # When monthly reset happens


class DatabaseManager:
    """
    Simple SQLite-based database manager for download records and user credits,
    with a small, explicit migration framework.

    Usage:
        mgr = DatabaseManager(db_path=Path("..."))
        mgr.init_schema()   # ensures versioning table exists and runs migrations
    """

    def __init__(self, db_path: Optional[Path] = None, logger: Optional[logging.Logger] = None) -> None:
        self.logger = logger or _logger
        db_path = Path(db_path) if db_path is not None else (TEMP_DIR / _DEFAULT_DB_FILENAME)
        self.db_path: Path = db_path.resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        # Use RLock to allow reentrant calls (we sometimes call helper funcs that
        # themselves take the lock).
        self._lock = threading.RLock()

        # sqlite connection; allow sharing across threads but protect with a lock
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row

        self.logger.debug("DatabaseManager initialized with db_path=%s", str(self.db_path))

        # Register available migrations in code order (version -> migration function)
        # Migration functions should be idempotent (use CREATE TABLE IF NOT EXISTS, etc.)
        self._migrations: Dict[int, Callable[[], None]] = {
            1: self._migrate_1_create_downloads,
            2: self._migrate_2_create_users,
            3: self._migrate_3_create_audit_log,
            4: self._migrate_4_add_subscription,  # NEW
        }

    # ---------------------------
    # Schema version helpers
    # ---------------------------
    def init_schema(self) -> None:
        """
        Ensure the schema version table exists and run pending migrations.

        Calling this will ensure the database has the base tables required by the
        code (it will also apply migrations up to the latest available).
        """
        with self._lock:
            cur = self._conn.cursor()
            # Create a small table to track schema version; single-row table.
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_version (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    version INTEGER NOT NULL
                )
                """
            )
            # Ensure there's a row (version 0) if none present
            cur.execute("SELECT COUNT(1) as c FROM schema_version")
            row = cur.fetchone()
            if row is None or row["c"] == 0:
                cur.execute("INSERT INTO schema_version (id, version) VALUES (1, 0)")
            self._conn.commit()
            self.logger.debug("Schema version table ensured (db=%s)", self.db_path)

        # Run migrations up to the latest available (idempotent).
        # We call migrate() after releasing the lock above to avoid nested lock complexity.
        self.migrate()

    def get_schema_version(self) -> int:
        """Return current schema version (integer). Requires init_schema() first."""
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("SELECT version FROM schema_version WHERE id = 1")
            row = cur.fetchone()
            if row is None:
                # Shouldn't happen if init_schema was called, but be defensive
                return 0
            return int(row["version"])

    def set_schema_version(self, version: int) -> None:
        """Set the schema version (overwrite existing single row)."""
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("UPDATE schema_version SET version = ? WHERE id = 1", (int(version),))
            self._conn.commit()
            self.logger.info("Database schema version set to %d", version)

    def migrate(self, target_version: Optional[int] = None) -> int:
        """
        Apply pending migrations up to `target_version` (inclusive).
        - If target_version is None, migrate to the latest available migration.
        - Returns the final schema version after migration.

        NOTE: `init_schema()` will create the `schema_version` table and then call
        this method. If you call `migrate()` directly, ensure `init_schema()` was
        called beforehand.
        """
        # Do not call init_schema() here to avoid recursion when init_schema()
        # itself calls migrate().
        current = self.get_schema_version()
        max_available = max(self._migrations.keys()) if self._migrations else 0
        if target_version is None:
            target_version = max_available
        target_version = min(target_version, max_available)

        if current >= target_version:
            self.logger.debug("No migrations to run (current=%d target=%d)", current, target_version)
            return current

        self.logger.info("Starting migrations: current=%d target=%d", current, target_version)
        for v in range(current + 1, target_version + 1):
            migrate_fn = self._migrations.get(v)
            if migrate_fn is None:
                raise RuntimeError(f"No migration function defined for target version {v}")
            try:
                self.logger.info("Applying migration v%d ...", v)
                with self._lock:
                    migrate_fn()
                    # After successful migration, update schema_version
                    self.set_schema_version(v)
                self.logger.info("Migration v%d applied successfully", v)
            except Exception as exc:
                self.logger.exception("Migration v%d failed: %s", v, exc)
                # Stop on first failure; caller may retry after fix
                raise

        final = self.get_schema_version()
        self.logger.info("Migrations complete; final schema version = %d", final)
        return final

    # ---------------------------
    # Migration implementations
    # ---------------------------
    def _migrate_1_create_downloads(self) -> None:
        """
        Migration v1: create the downloads table (base schema).
        Idempotent: uses CREATE TABLE IF NOT EXISTS.
        """
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
        self.logger.debug("Migration 1: downloads table ensured")

    def _migrate_2_create_users(self) -> None:
        """
        Migration v2: add users table for credit tracking.
        Idempotent via CREATE TABLE IF NOT EXISTS.
        """
        cur = self._conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                credits REAL NOT NULL DEFAULT 3.0,
                total_used REAL NOT NULL DEFAULT 0.0,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
            """
        )
        self._conn.commit()
        self.logger.debug("Migration 2: users table ensured")

    def _migrate_3_create_audit_log(self) -> None:
        """
        Migration v3: add audit_log table for credit transaction tracking.
        Idempotent via CREATE TABLE IF NOT EXISTS.
        """
        cur = self._conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                action TEXT NOT NULL,
                old_value REAL,
                new_value REAL,
                reason TEXT,
                admin_id INTEGER,
                created_at REAL NOT NULL
            )
            """
        )
        self._conn.commit()
        self.logger.debug("Migration 3: audit_log table ensured")

    def _migrate_4_add_subscription(self) -> None:
        """
        Migration v4: add subscription tier columns to users table.
        Idempotent via ALTER TABLE IF NOT EXISTS.
        """
        cur = self._conn.cursor()
        try:
            cur.execute("ALTER TABLE users ADD COLUMN subscription_tier TEXT DEFAULT 'free'")
        except sqlite3.OperationalError:
            # Column already exists
            pass
        try:
            cur.execute("ALTER TABLE users ADD COLUMN tier_reset_at REAL")
        except sqlite3.OperationalError:
            # Column already exists
            pass
        self._conn.commit()
        self.logger.debug("Migration 4: subscription columns added to users table")

    # ---------------------------
    # Audit Logging (new feature)
    # ---------------------------
    def log_audit(
        self,
        user_id: int,
        action: str,
        old_value: Optional[float] = None,
        new_value: Optional[float] = None,
        reason: Optional[str] = None,
        admin_id: Optional[int] = None,
    ) -> None:
        """Log a credit transaction to audit table."""
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                INSERT INTO audit_log (user_id, action, old_value, new_value, reason, admin_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (user_id, action, old_value, new_value, reason, admin_id, self._now()),
            )
            self._conn.commit()
            self.logger.info(
                "Audit: user=%s action=%s old=%s new=%s reason=%s admin=%s",
                user_id, action, old_value, new_value, reason, admin_id,
            )

    def get_audit_log(self, user_id: int, limit: int = 100) -> List[Dict[str, Any]]:
        """Retrieve audit log for a user."""
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                "SELECT * FROM audit_log WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
                (user_id, limit),
            )
            rows = cur.fetchall()
            return [dict(row) for row in rows]

    # ---------------------------
    # Downloads API (existing)
    # ---------------------------
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
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("SELECT * FROM downloads WHERE id = ?", (record_id,))
            row = cur.fetchone()
            if row is None:
                self.logger.debug("Download record id=%s not found", record_id)
                return None
            return self._row_to_record(row)

    def update_download(self, record_id: int, **fields: Any) -> None:
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
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("SELECT * FROM downloads ORDER BY created_at DESC LIMIT ? OFFSET ?", (limit, offset))
            rows = cur.fetchall()
            return [self._row_to_record(r) for r in rows]

    def delete_download(self, record_id: int) -> None:
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

    # ---------------------------
    # Users / Credits API (existing)
    # ---------------------------
    def get_user_credits(self, user_id: int) -> UserCredit:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
            row = cur.fetchone()

            if row is None:
                created = self._now()
                cur.execute(
                    """
                    INSERT INTO users (user_id, credits, total_used, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (user_id, 3.0, 0.0, created, created),
                )
                self._conn.commit()
                cur.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
                row = cur.fetchone()

            return self._row_to_user_credit(row)

    def deduct_credits(self, user_id: int, amount: float) -> bool:
        """Deduct credits; returns True if successful, False if insufficient."""
        with self._lock:
            uc = self.get_user_credits(user_id)
            if uc.credits < amount:
                self.logger.warning("Insufficient credits: user=%s has %s, need %s", user_id, uc.credits, amount)
                return False
            new_balance = uc.credits - amount
            updatedAt = self._now()
            cur = self._conn.cursor()
            cur.execute(
                "UPDATE users SET credits = ?, updated_at = ?, total_used = total_used + ? WHERE user_id = ?",
                (new_balance, updatedAt, amount, user_id),
            )
            self._conn.commit()
            self.logger.info("Deducted %.2f credits from user=%s, new_balance=%.2f", amount, user_id, new_balance)
            # NEW: Audit log
            self.log_audit(user_id, "deduct", old_value=uc.credits, new_value=new_balance, reason="Localization job")
            return True

    def add_credits(self, user_id: int, amount: float) -> float:
        """Add credits to user account; returns new balance."""
        with self._lock:
            uc = self.get_user_credits(user_id)
            new_balance = uc.credits + amount
            updatedAt = self._now()
            cur = self._conn.cursor()
            cur.execute(
                "UPDATE users SET credits = ?, updated_at = ? WHERE user_id = ?",
                (new_balance, updatedAt, user_id),
            )
            self._conn.commit()
            self.logger.info("Added %.2f credits to user=%s, new_balance=%.2f", amount, user_id, new_balance)
            # NEW: Audit log
            self.log_audit(user_id, "add", old_value=uc.credits, new_value=new_balance)
            return new_balance

    def set_user_credits(self, user_id: int, new_balance: float, reason: Optional[str] = None,
                         admin_id: Optional[int] = None) -> float:
        """Set user balance to exact amount (admin operation)."""
        with self._lock:
            uc = self.get_user_credits(user_id)
            old_balance = uc.credits
            updatedAt = self._now()
            cur = self._conn.cursor()
            cur.execute(
                "UPDATE users SET credits = ?, updated_at = ? WHERE user_id = ?",
                (new_balance, updatedAt, user_id),
            )
            self._conn.commit()
            self.logger.info("Set user=%s balance from %.2f to %.2f", user_id, old_balance, new_balance)
            # NEW: Audit log with admin tracking
            self.log_audit(user_id, "set", old_value=old_balance, new_value=new_balance,
                          reason=reason or "Admin adjustment", admin_id=admin_id)
            return new_balance

    def set_subscription_tier(self, user_id: int, tier: str) -> None:
        """Set user subscription tier (free, pro, enterprise)."""
        if tier not in ["free", "pro", "enterprise"]:
            raise ValueError(f"Invalid subscription tier: {tier}")

        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                "UPDATE users SET subscription_tier = ?, updated_at = ? WHERE user_id = ?",
                (tier, self._now(), user_id),
            )
            self._conn.commit()
            self.logger.info("Set subscription tier for user=%s to %s", user_id, tier)
            self.log_audit(user_id, "tier_change", reason=f"Changed to {tier}")

    def get_monthly_credit_limit(self, user_id: int) -> float:
        """Get monthly credit limit based on subscription tier."""
        uc = self.get_user_credits(user_id)
        tier_limits = {
            "free": 3.0,
            "pro": 100.0,
            "enterprise": 999999.0,
        }
        return tier_limits.get(uc.subscription_tier, 3.0)

    def reset_monthly_credits(self, user_id: int) -> None:
        """Reset credits to monthly limit (admin operation)."""
        limit = self.get_monthly_credit_limit(user_id)
        self.set_user_credits(user_id, limit, reason="Monthly reset")
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                "UPDATE users SET tier_reset_at = ? WHERE user_id = ?",
                (self._now(), user_id),
            )
            self._conn.commit()
        self.logger.info("Monthly credits reset for user=%s to %.2f", user_id, limit)

    def _row_to_user_credit(self, row: sqlite3.Row) -> UserCredit:
        """Convert sqlite3.Row to UserCredit dataclass."""
        row_dict = dict(row)
        return UserCredit(
            user_id=int(row_dict["user_id"]),
            credits=float(row_dict["credits"]),
            total_used=float(row_dict["total_used"]),
            created_at=float(row_dict["created_at"]),
            updated_at=float(row_dict["updated_at"]),
            subscription_tier=row_dict.get("subscription_tier", "free") or "free",
            tier_reset_at=row_dict.get("tier_reset_at"),
        )