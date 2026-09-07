"""Canonical channel-source registry, scan decisions, and nested attempts."""
from __future__ import annotations

import json
import re
import time
from collections import defaultdict
from typing import Any, Callable, Iterable, Optional
from urllib.parse import parse_qs, urlparse

from universal_video_ai.web.store import Store


SOURCE_SCHEMA = """
CREATE TABLE IF NOT EXISTS channel_source_videos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    platform TEXT NOT NULL,
    channel_id TEXT,
    channel_url TEXT,
    external_video_id TEXT NOT NULL,
    canonical_url TEXT NOT NULL,
    title TEXT,
    status TEXT NOT NULL DEFAULT 'NEW',
    download_status TEXT NOT NULL DEFAULT 'new',
    queue_status TEXT NOT NULL DEFAULT 'none',
    last_error TEXT,
    local_asset_path TEXT,
    logical_job_id TEXT,
    first_seen_at REAL NOT NULL,
    last_seen_at REAL NOT NULL,
    last_attempt_at REAL,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    archived INTEGER NOT NULL DEFAULT 0,
    UNIQUE(user_id, platform, external_video_id)
);
CREATE INDEX IF NOT EXISTS idx_channel_sources_owner_channel
    ON channel_source_videos(user_id, platform, channel_id, last_seen_at DESC);

CREATE TABLE IF NOT EXISTS channel_source_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    source_video_id INTEGER NOT NULL,
    attempt_number INTEGER NOT NULL,
    execution_mode TEXT NOT NULL,
    status TEXT NOT NULL,
    job_id TEXT,
    error TEXT,
    started_at REAL NOT NULL,
    completed_at REAL,
    archived INTEGER NOT NULL DEFAULT 0,
    UNIQUE(user_id, source_video_id, attempt_number)
);
CREATE INDEX IF NOT EXISTS idx_channel_source_attempts_owner
    ON channel_source_attempts(user_id, source_video_id, attempt_number DESC);

CREATE TABLE IF NOT EXISTS job_execution_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    job_id TEXT NOT NULL,
    attempt_number INTEGER NOT NULL,
    mode TEXT NOT NULL,
    reset_stages_json TEXT NOT NULL DEFAULT '[]',
    reused_outputs_json TEXT NOT NULL DEFAULT '{}',
    repeat_publish INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'queued',
    error TEXT,
    created_at REAL NOT NULL,
    completed_at REAL,
    UNIQUE(user_id, job_id, attempt_number)
);
CREATE INDEX IF NOT EXISTS idx_job_execution_attempts_owner
    ON job_execution_attempts(user_id, job_id, attempt_number DESC);
"""


YOUTUBE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{6,20}$")


class ChannelSourceError(RuntimeError):
    pass


def youtube_video_id(value: str) -> Optional[str]:
    raw = str(value or "").strip()
    if not raw:
        return None
    if YOUTUBE_ID_RE.fullmatch(raw) and "/" not in raw:
        return raw
    parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    host = parsed.netloc.lower().split(":", 1)[0]
    candidate = ""
    if host in {"youtu.be", "www.youtu.be"}:
        candidate = parsed.path.strip("/").split("/", 1)[0]
    elif host.endswith("youtube.com"):
        if parsed.path.rstrip("/") == "/watch":
            candidate = (parse_qs(parsed.query).get("v") or [""])[0]
        else:
            parts = [part for part in parsed.path.split("/") if part]
            if len(parts) >= 2 and parts[0] in {"shorts", "live", "embed", "v"}:
                candidate = parts[1]
    candidate = candidate.strip()
    return candidate if YOUTUBE_ID_RE.fullmatch(candidate) else None


def canonical_source_url(platform: str, external_video_id: str, fallback: str = "") -> str:
    if platform.lower() == "youtube":
        return f"https://www.youtube.com/watch?v={external_video_id}"
    return str(fallback or "").strip()


