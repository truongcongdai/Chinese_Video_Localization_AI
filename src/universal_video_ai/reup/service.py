"""Durable reup runs using localization callbacks, never creator production."""
from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Any

from universal_video_ai.web.store import Store

__all__ = ["ReupRepository"]
_logger = logging.getLogger(__name__)
_SCHEMA = """
CREATE TABLE IF NOT EXISTS reup_profiles (
 user_id INTEGER PRIMARY KEY REFERENCES users(id), config TEXT NOT NULL,
 enabled INTEGER NOT NULL DEFAULT 0, next_scan REAL NOT NULL DEFAULT 0, updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS reup_runs (
 id TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), channel_id TEXT NOT NULL,
 source_key TEXT NOT NULL, source_url TEXT NOT NULL, title TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'discovered', job_id TEXT, config TEXT NOT NULL,
 refs TEXT NOT NULL DEFAULT '{}', error TEXT, approved INTEGER NOT NULL DEFAULT 0,
 created_at REAL NOT NULL, updated_at REAL NOT NULL,
 UNIQUE(user_id, channel_id, source_key)
);
CREATE INDEX IF NOT EXISTS reup_runs_owner ON reup_runs(user_id,created_at);
CREATE TABLE IF NOT EXISTS reup_discovery_state (
 user_id INTEGER PRIMARY KEY REFERENCES users(id), report TEXT NOT NULL, updated_at REAL NOT NULL
);
"""


