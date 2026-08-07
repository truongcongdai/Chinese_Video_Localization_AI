from __future__ import annotations

from dataclasses import dataclass
import json
import time
from typing import Any, Optional

from .manager import DatabaseManager


@dataclass(frozen=True)
class YouTubeResearchProject:
    id: Optional[int]
    niche: str
    keyword: str
    target_language: Optional[str]
    target_country: Optional[str]
    created_at: float
    updated_at: float
    metadata: dict[str, Any]


class YouTubeResearchRepository:
    """Small repository that reuses DatabaseManager's SQLite connection."""

    def __init__(self, manager: DatabaseManager) -> None:
        self.manager = manager

    def create_project(
        self,
        niche: str,
        keyword: str,
        target_language: Optional[str] = None,
        target_country: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> int:
        now = time.time()
        with self.manager._lock:
            cur = self.manager._conn.cursor()
            cur.execute(
                """
                INSERT INTO youtube_research_projects
                    (niche, keyword, target_language, target_country, created_at, updated_at, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    niche,
                    keyword,
                    target_language,
                    target_country,
                    now,
                    now,
                    json.dumps(metadata or {}, ensure_ascii=False),
                ),
            )
            self.manager._conn.commit()
            return int(cur.lastrowid)

    def get_project(self, project_id: int) -> Optional[YouTubeResearchProject]:
        with self.manager._lock:
            cur = self.manager._conn.cursor()
            cur.execute("SELECT * FROM youtube_research_projects WHERE id = ?", (project_id,))
            row = cur.fetchone()
        if row is None:
            return None
        metadata_text = row["metadata"] or "{}"
        try:
            metadata = json.loads(metadata_text)
        except Exception:
            metadata = {}
        return YouTubeResearchProject(
            id=int(row["id"]),
            niche=row["niche"],
            keyword=row["keyword"],
            target_language=row["target_language"],
            target_country=row["target_country"],
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
            metadata=metadata,
        )
