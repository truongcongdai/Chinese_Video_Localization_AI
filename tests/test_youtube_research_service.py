from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from universal_video_ai.analytics.youtube_research import (
    ResearchVideo,
    YouTubeCollectorError,
    YouTubeResearchService,
)
from universal_video_ai.database import DatabaseManager, YouTubeResearchRepository


NOW = datetime(2026, 9, 3, tzinfo=timezone.utc)


class FakeYouTubeCollector:
    def __init__(self, videos=None, error=None):
        self.videos = list(videos or [])
        self.error = error
        self.calls = []

    async def search(self, query: str, max_results: int):
        self.calls.append((query, max_results))
        if self.error:
            raise self.error
        return self.videos


def _service(tmp_path, collector):
    manager = DatabaseManager(tmp_path / "research.sqlite3")
    manager.init_schema()
    repo = YouTubeResearchRepository(manager)
    service = YouTubeResearchService(
        repo, collector, hard_max_results=5, clock=lambda: NOW
    )
    return manager, repo, service


def test_service_collects_persists_analyzes_ranks_and_rescans(tmp_path) -> None:
    collector = FakeYouTubeCollector(
        [
            ResearchVideo(
                video_id="low12345678", title="Lower signal",
                published_at=NOW - timedelta(days=20), view_count=100,
                like_count=1, comment_count=0, subscriber_count=100_000,
            ),
            ResearchVideo(
                video_id="high1234567", title="Higher signal",
                published_at=NOW - timedelta(hours=4), view_count=100_000,
                like_count=8_000, comment_count=500, subscriber_count=20_000,
            ),
            ResearchVideo(video_id="high1234567", title="Duplicate"),
        ]
    )
    manager, repo, service = _service(tmp_path, collector)
    project = service.create_project(
        11, niche="creator tools", keyword="python automation",
        target_language="vi", target_country="VN",
    )

    payload = asyncio.run(service.scan(project.id, 11, max_results=3))
    assert collector.calls == [("creator tools python automation", 3)]
    assert payload["result_count"] == 2
    assert [item["rank"] for item in payload["results"]] == [1, 2]
    assert payload["results"][0]["opportunity_score"] >= payload["results"][1]["opportunity_score"]
    assert payload["results"][0]["canonical_url"].startswith(
        "https://www.youtube.com/watch?v="
    )
    assert payload["analysis"]["trend"]["video_count"] == 2
    assert service.ranked_results(project.id, 11) == payload["results"]

    asyncio.run(service.scan(project.id, 11, max_results=3))
    video_count = manager._conn.execute(
        "SELECT COUNT(*) AS count FROM youtube_research_videos WHERE project_id = ?",
        (project.id,),
    ).fetchone()["count"]
    opportunity_count = manager._conn.execute(
        "SELECT COUNT(*) AS count FROM youtube_research_opportunities WHERE project_id = ?",
        (project.id,),
    ).fetchone()["count"]
    snapshot_count = manager._conn.execute(
        "SELECT COUNT(*) AS count FROM youtube_research_snapshots WHERE project_id = ?",
        (project.id,),
    ).fetchone()["count"]
    assert video_count == 2
    assert opportunity_count == 2
    assert snapshot_count == 4
    assert repo.get_project(project.id, 99) is None


def test_service_preserves_missing_metrics_as_unavailable(tmp_path) -> None:
    collector = FakeYouTubeCollector(
        [ResearchVideo(video_id="partial12345", title="Partial")]
    )
    _, _, service = _service(tmp_path, collector)
    project = service.create_project(1, niche="", keyword="partial")
    payload = asyncio.run(service.scan(project.id, 1, max_results=1))
    result = payload["results"][0]
    assert result["view_count"] is None
    assert result["like_count"] is None
    assert result["comment_count"] is None
    assert result["subscriber_count"] is None
    assert result["published_at"] is None
    assert result["confidence"] == 0


def test_service_records_truthful_collector_failure_without_results(tmp_path) -> None:
    collector = FakeYouTubeCollector(error=YouTubeCollectorError("collector offline"))
    manager, _, service = _service(tmp_path, collector)
    project = service.create_project(1, niche="test", keyword="failure")

    with pytest.raises(YouTubeCollectorError, match="collector offline"):
        asyncio.run(service.scan(project.id, 1, max_results=2))

    source = manager._conn.execute(
        "SELECT status, result_count, error FROM youtube_research_sources"
    ).fetchone()
    assert dict(source) == {
        "status": "failed", "result_count": 0, "error": "collector offline"
    }
    assert service.ranked_results(project.id, 1) == []


def test_service_rejects_cross_tenant_scan_before_collector_call(tmp_path) -> None:
    collector = FakeYouTubeCollector([])
    _, _, service = _service(tmp_path, collector)
    project = service.create_project(1, niche="tenant", keyword="safe")
    with pytest.raises(LookupError):
        asyncio.run(service.scan(project.id, 2, max_results=2))
    assert collector.calls == []
