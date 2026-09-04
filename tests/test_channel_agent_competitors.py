from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from universal_video_ai.channel_agent.competitors import (
    BREAKOUT_ABOVE,
    CompetitorIntelligenceService,
    CompetitorMetadata,
    CompetitorVideo,
    YouTubeCompetitorProvider,
    analyze_competitor,
    breakout_strength,
    comparable_videos,
    duration_bucket,
    extract_patterns,
    opportunity_gaps,
)
from universal_video_ai.web.channel_agent_router import competitors
from universal_video_ai.web.store import Store


NOW = datetime(2026, 8, 20, 12, tzinfo=timezone.utc)


class FakeYouTube:
    def __init__(self, payloads: dict[str, list[dict[str, Any]]]) -> None:
        self.payloads = {key: list(value) for key, value in payloads.items()}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def data_request(self, user_id: int, resource: str, params: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((resource, params))
        return self.payloads[resource].pop(0)


def video(video_id: str, views: int | None, *, minutes: int = 30, title: str = "家族修仙",
          description: str = "", age_days: int = 1) -> CompetitorVideo:
    return CompetitorVideo(
        video_id=video_id, title=title, description=description, video_url=f"https://youtube.test/{video_id}",
        thumbnail_url=None, published_at=NOW - timedelta(days=age_days), duration_seconds=minutes * 60,
        view_count=views, like_count=None if views is None else max(0, views // 20), comment_count=1,
    )


def metadata(channel_id: str = "UC-1", *, hidden: bool = False) -> CompetitorMetadata:
    return CompetitorMetadata(
        channel_id=channel_id, channel_title=f"Channel {channel_id}",
        channel_url=f"https://www.youtube.com/channel/{channel_id}", custom_url=f"@{channel_id}",
        thumbnail_url="https://thumb", subscriber_count=None if hidden else 100,
        hidden_subscriber_count=hidden, lifetime_view_count=1000, video_count=20,
        uploads_playlist_id=f"UU-{channel_id}",
    )


def test_channel_metadata_populated_hidden_and_missing_optional_fields() -> None:
    youtube = FakeYouTube({"channels": [{"items": [
        {"id": "UC-a", "snippet": {"title": "A", "customUrl": "@a", "thumbnails": {"high": {"url": "x"}}},
         "statistics": {"subscriberCount": "12", "viewCount": "30", "videoCount": "4"},
         "contentDetails": {"relatedPlaylists": {"uploads": "UU-a"}}},
        {"id": "UC-b", "snippet": {"title": "B"},
         "statistics": {"hiddenSubscriberCount": True}, "contentDetails": {}},
    ]}]})
    result = YouTubeCompetitorProvider(youtube).fetch_channels(7, ["UC-a", "UC-b"])
    assert result["UC-a"].subscriber_count == 12
    assert result["UC-a"].uploads_playlist_id == "UU-a"
    assert result["UC-b"].hidden_subscriber_count is True
    assert result["UC-b"].subscriber_count is None
    assert result["UC-b"].thumbnail_url is None
    assert len(youtube.calls) == 1


def test_manual_channel_resolution_uses_handle_without_search() -> None:
    youtube = FakeYouTube({"channels": [{"items": [{"id": "UC-a", "snippet": {"title": "A"},
                                                       "statistics": {}, "contentDetails": {}}]}]})
    result = YouTubeCompetitorProvider(youtube).resolve_channel(1, "https://youtube.com/@channel-a")
    assert result.channel_id == "UC-a"
    assert [call[0] for call in youtube.calls] == ["channels"]
    assert youtube.calls[0][1]["forHandle"] == "channel-a"


def test_recent_uploads_use_playlist_calls_and_batched_video_metadata() -> None:
    youtube = FakeYouTube({
        "playlistItems": [
            {"items": [{"contentDetails": {"videoId": "a"}}, {"contentDetails": {"videoId": "b"}}]},
            {"items": [{"contentDetails": {"videoId": "c"}}]},
        ],
        "videos": [{"items": [
            {"id": key, "snippet": {"title": key, "publishedAt": "2026-08-20T00:00:00Z"},
             "contentDetails": {"duration": "PT30M"}, "statistics": {"viewCount": "10"}}
            for key in ("a", "b", "c")
        ]}],
    })
    rows = [{"channel_id": "UC-a", "uploads_playlist_id": "UU-a"},
            {"channel_id": "UC-b", "uploads_playlist_id": "UU-b"}]
    result = YouTubeCompetitorProvider(youtube).recent_videos(1, rows, sample_size=20)
    assert [item.video_id for item in result["UC-a"]] == ["a", "b"]
    assert [name for name, _ in youtube.calls] == ["playlistItems", "playlistItems", "videos"]
    assert youtube.calls[-1][1]["id"] == "a,b,c"


def test_empty_channel_and_long_short_separation() -> None:
    assert comparable_videos([], "long") == []
    items = [video("short", 100, minutes=1), video("medium", 100, minutes=10), video("long", 100, minutes=30)]
    assert [item.video_id for item in comparable_videos(items, "short")] == ["short"]
    assert [item.video_id for item in comparable_videos(items, "long")] == ["long"]
    assert len(comparable_videos(items, "all")) == 3


def test_baseline_median_zero_missing_and_confidence() -> None:
    analysis, rows = analyze_competitor([video("a", 100), video("b", 300), video("c", None)], now=NOW)
    assert analysis["median_views"] == 200
    assert analysis["recent_upload_count"] == 3
    assert analysis["score_confidence"] == "low"
    assert rows[0]["outlier_ratio"] == pytest.approx(0.5)
    zero, zero_rows = analyze_competitor([video("z", 0)], now=NOW)
    assert zero["median_views"] == 0
    assert zero_rows[0]["outlier_ratio"] is None


def test_breakout_thresholds_and_high_outlier() -> None:
    assert breakout_strength(None) == "unavailable"
    assert breakout_strength(1.5) == "normal"
    assert breakout_strength(BREAKOUT_ABOVE) == "above_baseline"
    assert breakout_strength(5) == "strong"
    assert breakout_strength(10) == "exceptional"
    analysis, rows = analyze_competitor([video("a", 100), video("b", 100), video("hit", 1000)], now=NOW)
    assert next(row for row in rows if row["video_id"] == "hit")["outlier_ratio"] == 10
    assert analysis["breakout_count"] == 1


def test_small_breakout_channel_can_beat_large_weak_channel_and_scores_are_bounded() -> None:
    small, _ = analyze_competitor(
        [video("s1", 100), video("s2", 100), video("s3", 1000), video("s4", 1200), video("s5", 100)],
        candidate_summary={"median_opportunity_score": .8, "median_relevance_score": .9}, now=NOW,
    )
    large, _ = analyze_competitor(
        [video(f"l{i}", 1_000_000 + i * 1000) for i in range(5)],
        candidate_summary={"median_opportunity_score": .2, "median_relevance_score": .6}, now=NOW,
    )
    assert 0 <= small["competitor_score"] <= 1
    assert small["competitor_score"] > large["competitor_score"]


def test_many_strong_niche_videos_produce_high_competitor_relevance() -> None:
    items = [video(f"n{i}", 100 + i, title="家族修仙老祖崛起") for i in range(14)]
    items.extend(video(f"u{i}", 50, title="普通故事") for i in range(6))
    analysis, _ = analyze_competitor(
        items, topic_terms="家族修仙,老祖", channel_title="修仙故事馆",
        candidate_summary={"median_relevance_score": .9}, now=NOW,
    )
    assert analysis["niche_hit_rate"] == pytest.approx(.7)
    assert analysis["competitor_relevance_score"] >= .70
    assert analysis["competitor_relevance_status"] == "qualified"
    assert any("14/20" in reason for reason in analysis["competitor_match_reasons"])


def test_one_generic_family_match_does_not_qualify_drama_channel() -> None:
    items = [video("generic", 100, title="前世豪门家族恩怨")]
    items.extend(video(f"d{i}", 100, title="都市爱情故事") for i in range(19))
    analysis, _ = analyze_competitor(
        items, topic_terms="家族修仙,老祖", channel_title="Beyond Realm Drama",
        candidate_summary={"median_relevance_score": .9}, now=NOW,
    )
    assert analysis["niche_hit_rate"] == 0
    assert analysis["competitor_relevance_score"] < .35
    assert analysis["competitor_relevance_status"] == "low_relevance"


def test_short_drama_exclusion_downranks_even_apparent_topic_matches() -> None:
    items = [video(f"s{i}", 100, title="家族修仙短剧 完整版") for i in range(10)]
    analysis, _ = analyze_competitor(
        items, topic_terms="家族修仙", channel_title="無界短劇社 | Beyond Realm Drama",
        candidate_summary={"median_relevance_score": .9}, now=NOW,
    )
    assert analysis["competitor_relevance_status"] == "low_relevance"
    assert analysis["competitor_relevance_score"] < .35
    assert any("excluded channel term" in reason for reason in analysis["competitor_match_reasons"])


def test_competitor_profile_is_configurable_beyond_cultivation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHANNEL_AGENT_COMPETITOR_STRONG_TERMS", "robotics lab")
    monkeypatch.setenv("CHANNEL_AGENT_COMPETITOR_GENERIC_TERMS", "review,complete")
    monkeypatch.setenv("CHANNEL_AGENT_COMPETITOR_EXCLUSION_TERMS", "soap opera")
    analysis, _ = analyze_competitor(
        [video(f"r{i}", 100, title="Robotics Lab field review") for i in range(5)],
        channel_title="Robotics Lab", now=NOW,
    )
    assert analysis["competitor_relevance_status"] == "qualified"
    assert analysis["niche_hit_rate"] == 1


def test_patterns_unicode_profile_and_no_fabrication() -> None:
    rows = [
        {"video_id": "1", "title": "家族老祖崛起", "video_url": "u1", "outlier_ratio": 5.0},
        {"video_id": "2", "title": "家族老祖长生", "video_url": "u2", "outlier_ratio": 3.0},
        {"video_id": "3", "title": "Unrelated title", "video_url": "u3", "outlier_ratio": 1.0},
    ]
    patterns = extract_patterns(rows, "家族,老祖")
    names = {item["pattern"] for item in patterns}
    assert "家族" in names and "老祖" in names
    assert all(item["evidence"] for item in patterns)
    assert extract_patterns(rows[2:], "家族,老祖") == []


def test_duration_bucket_analysis_and_empty_buckets() -> None:
    assert duration_bucket(19 * 60) == "under 20 min"
    assert duration_bucket(30 * 60) == "20–40 min"
    assert duration_bucket(130 * 60) == "120+ min"
    analysis, _ = analyze_competitor([video("a", 100, minutes=30), video("b", 300, minutes=35)], now=NOW)
    assert analysis["duration_buckets"] == [{
        "bucket": "20–40 min", "video_count": 2, "median_views": 200.0,
        "median_outlier": 1.0, "breakout_count": 0,
    }]


class FakeProvider:
    def fetch_channels(self, user_id: int, channel_ids: list[str]) -> dict[str, CompetitorMetadata]:
        return {channel_id: metadata(channel_id) for channel_id in channel_ids}

    def resolve_channel(self, user_id: int, reference: str) -> CompetitorMetadata:
        return metadata("UC-manual")

    def recent_videos(self, user_id: int, competitors: list[dict[str, Any]], *, sample_size: int) -> dict[str, list[CompetitorVideo]]:
        return {row["channel_id"]: [video("a", 100), video("b", 1000)] for row in competitors}


def make_store(tmp_path: Path) -> tuple[Store, int, int]:
    store = Store(tmp_path / "web.sqlite3")
    return store, store.create_user("a", "x"), store.create_user("b", "x")


def add_candidate(store: Store, user_id: int, source_id: str, channel_id: str,
                  relevance: float, status: str = "relevant") -> int:
    item_id = store.upsert_trend_candidate(user_id, {
        "scan_id": "scan", "source_id": source_id, "source_url": f"https://youtube.test/{source_id}",
        "title": "家族修仙", "channel_id": channel_id, "channel_title": channel_id,
        "captured_at": 1.0,
    }, "idea_only")
    store.update_trend_candidate_score(
        user_id, item_id, trend_score=.8, niche_relevance_score=relevance,
        opportunity_score=.8 * relevance, relevance_status=status,
    )
    return item_id


def test_discovery_uses_only_qualified_candidates_and_deduplicates(tmp_path: Path) -> None:
    store, user_a, _ = make_store(tmp_path)
    add_candidate(store, user_a, "one", "UC-good", .9)
    add_candidate(store, user_a, "two", "UC-good", .8)
    add_candidate(store, user_a, "bad", "UC-bad", .1, "low_relevance")
    result = CompetitorIntelligenceService(store, FakeProvider()).discover(user_a)
    assert result == {"qualified_channels": 1, "competitors_discovered": 1}
    channels = store.list_competitors(user_a)
    assert len(channels) == 1
    assert channels[0]["channel_id"] == "UC-good"
    assert channels[0]["source_candidate_count"] == 2


def test_persistence_dedup_snapshots_and_user_isolation(tmp_path: Path) -> None:
    store, user_a, user_b = make_store(tmp_path)
    first = store.upsert_competitor(user_a, metadata().to_dict())
    second = store.upsert_competitor(user_a, metadata().to_dict())
    other_user = store.upsert_competitor(user_b, metadata().to_dict())
    assert first == second
    assert other_user != first
    store.add_competitor_snapshot(user_a, first)
    store.add_competitor_snapshot(user_a, first)
    assert len(store.list_competitor_snapshots(user_a, first)) == 2
    assert store.get_competitor(user_b, first) is None
    assert store.list_competitor_videos(user_b, first) == []
    assert store.get_competitor(user_b, other_user)["channel_id"] == "UC-1"


def test_low_relevance_competitor_remains_stored_and_audit_api_can_return_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("AI_CHANNEL_AGENT_ENABLED", "true")
    store, user_a, _ = make_store(tmp_path)
    competitor_id = store.upsert_competitor(user_a, metadata().to_dict())
    analysis, _ = analyze_competitor(
        [video("d", 100, title="前世都市爱情")], topic_terms="家族修仙,老祖", now=NOW,
    )
    store.update_competitor_analysis(user_a, competitor_id, analysis)
    assert store.get_competitor(user_a, competitor_id)["competitor_relevance_status"] == "low_relevance"
    assert competitors(include_filtered=False, user_id=user_a, store=store) == []
    assert [item["id"] for item in competitors(include_filtered=True, user_id=user_a, store=store)] == [competitor_id]


def test_refresh_persists_analysis_videos_and_append_only_snapshots(tmp_path: Path) -> None:
    store, user_a, _ = make_store(tmp_path)
    competitor_id = store.upsert_competitor(user_a, metadata().to_dict())
    service = CompetitorIntelligenceService(store, FakeProvider())

    first = service.refresh(user_a, mode="long")
    second = service.refresh(user_a, competitor_id=competitor_id, mode="long")

    competitor = store.get_competitor(user_a, competitor_id)
    assert first["competitors_refreshed"] == second["competitors_refreshed"] == 1
    assert competitor["recent_upload_count"] == 2
    assert competitor["median_views"] == 550.0
    assert len(store.list_competitor_videos(user_a, competitor_id)) == 2
    assert len(store.list_competitor_snapshots(user_a, competitor_id)) == 2


def _pattern(patterns: list[dict[str, Any]], *terms: str) -> dict[str, Any]:
    expected = set(terms)
    return next(item for item in patterns if set(part.strip() for part in item["pattern"].split("+")) == expected)


def test_generic_pattern_is_retained_for_audit_but_not_a_default_gap() -> None:
    rows = [
        {"video_id": "a", "title": "前世归来", "video_url": "ua", "outlier_ratio": 5.0},
        {"video_id": "b", "title": "前世秘密", "video_url": "ub", "outlier_ratio": 3.0},
    ]
    generic = _pattern(extract_patterns(rows, "家族修仙,老祖"), "前世")
    assert generic["pattern_quality_status"] == "filtered"
    assert generic["pattern_quality_score"] < .55
    channels = [{"id": 1, "competitor_relevance_status": "qualified", "patterns": [generic]}]
    assert opportunity_gaps(channels, []) == []
    audit = opportunity_gaps(channels, [], include_filtered=True)
    assert audit[0]["gap_quality_status"] == "filtered"


def test_compound_pattern_becomes_quality_gap_with_distinct_evidence() -> None:
    first_rows = [
        {"video_id": "a1", "title": "家族老祖崛起", "video_url": "ua1", "outlier_ratio": 6.0},
        {"video_id": "a2", "title": "家族老祖长生", "video_url": "ua2", "outlier_ratio": 4.0},
    ]
    second_rows = [
        {"video_id": "b1", "title": "家族老祖归来", "video_url": "ub1", "outlier_ratio": 7.0},
        {"video_id": "b2", "title": "家族老祖修仙", "video_url": "ub2", "outlier_ratio": 3.0},
    ]
    first = _pattern(extract_patterns(first_rows, "家族修仙,老祖"), "家族", "老祖")
    second = _pattern(extract_patterns(second_rows, "家族修仙,老祖"), "家族", "老祖")
    assert first["pattern_quality_status"] == "qualified"
    channels = [
        {"id": 1, "competitor_relevance_status": "qualified", "patterns": [first]},
        {"id": 2, "competitor_relevance_status": "qualified", "patterns": [second]},
    ]
    gaps = opportunity_gaps(channels, [{"title": "other"}])
    assert gaps[0]["gap_quality_status"] == "qualified"
    assert gaps[0]["supporting_competitor_count"] == 2
    assert gaps[0]["supporting_breakout_count"] == 4
    assert gaps[0]["qualified_candidate_count"] == 0


def test_duplicate_video_evidence_does_not_inflate_gap_support_and_low_competitors_are_excluded() -> None:
    evidence = [
        {"video_id": "same", "title": "家族老祖", "video_url": "u", "outlier_ratio": 6.0},
        {"video_id": "other", "title": "家族老祖", "video_url": "u2", "outlier_ratio": 5.0},
    ]
    pattern = {
        "pattern": "家族 + 老祖", "breakout_count": 2, "pattern_quality_status": "qualified",
        "pattern_quality_score": .9, "evidence": evidence,
    }
    duplicate = {**pattern, "evidence": [evidence[0]]}
    channels = [
        {"id": 1, "competitor_relevance_status": "qualified", "patterns": [pattern]},
        {"id": 2, "competitor_relevance_status": "qualified", "patterns": [duplicate]},
        {"id": 3, "competitor_relevance_status": "low_relevance", "patterns": [{**pattern, "evidence": [
            {"video_id": "low", "title": "家族老祖", "video_url": "ul", "outlier_ratio": 9.0},
        ]}]},
    ]
    default = opportunity_gaps(channels, [])
    assert default[0]["supporting_competitor_count"] == 2
    assert default[0]["supporting_breakout_count"] == 2
    audit = opportunity_gaps(channels, [], include_filtered=True)
    assert audit[0]["supporting_competitor_count"] == 3


def test_feature_flag_blocks_competitor_routes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AI_CHANNEL_AGENT_ENABLED", "false")
    store, user_a, _ = make_store(tmp_path)
    with pytest.raises(Exception) as exc_info:
        competitors(user_id=user_a, store=store)
    assert getattr(exc_info.value, "status_code", None) == 404


def test_legacy_partial_competitor_schema_migrates_idempotently(tmp_path: Path) -> None:
    db = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(db) as conn:
        conn.executescript("""
        CREATE TABLE competitor_channels (id INTEGER PRIMARY KEY, user_id INTEGER, channel_id TEXT);
        INSERT INTO competitor_channels VALUES (1,7,'UC-old');
        CREATE TABLE competitor_snapshots (id INTEGER PRIMARY KEY, competitor_id INTEGER);
        CREATE TABLE competitor_videos (id INTEGER PRIMARY KEY, competitor_id INTEGER, video_id TEXT);
        """)
    Store(db)
    Store(db)
    with sqlite3.connect(db) as conn:
        channel_columns = {row[1] for row in conn.execute("PRAGMA table_info(competitor_channels)")}
        video_columns = {row[1] for row in conn.execute("PRAGMA table_info(competitor_videos)")}
        assert {
            "competitor_score", "patterns_json", "uploads_playlist_id", "competitor_relevance_score",
            "competitor_relevance_status", "competitor_match_reasons_json", "niche_hit_rate",
            "niche_matching_video_count", "niche_analyzed_video_count",
        }.issubset(channel_columns)
        assert {"outlier_ratio", "rights_status", "video_url"}.issubset(video_columns)
        assert conn.execute("SELECT channel_id FROM competitor_channels WHERE id=1").fetchone()[0] == "UC-old"
        assert conn.execute(
            "SELECT competitor_relevance_status FROM competitor_channels WHERE id=1"
        ).fetchone()[0] == "unscored"