class ReupRepository:
    def __init__(self, store: Store) -> None:
        self.store = store
        with store._connect() as conn:
            conn.executescript(_SCHEMA)
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(reup_runs)")}
            for name, definition in (("next_action_at", "REAL NOT NULL DEFAULT 0"), ("attempt", "INTEGER NOT NULL DEFAULT 1")):
                if name not in columns:
                    conn.execute(f"ALTER TABLE reup_runs ADD COLUMN {name} {definition}")

    def save_profile(self, user_id: int, config: dict) -> dict:
        with self.store._connect() as conn:
            conn.execute("""INSERT INTO reup_profiles(user_id,config,enabled,next_scan,updated_at)
                VALUES(?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET config=excluded.config,
                enabled=excluded.enabled,next_scan=0,updated_at=excluded.updated_at""",
                         (user_id, json.dumps(config, ensure_ascii=False), int(config.get("enabled", False)), 0, time.time()))
        return config

    def profile(self, user_id: int) -> dict:
        with self.store._connect() as conn:
            row = conn.execute("SELECT config FROM reup_profiles WHERE user_id=?", (user_id,)).fetchone()
        return json.loads(row["config"]) if row else {}

    def save_discovery(self, user_id: int, report: dict) -> None:
        with self.store._connect() as conn:
            conn.execute('INSERT OR REPLACE INTO reup_discovery_state VALUES(?,?,?)',
                         (user_id, json.dumps(report, ensure_ascii=False), time.time()))

    def discovery(self, user_id: int, *, include_completed_at: bool = False) -> dict:
        with self.store._connect() as conn:
            row = conn.execute('SELECT report,updated_at FROM reup_discovery_state WHERE user_id=?', (user_id,)).fetchone()
        if not row:
            return {}
        report = json.loads(row[0])
        return {**report, "completed_at": row[1]} if include_completed_at else report

    def due_profiles(self, at: float) -> list[tuple[int, dict]]:
        # Claim next scan before network work; no overlapping periodic scan.
        with self.store._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute("SELECT * FROM reup_profiles WHERE enabled=1 AND next_scan<=?", (at,)).fetchall()
            result = []
            for row in rows:
                config = json.loads(row["config"])
                conn.execute("UPDATE reup_profiles SET next_scan=? WHERE user_id=?",
                             (at + max(900, config.get("scan_interval_minutes", 60) * 60), row["user_id"]))
                result.append((row["user_id"], config))
        return result

    def enqueue(self, user_id: int, candidate: dict, config: dict, *, restart_cancelled: bool = False) -> dict | None:
        from .discovery import source_identity
        platform, identity, canonical = source_identity(candidate["url"])
        key = f"{platform}:{identity}"
        channel = config.get("channel_id") or "unconnected"
        at = time.time()
        zone = ZoneInfo(config.get("timezone", "Asia/Bangkok"))
        local = datetime.fromtimestamp(at, zone).replace(hour=0, minute=0, second=0, microsecond=0)
        begin, end = local.timestamp(), (local + timedelta(days=1)).timestamp()
        with self.store._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            prior = conn.execute("SELECT * FROM reup_runs WHERE user_id=? AND channel_id=? AND source_key=?",
                                 (user_id, channel, key)).fetchone()
            restart = prior is not None and restart_cancelled and prior["status"] == "cancelled"
            if restart:
                refs = json.loads(prior["refs"])
                restart = not refs.get("external_video_id") and not refs.get("upload_session_uri")
            if prior and not restart:
                return self._row(prior)
            count = conn.execute("SELECT COUNT(*) FROM reup_runs WHERE user_id=? AND channel_id=? AND created_at>=? AND created_at<? AND status NOT IN ('cancelled','failed')",
                                 (user_id, channel, begin, end)).fetchone()[0]
            if count >= config.get("daily_limit", 1):
                return None
            if restart:
                conn.execute("""UPDATE reup_runs SET status='discovered', job_id=NULL,
                    refs='{}', error=NULL, approved=0, config=?, title=?,
                    attempt=attempt+1, next_action_at=0, created_at=?, updated_at=? WHERE id=? AND user_id=?""",
                    (json.dumps(config, ensure_ascii=False), str(candidate.get("title") or prior["title"])[:500],
                     at, at, prior["id"], user_id))
                return self._row(conn.execute("SELECT * FROM reup_runs WHERE id=?", (prior["id"],)).fetchone())
            run_id = uuid.uuid4().hex
            conn.execute("""INSERT INTO reup_runs(id,user_id,channel_id,source_key,source_url,title,config,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?)""", (run_id, user_id, channel, key, canonical,
                        str(candidate.get("title") or "")[:500], json.dumps(config, ensure_ascii=False), at, at))
            row = conn.execute("SELECT * FROM reup_runs WHERE id=?", (run_id,)).fetchone()
        return self._row(row)

    @staticmethod
    def _row(row: Any, *, private: bool = False) -> dict:
        result = dict(row)
        result["config"] = json.loads(result["config"])
        result["refs"] = json.loads(result["refs"])
        if not private:
            result["refs"].pop("upload_session_uri", None)
        return result

    def get(self, user_id: int, run_id: str, *, private: bool = False) -> dict:
        with self.store._connect() as conn:
            row = conn.execute("SELECT * FROM reup_runs WHERE id=? AND user_id=?", (run_id, user_id)).fetchone()
        if not row:
            raise LookupError("Reup run not found.")
        return self._row(row, private=private)

    def list(self, user_id: int) -> list[dict]:
        with self.store._connect() as conn:
            rows = conn.execute("SELECT * FROM reup_runs WHERE user_id=? ORDER BY created_at DESC LIMIT 100", (user_id,)).fetchall()
        return [self._row(row) for row in rows]

    def active(self, *, at: float | None = None, limit: int = 20) -> list[tuple[int, str]]:
        """Rotate due work; approval waits and disabled profiles consume no capacity."""
        at = time.time() if at is None else at
        with self.store._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute("""SELECT r.user_id,r.id FROM reup_runs r
                LEFT JOIN reup_profiles p ON p.user_id=r.user_id
                WHERE r.status IN ('queued','localizing','packaging','ready','publishing','processing','scheduled')
                  AND r.next_action_at<=?
                  AND (COALESCE(json_extract(r.refs,'$.trigger'),'manual')!='automatic' OR COALESCE(p.enabled,0)=1)
                  AND (r.status!='ready' OR r.approved=1 OR json_extract(r.config,'$.automation')='auto')
                  AND (r.status!='scheduled' OR COALESCE(unixepoch(json_extract(r.refs,'$.schedule_utc')),0)<=?)
                ORDER BY r.next_action_at,r.updated_at,r.id LIMIT ?""", (at, at, max(1, min(limit, 100)))).fetchall()
            conn.executemany("UPDATE reup_runs SET next_action_at=? WHERE id=? AND user_id=?",
                             [(at + 30, row["id"], row["user_id"]) for row in rows])
        return [(row["user_id"], row["id"]) for row in rows]

    def due_metrics(self, at: float) -> list[tuple[int, str]]:
        with self.store._connect() as conn:
            rows = conn.execute("SELECT user_id,id FROM reup_runs WHERE status IN ('published','uploaded_private') AND updated_at<? ORDER BY updated_at LIMIT 10",
                                (at - 86400,)).fetchall()
        return [(row["user_id"], row["id"]) for row in rows]

    def update(self, user_id: int, run_id: str, *, status: str | None = None, job_id: str | None = None,
               refs: dict | None = None, error: str | None = None, approved: bool | None = None,
               config: dict | None = None) -> dict:
        values: dict = {"updated_at": time.time(), "error": error}
        if status is not None:
            values["status"] = status
            values["next_action_at"] = 0
        if job_id is not None:
            values["job_id"] = job_id
        if refs is not None:
            values["refs"] = json.dumps(refs, ensure_ascii=False)
        if approved is not None:
            values["approved"] = int(approved)
        if config is not None:
            values["config"] = json.dumps(config, ensure_ascii=False)
        with self.store._connect() as conn:
            conn.execute(f"UPDATE reup_runs SET {','.join(f'{key}=?' for key in values)} WHERE id=? AND user_id=?",
                         (*values.values(), run_id, user_id))
        return self.get(user_id, run_id)

    @staticmethod
    def localization_id(run: dict) -> str:
        identity = f"reup:{run['user_id']}:{run['id']}"
        if run.get("attempt", 1) > 1:
            identity += f":attempt:{run['attempt']}"
        return hashlib.sha256(identity.encode()).hexdigest()[:12]
