from __future__ import annotations

from datetime import datetime, timezone
import sqlite3
from pathlib import Path

from universal_video_ai.analytics.youtube_research import ResearchVideo
from universal_video_ai.database import DatabaseManager, YouTubeResearchRepository


YOUTUBE_RESEARCH_TABLES = {
    "youtube_research_projects",
    "youtube_research_sources",
    "youtube_research_videos",
    "youtube_research_snapshots",
    "youtube_research_analyses",
    "youtube_research_opportunities",
}


def _tables(db_path: Path) -> set[str]:
    with sqlite3.connect(str(db_path)) as conn:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    return {row[0] for row in rows}


def _repo(tmp_path: Path) -> tuple[DatabaseManager, YouTubeResearchRepository]:
    manager = DatabaseManager(db_path=tmp_path / "db.sqlite3")
    manager.init_schema()
    return manager, YouTubeResearchRepository(manager)


def test_youtube_research_migration_upgrade_is_additive(tmp_path: Path) -> None:
    manager, _ = _repo(tmp_path)
    tables = _tables(tmp_path / "db.sqlite3")
    assert YOUTUBE_RESEARCH_TABLES.issubset(tables)
    assert {"downloads", "users", "audit_log"}.issubset(tables)
    assert manager.get_schema_version() == 6

    columns = {
        row["name"]
        for row in manager._conn.execute("PRAGMA table_info(youtube_research_projects)")
    }
    assert "user_id" in columns


def test_v5_database_migrates_without_rebuilding_legacy_rows(tmp_path: Path) -> None:
    manager = DatabaseManager(db_path=tmp_path / "legacy.sqlite3")
    with manager._lock:
        manager._conn.execute(
            "CREATE TABLE schema_version (id INTEGER PRIMARY KEY, version INTEGER NOT NULL)"
        )
        manager._conn.execute("INSERT INTO schema_version VALUES (1, 0)")
        manager._conn.commit()
    assert manager.migrate(target_version=5) == 5
    manager._conn.execute(
        "INSERT INTO youtube_research_projects "
        "(niche, keyword, created_at, updated_at, metadata) VALUES (?, ?, 1, 1, '{}')",
        ("legacy", "legacy query"),
    )
    manager._conn.commit()

    assert manager.migrate() == 6
    row = manager._conn.execute(
        "SELECT niche, user_id FROM youtube_research_projects"
    ).fetchone()
    assert row["niche"] == "legacy"
    assert row["user_id"] is None


def test_project_crud_and_tenant_ownership(tmp_path: Path) -> None:
    _, repo = _repo(tmp_path)
    project_id = repo.create_project(
        user_id=101,
        niche="automation",
        keyword="python automation",
        target_language="en",
        target_country="US",
        metadata={"source": "unit-test"},
    )

    project = repo.get_project(project_id, 101)
    assert project is not None
    assert project.user_id == 101
    assert project.metadata["source"] == "unit-test"
    assert repo.get_project(project_id, 202) is None
    assert repo.list_projects(202) == []
    assert [item.id for item in repo.list_projects(101)] == [project_id]

    assert repo.update_project(
        project_id, 202, niche="bad", keyword="bad",
        target_language=None, target_country=None,
    ) is False
    assert repo.update_project(
        project_id, 101, niche="new niche", keyword="new keyword",
        target_language="vi", target_country="VN",
    ) is True
    assert repo.get_project(project_id, 101).keyword == "new keyword"


def test_video_upsert_snapshots_analyses_and_ranked_results(tmp_path: Path) -> None:
    manager, repo = _repo(tmp_path)
    project_id = repo.create_project(7, "tech", "python")
    source_id = repo.create_search_execution(
        project_id, 7, query="tech python", max_results=5
    )
    observed = datetime(2026, 9, 3, tzinfo=timezone.utc)
    first = ResearchVideo(
        video_id="abc123def45",
        title="First",
        canonical_url="https://www.youtube.com/watch?v=abc123def45",
        view_count=10,
        like_count=None,
        collected_at=observed,
    )
    repo.upsert_video(project_id, 7, first)
    repo.upsert_video(
        project_id, 7,
        ResearchVideo(
            video_id=first.video_id,
            title="First updated",
            canonical_url=first.canonical_url,
            view_count=20,
            collected_at=observed,
        ),
    )
    repo.save_snapshot(project_id, 7, first.video_id, {"view_count": 20})
    repo.save_analysis(
        project_id, 7, "trend", {"trend_score": 42},
        score=42, confidence_score=25,
    )
    repo.save_opportunity(
        project_id, 7, source_id=source_id, video_id=first.video_id,
        title="First updated", raw_score=70, adjusted_score=35,
        confidence_score=50,
        payload={"explanations": ["Deterministic score."]},
    )
    repo.finish_search_execution(
        source_id, project_id, 7, status="completed", result_count=1
    )

    assert repo.get_video(project_id, 7, first.video_id)["view_count"] == 20
    assert repo.get_video(project_id, 8, first.video_id) is None
    ranked = repo.get_ranked_results(project_id, 7)
    assert ranked[0]["rank"] == 1
    assert ranked[0]["title"] == "First updated"
    assert ranked[0]["opportunity_score"] == 35
    assert ranked[0]["explanations"] == ["Deterministic score."]
    assert repo.get_ranked_results(project_id, 8) == []

    video_count = manager._conn.execute(
        "SELECT COUNT(*) AS count FROM youtube_research_videos"
    ).fetchone()["count"]
    snapshot_count = manager._conn.execute(
        "SELECT COUNT(*) AS count FROM youtube_research_snapshots"
    ).fetchone()["count"]
    analysis_count = manager._conn.execute(
        "SELECT COUNT(*) AS count FROM youtube_research_analyses"
    ).fetchone()["count"]
    assert video_count == 1
    assert snapshot_count == 1
    assert analysis_count == 1


def test_youtube_research_downgrade_drops_only_research_tables(tmp_path: Path) -> None:
    manager, _ = _repo(tmp_path)
    manager.downgrade_youtube_research()
    tables = _tables(tmp_path / "db.sqlite3")
    assert YOUTUBE_RESEARCH_TABLES.isdisjoint(tables)
    assert {"schema_version", "downloads", "users", "audit_log"}.issubset(tables)
    assert manager.get_schema_version() == 4
