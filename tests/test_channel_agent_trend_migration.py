from __future__ import annotations

import sqlite3
from pathlib import Path

from universal_video_ai.web.store import Store


EXPECTED_COLUMNS = {
    "trend_scans": {
        "id", "scan_key", "user_id", "topic", "query_json", "platforms_json", "providers_json", "status",
        "progress_note", "warnings_json", "error", "max_results", "created_at", "updated_at",
    },
    "trend_items": {
        "id", "scan_id", "user_id", "platform", "provider", "source_url", "source_id",
        "title", "description", "author", "channel_id", "thumbnail_url", "duration_seconds", "view_count",
        "like_count", "comment_count", "share_count", "published_at", "trend_score",
        "niche_relevance_score", "opportunity_score", "relevance_status", "match_reason_json",
        "raw_json", "download_status", "local_path", "error", "rights_status",
        "first_seen_at", "last_seen_at", "observed_vph", "approx_vph", "engagement_rate",
        "outlier_ratio", "channel_typical_views", "freshness_score", "competition_proxy",
        "score_confidence", "available_signal_count", "score_explanation_json",
        "snapshot_count", "created_at", "updated_at",
    },
    "trend_queries": {
        "id", "user_id", "query", "relevance_language", "region_code",
        "published_within_days", "duration_filter", "search_order", "enabled", "notes",
        "topic_terms", "exclusion_terms",
        "created_at", "updated_at",
    },
    "trend_item_queries": {"item_id", "query_id", "first_matched_at", "last_matched_at"},
    "trend_snapshots": {"id", "item_id", "captured_at", "view_count", "like_count", "comment_count"},
}


def columns(db_path: Path, table: str) -> set[str]:
    with sqlite3.connect(str(db_path)) as conn:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def create_legacy_database(db_path: Path) -> None:
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(
            """
            CREATE TABLE trend_scans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                rule_id INTEGER,
                status TEXT NOT NULL,
                query_json TEXT NOT NULL,
                error TEXT,
                result_count INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            INSERT INTO trend_scans(user_id,status,query_json,created_at,updated_at)
            VALUES (7,'done','{"legacy":true}',100,101);

            CREATE TABLE trend_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                platform TEXT,
                source_url TEXT,
                title TEXT,
                created_at REAL,
                updated_at REAL
            );
            INSERT INTO trend_items(user_id,platform,source_url,title,created_at,updated_at)
            VALUES (7,'youtube','https://youtube.test/legacy','Legacy candidate',100,101);

            CREATE TABLE trend_queries (id INTEGER PRIMARY KEY, user_id INTEGER, query TEXT);
            INSERT INTO trend_queries(id,user_id,query) VALUES (1,7,'legacy query');
            CREATE TABLE trend_item_queries (item_id INTEGER, query_id INTEGER);
            INSERT INTO trend_item_queries(item_id,query_id) VALUES (1,1);
            CREATE TABLE trend_snapshots (id INTEGER PRIMARY KEY, item_id INTEGER, captured_at REAL);
            INSERT INTO trend_snapshots(id,item_id,captured_at) VALUES (1,1,100);
            """
        )


def test_legacy_cp2_schema_is_upgraded_without_losing_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy.sqlite3"
    create_legacy_database(db_path)

    store = Store(db_path)

    for table, expected in EXPECTED_COLUMNS.items():
        assert expected.issubset(columns(db_path, table)), table
    with sqlite3.connect(str(db_path)) as conn:
        assert conn.execute("SELECT title FROM trend_items WHERE id=1").fetchone()[0] == "Legacy candidate"
        migrated_candidate = conn.execute(
            "SELECT niche_relevance_score,opportunity_score,relevance_status FROM trend_items WHERE id=1"
        ).fetchone()
        assert migrated_candidate == (None, None, "unscored")
        legacy = conn.execute("SELECT id,scan_key,user_id,status,topic,query_json FROM trend_scans WHERE id=1").fetchone()
        assert legacy == (1, "1", 7, "done", None, '{"legacy":true}')

    store.create_trend_scan("new-scan", 7, "家族修仙", 10)
    latest = store.latest_trend_scan(7)
    assert latest["scan_id"] == "new-scan"
    assert latest["topic"] == "家族修仙"
    store.finish_trend_scan("new-scan", status="done", note="Scan complete")
    latest = store.latest_trend_scan(7)
    assert latest["status"] == "done"
    assert latest["progress_note"] == "Scan complete"
    with sqlite3.connect(str(db_path)) as conn:
        inserted = conn.execute(
            "SELECT id,scan_key,query_json FROM trend_scans WHERE scan_key='new-scan'"
        ).fetchone()
        assert inserted[0] == 2
        assert inserted[1] == "new-scan"
        assert '"topic"' in inserted[2]


def test_legacy_migration_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy-twice.sqlite3"
    create_legacy_database(db_path)

    Store(db_path)
    Store(db_path)

    with sqlite3.connect(str(db_path)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM trend_scans WHERE id=1 AND scan_key='1'").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM trend_items WHERE title='Legacy candidate'").fetchone()[0] == 1


def test_fresh_database_has_complete_cp2_schema_and_operations(tmp_path: Path) -> None:
    db_path = tmp_path / "fresh.sqlite3"
    store = Store(db_path)

    for table, expected in EXPECTED_COLUMNS.items():
        assert expected.issubset(columns(db_path, table)), table
    user_id = store.create_user("migration-user", "hash")
    query_id = store.create_trend_query(user_id, "长生家族")
    store.create_trend_scan("fresh-scan", user_id, "长生家族", 10)
    item_id = store.upsert_trend_candidate(user_id, {
        "scan_id": "fresh-scan", "source_id": "video-1",
        "source_url": "https://www.youtube.com/watch?v=video-1", "title": "Candidate",
        "captured_at": 1000.0, "view_count": 10, "like_count": 1, "comment_count": 0,
    }, "idea_only")
    store.match_trend_candidate_query(item_id, query_id, 1000.0)
    assert store.add_trend_snapshot(item_id, 1000.0, 10, 1, 0) is None

    candidate = store.get_trend_candidate(user_id, item_id)
    assert candidate is not None
    assert candidate["snapshot_count"] == 1
    assert candidate["matched_queries"] == "长生家族"
