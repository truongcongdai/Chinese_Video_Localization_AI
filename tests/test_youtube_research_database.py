from __future__ import annotations

import sqlite3
from pathlib import Path

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


def test_youtube_research_migration_upgrade_creates_only_new_tables(tmp_path: Path) -> None:
    db_file = tmp_path / "db.sqlite3"
    manager = DatabaseManager(db_path=db_file)
    manager.init_schema()

    tables = _tables(db_file)
    assert YOUTUBE_RESEARCH_TABLES.issubset(tables)
    assert {"downloads", "users", "audit_log"}.issubset(tables)
    assert manager.get_schema_version() == 5


def test_youtube_research_repository_uses_existing_manager_connection(tmp_path: Path) -> None:
    db_file = tmp_path / "db.sqlite3"
    manager = DatabaseManager(db_path=db_file)
    manager.init_schema()
    repo = YouTubeResearchRepository(manager)

    project_id = repo.create_project(
        niche="automation",
        keyword="python automation",
        target_language="en",
        target_country="US",
        metadata={"source": "unit-test"},
    )
    project = repo.get_project(project_id)

    assert project is not None
    assert project.keyword == "python automation"
    assert project.metadata["source"] == "unit-test"


def test_youtube_research_downgrade_drops_only_new_tables(tmp_path: Path) -> None:
    db_file = tmp_path / "db.sqlite3"
    manager = DatabaseManager(db_path=db_file)
    manager.init_schema()

    manager.downgrade_youtube_research()

    tables = _tables(db_file)
    assert YOUTUBE_RESEARCH_TABLES.isdisjoint(tables)
    assert {"schema_version", "downloads", "users", "audit_log"}.issubset(tables)
    assert manager.get_schema_version() == 4