class ChannelSourceRegistry:
    def __init__(self, store: Store, *, now: Callable[[], float] = time.time) -> None:
        self.store, self.now = store, now
        self.ensure_schema()

    def ensure_schema(self) -> None:
        with self.store._connect() as conn:
            conn.executescript(SOURCE_SCHEMA)
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
            additions = {
                "source_external_video_id": "ALTER TABLE jobs ADD COLUMN source_external_video_id TEXT",
                "source_platform": "ALTER TABLE jobs ADD COLUMN source_platform TEXT",
                "source_logical_id": "ALTER TABLE jobs ADD COLUMN source_logical_id INTEGER",
                "history_archived": "ALTER TABLE jobs ADD COLUMN history_archived INTEGER NOT NULL DEFAULT 0",
                "execution_attempt": "ALTER TABLE jobs ADD COLUMN execution_attempt INTEGER NOT NULL DEFAULT 1",
            }
            for name, ddl in additions.items():
                if name not in columns:
                    conn.execute(ddl)

    @staticmethod
    def _row(row: Any) -> dict[str, Any]:
        return dict(row)

    def get(self, user_id: int, source_id: int) -> Optional[dict[str, Any]]:
        with self.store._connect() as conn:
            row = conn.execute(
                "SELECT * FROM channel_source_videos WHERE user_id=? AND id=?", (user_id, source_id),
            ).fetchone()
        return self._row(row) if row else None

    def get_identity(self, user_id: int, platform: str, external_video_id: str) -> Optional[dict[str, Any]]:
        with self.store._connect() as conn:
            row = conn.execute(
                "SELECT * FROM channel_source_videos WHERE user_id=? AND platform=? AND external_video_id=?",
                (user_id, platform.lower(), external_video_id),
            ).fetchone()
        return self._row(row) if row else None

    def discover(self, user_id: int, *, platform: str, channel_id: str,
                 channel_url: str, videos: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
        platform = platform.lower()
        now, seen, output = self.now(), set(), []
        with self.store._connect() as conn:
            legacy_by_id: dict[str, Any] = {}
            if platform == "youtube":
                legacy_rows = conn.execute(
                    "SELECT * FROM jobs WHERE user_id=? AND COALESCE(history_archived,0)=0 "
                    "ORDER BY created_at DESC",
                    (user_id,),
                ).fetchall()
                priority = {"done": 5, "running": 4, "review": 3, "queued": 2, "error": 1, "cancelled": 0}
                for legacy in legacy_rows:
                    external = str(legacy["source_external_video_id"] or "").strip()
                    external = external or youtube_video_id(legacy["source_url"]) or ""
                    prior = legacy_by_id.get(external)
                    if external and (
                        prior is None
                        or priority.get(str(legacy["status"]), -1) > priority.get(str(prior["status"]), -1)
                    ):
                        legacy_by_id[external] = legacy
            for video in videos:
                source_url = str(video.get("source_url") or video.get("url") or "")
                external_id = str(video.get("video_id") or "").strip()
                if platform == "youtube":
                    external_id = external_id or youtube_video_id(source_url) or ""
                if not external_id:
                    continue
                identity = (platform, external_id)
                if identity in seen:
                    continue
                seen.add(identity)
                canonical = canonical_source_url(platform, external_id, source_url)
                existed = conn.execute(
                    "SELECT id FROM channel_source_videos WHERE user_id=? AND platform=? AND external_video_id=?",
                    (user_id, platform, external_id),
                ).fetchone()
                conn.execute(
                    "INSERT INTO channel_source_videos "
                    "(user_id,platform,channel_id,channel_url,external_video_id,canonical_url,title,first_seen_at,last_seen_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(user_id,platform,external_video_id) DO UPDATE SET "
                    "channel_id=excluded.channel_id,channel_url=excluded.channel_url,canonical_url=excluded.canonical_url,"
                    "title=COALESCE(NULLIF(excluded.title,''),channel_source_videos.title),last_seen_at=excluded.last_seen_at",
                    (user_id, platform, channel_id, channel_url, external_id, canonical,
                     str(video.get("title") or video.get("desc") or ""), now, now),
                )
                legacy = legacy_by_id.get(external_id) if not existed else None
                if legacy is not None:
                    job_status = str(legacy["status"])
                    source_status, download_status, queue_status = {
                        "done": ("SUCCESS", "success", "done"),
                        "running": ("PROCESSING", "processing", "running"),
                        "review": ("PROCESSING", "success", "review"),
                        "queued": ("QUEUED", "new", "queued"),
                        "error": ("FAILED", "failed", "error"),
                        "cancelled": ("SKIPPED", "new", "cancelled"),
                    }.get(job_status, ("NEW", "new", "none"))
                    conn.execute(
                        "UPDATE channel_source_videos SET status=?,download_status=?,queue_status=?,last_error=?,"
                        "local_asset_path=?,logical_job_id=?,attempt_count=CASE WHEN ?='NEW' THEN 0 ELSE 1 END "
                        "WHERE user_id=? AND platform=? AND external_video_id=?",
                        (source_status, download_status, queue_status, legacy["error"],
                         legacy["source_video_path"], legacy["id"], source_status,
                         user_id, platform, external_id),
                    )
                    conn.execute(
                        "UPDATE jobs SET source_external_video_id=?,source_platform=?,"
                        "source_logical_id=(SELECT id FROM channel_source_videos WHERE user_id=? AND platform=? AND external_video_id=?) "
                        "WHERE user_id=? AND id=?",
                        (external_id, platform, user_id, platform, external_id, user_id, legacy["id"]),
                    )
                row = conn.execute(
                    "SELECT * FROM channel_source_videos WHERE user_id=? AND platform=? AND external_video_id=?",
                    (user_id, platform, external_id),
                ).fetchone()
                output.append(self._row(row))
        return output

    @staticmethod
    def decision(row: dict[str, Any], *, retry_failed: bool = True,
                 max_attempts: int = 3) -> str:
        status = str(row.get("status") or "NEW").upper()
        download = str(row.get("download_status") or "new").lower()
        queue = str(row.get("queue_status") or "none").lower()
        if status == "SKIPPED":
            return "SKIPPED"
        if download == "success" or queue in {"done", "completed", "success"} or status == "SUCCESS":
            return "SUCCESS"
        if download in {"processing", "downloading"} or queue in {"running", "processing", "review"}:
            return "PROCESSING"
        if queue == "queued" or status == "QUEUED":
            return "QUEUED"
        if download == "failed" or queue in {"failed", "error"} or status == "FAILED":
            if retry_failed and int(row.get("attempt_count") or 0) < max_attempts:
                return "RETRYABLE_FAILED"
            return "FAILED"
        return "NEW"

    def preflight(self, rows: Iterable[dict[str, Any]], *, retry_failed: bool = True,
                  max_attempts: int = 3) -> dict[str, Any]:
        unique = {int(row["id"]): row for row in rows}
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in unique.values():
            grouped[self.decision(row, retry_failed=retry_failed, max_attempts=max_attempts)].append(row)
        return {
            "discovered": len(unique),
            "new": len(grouped["NEW"]),
            "already_queued": len(grouped["QUEUED"]),
            "processing": len(grouped["PROCESSING"]),
            "already_successful": len(grouped["SUCCESS"]),
            "failed_retryable": len(grouped["RETRYABLE_FAILED"]),
            "failed": len(grouped["FAILED"]),
            "skipped": len(grouped["SKIPPED"]),
            "eligible": grouped["NEW"] + grouped["RETRYABLE_FAILED"],
        }

    def begin_attempt(self, user_id: int, source_id: int, *, mode: str,
                      job_id: Optional[str] = None) -> dict[str, Any]:
        now = self.now()
        with self.store._connect() as conn:
            row = conn.execute(
                "SELECT * FROM channel_source_videos WHERE user_id=? AND id=?", (user_id, source_id),
            ).fetchone()
            if not row:
                raise ChannelSourceError("Channel source video not found.")
            number = int(row["attempt_count"] or 0) + 1
            conn.execute(
                "UPDATE channel_source_videos SET status='PROCESSING',download_status='processing',queue_status=?,"
                "logical_job_id=COALESCE(?,logical_job_id),attempt_count=?,last_attempt_at=?,last_error=NULL WHERE user_id=? AND id=?",
                ("queued" if job_id else "processing", job_id, number, now, user_id, source_id),
            )
            cur = conn.execute(
                "INSERT INTO channel_source_attempts "
                "(user_id,source_video_id,attempt_number,execution_mode,status,job_id,started_at) VALUES (?,?,?,?,?,?,?)",
                (user_id, source_id, number, mode, "running", job_id, now),
            )
            attempt = conn.execute("SELECT * FROM channel_source_attempts WHERE id=?", (cur.lastrowid,)).fetchone()
        return self._row(attempt)

    def link_job(self, user_id: int, source_id: int, job_id: str) -> None:
        source = self.get(user_id, source_id)
        if not source:
            raise ChannelSourceError("Channel source video not found.")
        with self.store._connect() as conn:
            conn.execute(
                "UPDATE channel_source_videos SET logical_job_id=?,queue_status='queued',status='QUEUED' WHERE user_id=? AND id=?",
                (job_id, user_id, source_id),
            )
            conn.execute(
                "UPDATE jobs SET source_external_video_id=?,source_platform=?,source_logical_id=? "
                "WHERE user_id=? AND id=?",
                (source["external_video_id"], source["platform"], source_id, user_id, job_id),
            )

    def finish(self, user_id: int, source_id: int, *, success: bool,
               job_id: Optional[str] = None, local_asset_path: Optional[str] = None,
               error: Optional[str] = None) -> dict[str, Any]:
        now = self.now()
        with self.store._connect() as conn:
            row = conn.execute(
                "SELECT * FROM channel_source_videos WHERE user_id=? AND id=?", (user_id, source_id),
            ).fetchone()
            if not row:
                raise ChannelSourceError("Channel source video not found.")
            status = "SUCCESS" if success else "FAILED"
            conn.execute(
                "UPDATE channel_source_videos SET status=?,download_status=?,queue_status=?,last_error=?,"
                "local_asset_path=COALESCE(?,local_asset_path),logical_job_id=COALESCE(?,logical_job_id),last_attempt_at=? "
                "WHERE user_id=? AND id=?",
                (status, "success" if success else "failed", "done" if success else "error",
                 None if success else error, local_asset_path, job_id, now, user_id, source_id),
            )
            conn.execute(
                "UPDATE channel_source_attempts SET status=?,error=?,completed_at=? WHERE user_id=? AND source_video_id=? "
                "AND attempt_number=(SELECT MAX(attempt_number) FROM channel_source_attempts WHERE user_id=? AND source_video_id=?)",
                ("success" if success else "failed", None if success else error, now,
                 user_id, source_id, user_id, source_id),
            )
        return self.get(user_id, source_id) or {}

    def attempts(self, user_id: int, source_id: int) -> list[dict[str, Any]]:
        if not self.get(user_id, source_id):
            raise ChannelSourceError("Channel source video not found.")
        with self.store._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM channel_source_attempts WHERE user_id=? AND source_video_id=? ORDER BY attempt_number DESC",
                (user_id, source_id),
            ).fetchall()
        return [self._row(row) for row in rows]

    def skip(self, user_id: int, source_id: int, *, reason: str = "cancelled") -> dict[str, Any]:
        now = self.now()
        with self.store._connect() as conn:
            cur = conn.execute(
                "UPDATE channel_source_videos SET status='SKIPPED',queue_status='cancelled',"
                "last_error=?,last_attempt_at=? WHERE user_id=? AND id=?",
                (reason, now, user_id, source_id),
            )
            if not cur.rowcount:
                raise ChannelSourceError("Channel source video not found.")
            conn.execute(
                "UPDATE channel_source_attempts SET status='cancelled',error=?,completed_at=? "
                "WHERE user_id=? AND source_video_id=? AND status='running'",
                (reason, now, user_id, source_id),
            )
        return self.get(user_id, source_id) or {}

    def list_visible(self, user_id: int, *, channel_id: Optional[str] = None) -> list[dict[str, Any]]:
        sql, params = "SELECT * FROM channel_source_videos WHERE user_id=? AND archived=0", [user_id]
        if channel_id:
            sql += " AND channel_id=?"
            params.append(channel_id)
        sql += " ORDER BY last_seen_at DESC"
        with self.store._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        output = []
        for row in rows:
            item = self._row(row)
            item["decision"] = self.decision(item)
            item["attempts"] = self.attempts(user_id, int(item["id"]))
            output.append(item)
        return output

    def compact_legacy_failed_jobs(self, user_id: int) -> dict[str, int]:
        """Archive only provable duplicate YouTube failures; successful rows stay visible."""
        with self.store._connect() as conn:
            rows = conn.execute(
                "SELECT id,source_url,status,created_at FROM jobs WHERE user_id=? AND COALESCE(history_archived,0)=0",
                (user_id,),
            ).fetchall()
            groups: dict[str, list[Any]] = defaultdict(list)
            for row in rows:
                external_id = youtube_video_id(row["source_url"])
                if external_id:
                    groups[external_id].append(row)
            archived = 0
            for external_id, group in groups.items():
                if len(group) < 2:
                    continue
                successful = [row for row in group if row["status"] == "done"]
                keeper = max(successful or group, key=lambda row: float(row["created_at"] or 0))
                for row in group:
                    if row["id"] == keeper["id"] or row["status"] == "done":
                        continue
                    if row["status"] != "error":
                        continue
                    conn.execute(
                        "UPDATE jobs SET history_archived=1,source_external_video_id=?,source_platform='youtube' WHERE user_id=? AND id=?",
                        (external_id, user_id, row["id"]),
                    )
                    archived += 1
        return {"groups": sum(len(group) > 1 for group in groups.values()), "archived_failed": archived}

    def create_job_attempt(self, user_id: int, job_id: str, *, mode: str,
                           reset_stages: list[str], reused_outputs: dict[str, Any],
                           repeat_publish: bool = False) -> dict[str, Any]:
        if mode not in {"RETRY_FROM_FAILED_STAGE", "RESTART_FROM_BEGINNING"}:
            raise ChannelSourceError("Unknown pipeline rerun mode.")
        now = self.now()
        with self.store._connect() as conn:
            job = conn.execute("SELECT * FROM jobs WHERE user_id=? AND id=?", (user_id, job_id)).fetchone()
            if not job:
                raise ChannelSourceError("Job not found.")
            number = int(job["execution_attempt"] or 1) + 1
            conn.execute(
                "UPDATE jobs SET execution_attempt=?,updated_at=? WHERE user_id=? AND id=?",
                (number, now, user_id, job_id),
            )
            cur = conn.execute(
                "INSERT INTO job_execution_attempts "
                "(user_id,job_id,attempt_number,mode,reset_stages_json,reused_outputs_json,repeat_publish,status,created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (user_id, job_id, number, mode, json.dumps(reset_stages), json.dumps(reused_outputs),
                 int(repeat_publish), "queued", now),
            )
            row = conn.execute("SELECT * FROM job_execution_attempts WHERE id=?", (cur.lastrowid,)).fetchone()
        item = self._row(row)
        item["reset_stages"] = json.loads(item.pop("reset_stages_json"))
        item["reused_outputs"] = json.loads(item.pop("reused_outputs_json"))
        item["repeat_publish"] = bool(item["repeat_publish"])
        return item

    def job_attempts(self, user_id: int, job_id: str) -> list[dict[str, Any]]:
        with self.store._connect() as conn:
            owner = conn.execute("SELECT 1 FROM jobs WHERE user_id=? AND id=?", (user_id, job_id)).fetchone()
            if not owner:
                raise ChannelSourceError("Job not found.")
            rows = conn.execute(
                "SELECT * FROM job_execution_attempts WHERE user_id=? AND job_id=? ORDER BY attempt_number DESC",
                (user_id, job_id),
            ).fetchall()
        output = []
        for row in rows:
            item = self._row(row)
            item["reset_stages"] = json.loads(item.pop("reset_stages_json"))
            item["reused_outputs"] = json.loads(item.pop("reused_outputs_json"))
            item["repeat_publish"] = bool(item["repeat_publish"])
            output.append(item)
        return output
