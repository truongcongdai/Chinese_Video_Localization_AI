from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import time
from typing import Any, Optional

from universal_video_ai.analytics.youtube_research.schemas import ResearchVideo

from .manager import DatabaseManager


@dataclass(frozen=True)
class YouTubeResearchProject:
    id: Optional[int]
    user_id: int
    niche: str
    keyword: str
    target_language: Optional[str]
    target_country: Optional[str]
    created_at: float
    updated_at: float
    metadata: dict[str, Any]


class YouTubeResearchRepository:
    """Tenant-scoped repository using DatabaseManager's SQLite connection."""

    def __init__(self, manager: DatabaseManager) -> None:
        self.manager = manager

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)

    @staticmethod
    def _load_json(value: Any) -> dict[str, Any]:
        try:
            parsed = json.loads(value or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}

    @classmethod
    def _project_from_row(cls, row: Any) -> YouTubeResearchProject:
        return YouTubeResearchProject(
            id=int(row["id"]),
            user_id=int(row["user_id"]),
            niche=str(row["niche"]),
            keyword=str(row["keyword"]),
            target_language=row["target_language"],
            target_country=row["target_country"],
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
            metadata=cls._load_json(row["metadata"]),
        )

    def create_project(
        self,
        user_id: int,
        niche: str,
        keyword: str,
        target_language: Optional[str] = None,
        target_country: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> int:
        now = time.time()
        with self.manager._lock:
            cur = self.manager._conn.execute(
                """
                INSERT INTO youtube_research_projects
                    (user_id, niche, keyword, target_language, target_country,
                     created_at, updated_at, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(user_id), niche, keyword, target_language, target_country,
                    now, now, self._json(metadata or {}),
                ),
            )
            self.manager._conn.commit()
            return int(cur.lastrowid)

    def get_project(self, project_id: int, user_id: int) -> Optional[YouTubeResearchProject]:
        with self.manager._lock:
            row = self.manager._conn.execute(
                "SELECT * FROM youtube_research_projects WHERE id = ? AND user_id = ?",
                (int(project_id), int(user_id)),
            ).fetchone()
        return self._project_from_row(row) if row is not None else None

    def list_projects(self, user_id: int, limit: int = 100) -> list[YouTubeResearchProject]:
        with self.manager._lock:
            rows = self.manager._conn.execute(
                "SELECT * FROM youtube_research_projects WHERE user_id = ? "
                "ORDER BY updated_at DESC, id DESC LIMIT ?",
                (int(user_id), max(1, min(int(limit), 500))),
            ).fetchall()
        return [self._project_from_row(row) for row in rows]

    def update_project(
        self,
        project_id: int,
        user_id: int,
        *,
        niche: str,
        keyword: str,
        target_language: Optional[str],
        target_country: Optional[str],
        metadata: Optional[dict[str, Any]] = None,
    ) -> bool:
        with self.manager._lock:
            cur = self.manager._conn.execute(
                """
                UPDATE youtube_research_projects
                SET niche = ?, keyword = ?, target_language = ?, target_country = ?,
                    metadata = COALESCE(?, metadata), updated_at = ?
                WHERE id = ? AND user_id = ?
                """,
                (
                    niche, keyword, target_language, target_country,
                    self._json(metadata) if metadata is not None else None,
                    time.time(), int(project_id), int(user_id),
                ),
            )
            self.manager._conn.commit()
            return cur.rowcount > 0

    def create_search_execution(
        self,
        project_id: int,
        user_id: int,
        *,
        query: str,
        max_results: int,
    ) -> int:
        with self.manager._lock:
            cur = self.manager._conn.execute(
                """
                INSERT INTO youtube_research_sources
                    (project_id, source_type, source_ref, collected_at, metadata,
                     status, max_results)
                SELECT id, 'youtube_search', ?, ?, '{}', 'running', ?
                FROM youtube_research_projects
                WHERE id = ? AND user_id = ?
                """,
                (query, time.time(), int(max_results), int(project_id), int(user_id)),
            )
            if cur.rowcount != 1:
                self.manager._conn.rollback()
                raise LookupError("research project not found")
            self.manager._conn.commit()
            return int(cur.lastrowid)

    def finish_search_execution(
        self,
        source_id: int,
        project_id: int,
        user_id: int,
        *,
        status: str,
        result_count: int,
        error: Optional[str] = None,
    ) -> bool:
        with self.manager._lock:
            cur = self.manager._conn.execute(
                """
                UPDATE youtube_research_sources
                SET status = ?, result_count = ?, error = ?, completed_at = ?
                WHERE id = ? AND project_id = ?
                  AND EXISTS (
                    SELECT 1 FROM youtube_research_projects p
                    WHERE p.id = youtube_research_sources.project_id AND p.user_id = ?
                  )
                """,
                (
                    status, int(result_count), error, time.time(),
                    int(source_id), int(project_id), int(user_id),
                ),
            )
            self.manager._conn.commit()
            return cur.rowcount > 0

    def upsert_video(self, project_id: int, user_id: int, video: ResearchVideo) -> int:
        if self.get_project(project_id, user_id) is None:
            raise LookupError("research project not found")
        published_at = video.published_at.timestamp() if video.published_at else None
        canonical_url = video.canonical_url or (
            f"https://www.youtube.com/watch?v={video.video_id}"
        )
        with self.manager._lock:
            self.manager._conn.execute(
                """
                INSERT INTO youtube_research_videos (
                    project_id, video_id, canonical_url, channel_id, channel_title,
                    title, description, published_at, duration_seconds, view_count,
                    like_count, comment_count, subscriber_count, thumbnail_url,
                    search_query, collected_at, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(project_id, video_id) DO UPDATE SET
                    canonical_url = excluded.canonical_url,
                    channel_id = excluded.channel_id,
                    channel_title = excluded.channel_title,
                    title = excluded.title,
                    description = excluded.description,
                    published_at = excluded.published_at,
                    duration_seconds = excluded.duration_seconds,
                    view_count = excluded.view_count,
                    like_count = excluded.like_count,
                    comment_count = excluded.comment_count,
                    subscriber_count = excluded.subscriber_count,
                    thumbnail_url = excluded.thumbnail_url,
                    search_query = excluded.search_query,
                    collected_at = excluded.collected_at,
                    metadata = excluded.metadata
                """,
                (
                    int(project_id), video.video_id, canonical_url, video.channel_id,
                    video.channel_title, video.title, video.description, published_at,
                    video.duration_seconds, video.view_count, video.like_count,
                    video.comment_count, video.subscriber_count, video.thumbnail_url,
                    video.search_query, video.collected_at.timestamp(), "{}",
                ),
            )
            row = self.manager._conn.execute(
                "SELECT id FROM youtube_research_videos "
                "WHERE project_id = ? AND video_id = ?",
                (int(project_id), video.video_id),
            ).fetchone()
            self.manager._conn.execute(
                "UPDATE youtube_research_projects SET updated_at = ? "
                "WHERE id = ? AND user_id = ?",
                (time.time(), int(project_id), int(user_id)),
            )
            self.manager._conn.commit()
            return int(row["id"])

    def save_snapshot(
        self,
        project_id: int,
        user_id: int,
        video_id: str,
        payload: dict[str, Any],
    ) -> int:
        with self.manager._lock:
            cur = self.manager._conn.execute(
                """
                INSERT INTO youtube_research_snapshots
                    (project_id, snapshot_type, payload_json, created_at, video_id)
                SELECT id, 'video_metadata', ?, ?, ?
                FROM youtube_research_projects WHERE id = ? AND user_id = ?
                """,
                (
                    self._json(payload), time.time(), video_id,
                    int(project_id), int(user_id),
                ),
            )
            if cur.rowcount != 1:
                self.manager._conn.rollback()
                raise LookupError("research project not found")
            self.manager._conn.commit()
            return int(cur.lastrowid)

    def save_analysis(
        self,
        project_id: int,
        user_id: int,
        analysis_type: str,
        payload: dict[str, Any],
        *,
        score: Optional[float] = None,
        confidence_score: Optional[float] = None,
        video_id: Optional[str] = None,
    ) -> int:
        with self.manager._lock:
            cur = self.manager._conn.execute(
                """
                INSERT INTO youtube_research_analyses
                    (project_id, analysis_type, score, confidence_score,
                     payload_json, created_at, video_id)
                SELECT id, ?, ?, ?, ?, ?, ?
                FROM youtube_research_projects WHERE id = ? AND user_id = ?
                """,
                (
                    analysis_type, score, confidence_score, self._json(payload),
                    time.time(), video_id, int(project_id), int(user_id),
                ),
            )
            if cur.rowcount != 1:
                self.manager._conn.rollback()
                raise LookupError("research project not found")
            self.manager._conn.commit()
            return int(cur.lastrowid)

    def clear_opportunities(self, project_id: int, user_id: int) -> None:
        with self.manager._lock:
            self.manager._conn.execute(
                """
                DELETE FROM youtube_research_opportunities
                WHERE project_id = ?
                  AND EXISTS (
                    SELECT 1 FROM youtube_research_projects p
                    WHERE p.id = youtube_research_opportunities.project_id
                      AND p.user_id = ?
                  )
                """,
                (int(project_id), int(user_id)),
            )
            self.manager._conn.commit()

    def save_opportunity(
        self,
        project_id: int,
        user_id: int,
        *,
        source_id: int,
        video_id: str,
        title: str,
        raw_score: float,
        adjusted_score: float,
        confidence_score: float,
        payload: dict[str, Any],
    ) -> int:
        with self.manager._lock:
            cur = self.manager._conn.execute(
                """
                INSERT INTO youtube_research_opportunities (
                    project_id, title, raw_score, adjusted_score, confidence_score,
                    payload_json, created_at, video_id, source_id
                )
                SELECT id, ?, ?, ?, ?, ?, ?, ?, ?
                FROM youtube_research_projects WHERE id = ? AND user_id = ?
                """,
                (
                    title, float(raw_score), float(adjusted_score),
                    float(confidence_score), self._json(payload), time.time(),
                    video_id, int(source_id), int(project_id), int(user_id),
                ),
            )
            if cur.rowcount != 1:
                self.manager._conn.rollback()
                raise LookupError("research project not found")
            self.manager._conn.commit()
            return int(cur.lastrowid)

    def get_video(
        self, project_id: int, user_id: int, video_id: str
    ) -> Optional[dict[str, Any]]:
        with self.manager._lock:
            row = self.manager._conn.execute(
                """
                SELECT v.* FROM youtube_research_videos v
                JOIN youtube_research_projects p ON p.id = v.project_id
                WHERE v.project_id = ? AND v.video_id = ? AND p.user_id = ?
                """,
                (int(project_id), video_id, int(user_id)),
            ).fetchone()
        return self._video_row(row) if row is not None else None

    @staticmethod
    def _iso_datetime(value: Any) -> Optional[str]:
        if value is None:
            return None
        return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat()

    @classmethod
    def _video_row(cls, row: Any) -> dict[str, Any]:
        return {
            "video_id": str(row["video_id"]),
            "canonical_url": str(
                row["canonical_url"]
                or f"https://www.youtube.com/watch?v={row['video_id']}"
            ),
            "title": str(row["title"] or ""),
            "channel_id": str(row["channel_id"] or ""),
            "channel_name": str(row["channel_title"] or ""),
            "published_at": cls._iso_datetime(row["published_at"]),
            "duration_seconds": row["duration_seconds"],
            "view_count": row["view_count"],
            "like_count": row["like_count"],
            "comment_count": row["comment_count"],
            "subscriber_count": row["subscriber_count"],
            "thumbnail_url": str(row["thumbnail_url"] or ""),
            "source_query": str(row["search_query"] or ""),
            "collected_at": cls._iso_datetime(row["collected_at"]),
        }

    def get_ranked_results(
        self, project_id: int, user_id: int, limit: int = 100
    ) -> list[dict[str, Any]]:
        with self.manager._lock:
            rows = self.manager._conn.execute(
                """
                SELECT v.*, o.raw_score, o.adjusted_score, o.confidence_score,
                       o.payload_json AS opportunity_payload
                FROM youtube_research_opportunities o
                JOIN youtube_research_videos v
                  ON v.project_id = o.project_id AND v.video_id = o.video_id
                JOIN youtube_research_projects p ON p.id = o.project_id
                WHERE o.project_id = ? AND p.user_id = ?
                ORDER BY o.adjusted_score DESC, o.raw_score DESC,
                         COALESCE(v.published_at, 0) DESC, v.video_id ASC
                LIMIT ?
                """,
                (int(project_id), int(user_id), max(1, min(int(limit), 500))),
            ).fetchall()
        output: list[dict[str, Any]] = []
        for rank, row in enumerate(rows, start=1):
            item = self._video_row(row)
            payload = self._load_json(row["opportunity_payload"])
            item.update(
                {
                    "rank": rank,
                    "opportunity_score": float(row["adjusted_score"]),
                    "raw_score": float(row["raw_score"]),
                    "confidence": float(row["confidence_score"]),
                    "explanations": list(payload.get("explanations") or []),
                    "positive_signals": list(payload.get("positive_signals") or []),
                    "negative_signals": list(payload.get("negative_signals") or []),
                    "risks": list(payload.get("risks") or []),
                }
            )
            output.append(item)
        return output
