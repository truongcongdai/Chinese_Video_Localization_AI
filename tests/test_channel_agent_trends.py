from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from universal_video_ai.analytics.youtube_research import ResearchVideo
from universal_video_ai.channel_agent.trends import (
    SearchResult,
    TrendScanAlreadyRunning,
    YouTubeTrendScanner,
    YouTubeTrendSearchProvider,
    approximate_vph,
    competition_opportunity_proxy,
    freshness_score,
    normalize_relevance_text,
    opportunity_score,
    parse_youtube_duration,
    score_candidate,
    score_niche_relevance,
)
from universal_video_ai.web.channel_agent_router import trend_candidates
from universal_video_ai.web.store import Store


NOW = datetime(2026, 8, 20, 12, tzinfo=timezone.utc)


class FakeYouTube:
    def __init__(self, payloads: dict[str, list[dict[str, Any]]]) -> None:
        self.payloads = {key: list(value) for key, value in payloads.items()}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def data_request(self, user_id: int, resource: str, params: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((resource, params))
        value = self.payloads[resource].pop(0)
        if isinstance(value, Exception):
            raise value
        return value


def query(**overrides: Any) -> dict[str, Any]:
    value = {"id": 1, "query": "家族修仙", "published_within_days": 30,
             "duration_filter": "long", "search_order": "date",
             "relevance_language": "zh", "region_code": None}
    value.update(overrides)
    return value


def provider_payloads() -> dict[str, list[dict[str, Any]]]:
    return {
        "search": [{"items": [{"id": {"videoId": "v1"}}, {"id": {"videoId": "v2"}}]}],
        "videos": [{"items": [
            {"id": "v1", "snippet": {"title": "First", "description": "Metadata description", "channelId": "c1", "channelTitle": "A",
             "publishedAt": "2026-08-20T01:00:00Z", "thumbnails": {"default": {"url": "thumb1"}}},
             "contentDetails": {"duration": "PT25M"},
             "statistics": {"viewCount": "1000", "likeCount": "50", "commentCount": "5"}},
            {"id": "v2", "snippet": {"title": "Second", "channelId": "c2", "channelTitle": "B"},
             "contentDetails": {"duration": "invalid"}, "statistics": {"viewCount": "0"}},
        ]}],
        "channels": [{"items": [
            {"id": "c1", "statistics": {"subscriberCount": "100"},
             "contentDetails": {"relatedPlaylists": {"uploads": "u1"}}},
            {"id": "c2", "statistics": {}, "contentDetails": {}},
        ]}],
    }


def test_search_uses_batched_video_and_channel_enrichment() -> None:
    youtube = FakeYouTube(provider_payloads())
    result = YouTubeTrendSearchProvider(youtube).search(7, query(), max_results=10)

    assert [video.video_id for video in result.videos] == ["v1", "v2"]
    assert result.videos[0].duration_seconds == 1500
    assert result.videos[0].description == "Metadata description"
    assert result.videos[1].duration_seconds is None
    assert result.uploads_playlists == {"c1": "u1"}
    assert [resource for resource, _ in youtube.calls] == ["search", "videos", "channels"]
    assert youtube.calls[1][1]["id"] == "v1,v2"
    assert youtube.calls[2][1]["id"] == "c1,c2"


def test_search_zero_results_and_provider_failure() -> None:
    youtube = FakeYouTube({"search": [{"items": []}]})
    assert YouTubeTrendSearchProvider(youtube).search(1, query(), max_results=10).videos == ()

    youtube = FakeYouTube({"search": [RuntimeError("provider failed")]})
    with pytest.raises(RuntimeError, match="provider failed"):
        YouTubeTrendSearchProvider(youtube).search(1, query(), max_results=10)


def test_duration_parser_handles_optional_malformed_metadata() -> None:
    assert parse_youtube_duration("PT1H2M3S") == 3723
    assert parse_youtube_duration("invalid") is None
    assert parse_youtube_duration(None) is None


def test_channel_baseline_uses_recent_long_form_median_and_batches_videos() -> None:
    youtube = FakeYouTube({
        "playlistItems": [
            {"items": [{"contentDetails": {"videoId": "a"}}, {"contentDetails": {"videoId": "b"}}]},
            {"items": [{"contentDetails": {"videoId": "c"}}]},
        ],
        "videos": [{"items": [
            {"id": "a", "snippet": {"channelId": "c1"}, "contentDetails": {"duration": "PT30M"}, "statistics": {"viewCount": "100"}},
            {"id": "b", "snippet": {"channelId": "c1"}, "contentDetails": {"duration": "PT40M"}, "statistics": {"viewCount": "300"}},
            {"id": "c", "snippet": {"channelId": "c2"}, "contentDetails": {"duration": "PT30S"}, "statistics": {"viewCount": "9999"}},
        ]}],
    })
    baselines = YouTubeTrendSearchProvider(youtube).channel_baselines(
        1, {"c1": "u1", "c2": "u2"}, ["c1", "c2"], long_form=True
    )

    assert baselines == {"c1": 200.0}
    assert [name for name, _ in youtube.calls].count("videos") == 1


def test_freshness_approximation_and_competition_are_bounded() -> None:
    assert freshness_score(NOW - timedelta(hours=2), NOW) > freshness_score(NOW - timedelta(days=30), NOW)
    assert freshness_score(NOW + timedelta(days=1), NOW) == 1.0
    assert freshness_score(None, NOW) is None
    assert approximate_vph(1000, NOW - timedelta(hours=2), NOW) == 500.0
    assert approximate_vph(1000, NOW, NOW) == 1000.0
    assert competition_opportunity_proxy(0, 0) == 1.0
    assert competition_opportunity_proxy(100, 50) < competition_opportunity_proxy(5, 2)


def test_score_renormalizes_missing_outlier_and_reports_confidence() -> None:
    complete = score_candidate(observed_vph=1000, approx_vph_value=None, engagement=0.05,
                               outlier=4.0, freshness=0.9, competition_proxy=0.5)
    partial = score_candidate(observed_vph=None, approx_vph_value=500, engagement=0.05,
                              outlier=None, freshness=0.9, competition_proxy=0.5)

    assert 0 <= complete.score <= 1
    assert complete.confidence == "high"
    assert partial.available_signal_count == 4
    assert partial.confidence == "medium"
    assert 0 <= partial.score <= 1


def test_relevance_exact_chinese_query_match() -> None:
    result = score_niche_relevance(
        title="家族修仙：老祖建立长生世家",
        description=None,
        query="家族修仙",
    )
    assert result.score >= 0.90
    assert result.status == "relevant"
    assert "家族修仙" in result.match_reasons


def test_relevance_partial_motif_and_configurable_profile() -> None:
    partial = score_niche_relevance(
        title="家族老祖觉醒，弱小家族崛起",
        description="",
        query="家族修仙",
        topic_terms="家族,修仙,老祖",
    )
    configured = score_niche_relevance(
        title="家族老祖崛起",
        description=None,
        query="一口气看完",
        topic_terms="家族,老祖",
    )
    assert partial.score >= 0.55
    assert configured.score >= 0.55
    assert "老祖" in partial.match_reasons


def test_relevance_downranks_unrelated_chinese_drama() -> None:
    result = score_niche_relevance(
        title="豪门爱情短剧一口气看完",
        description="热门中国电视剧",
        query="家族修仙 一口气看完",
        topic_terms="家族,修仙,老祖",
        exclusion_terms="短剧,电视剧",
    )
    assert result.score < 0.55
    assert result.status == "low_relevance"
    assert any(reason.startswith("excluded:") for reason in result.match_reasons)


def test_relevance_handles_vietnamese_english_and_unicode_normalization() -> None:
    vietnamese = score_niche_relevance(
        title="Gia toc tu tien truong sinh",
        description="",
        query="gia tộc tu tiên",
    )
    english = score_niche_relevance(
        title="A cultivation family rises from nothing",
        description=None,
        query="family cultivation",
        topic_terms="family,cultivation",
    )
    assert vietnamese.score >= 0.55
    assert english.score >= 0.55
    assert normalize_relevance_text("TU TIÊN") == "tu tien"


def test_relevance_empty_title_and_missing_description_are_safe() -> None:
    result = score_niche_relevance(title="", description=None, query="家族修仙")
    description_only = score_niche_relevance(
        title="", description="家族修仙故事", query="家族修仙",
    )
    title_match = score_niche_relevance(
        title="家族修仙故事", description="", query="家族修仙",
    )
    assert result.score == 0.0
    assert result.status == "low_relevance"
    assert result.match_reasons == ()
    assert 0 < description_only.score < title_match.score


def test_relevance_threshold_is_configurable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHANNEL_AGENT_TREND_MIN_RELEVANCE", "0.90")
    result = score_niche_relevance(
        title="家老祖崛起", description="", query="家族修仙",
        topic_terms="老祖",
    )
    assert 0 < result.score < 0.90
    assert result.status == "low_relevance"


def test_opportunity_score_keeps_trend_and_relevance_separate() -> None:
    assert opportunity_score(0.95, 0.10) == pytest.approx(0.095)
    assert opportunity_score(0.70, 0.90) == pytest.approx(0.63)
    assert opportunity_score(0.70, 0.90) > opportunity_score(0.95, 0.10)
    assert opportunity_score(None, 0.9) is None


def make_store(tmp_path: Path) -> tuple[Store, int, int]:
    store = Store(tmp_path / "web.sqlite3")
    user_a = store.create_user("user-a", "x")
    user_b = store.create_user("user-b", "x")
    return store, user_a, user_b


def test_query_crud_is_per_user(tmp_path: Path) -> None:
    store, user_a, user_b = make_store(tmp_path)
    query_id = store.create_trend_query(
        user_a, "长生家族", topic_terms="家族,长生", exclusion_terms="短剧",
    )

    assert len(store.list_trend_queries(user_a)) == 1
    assert store.list_trend_queries(user_a)[0]["topic_terms"] == "家族,长生"
    assert store.list_trend_queries(user_a)[0]["exclusion_terms"] == "短剧"
    assert store.list_trend_queries(user_b) == []
    assert store.update_trend_query(user_b, query_id, query="stolen") is False
    assert store.delete_trend_query(user_b, query_id) is False
    assert store.update_trend_query(user_a, query_id, query="长生世家") is True


class ScannerProvider:
    def __init__(self) -> None:
        self.captured_at = NOW
        self.views = 1000
        self.youtube = SimpleNamespace(get_own_channel=lambda user_id: SimpleNamespace(channel_id="own"))

    def search(self, user_id: int, query_value: dict[str, Any], *, max_results: int) -> SearchResult:
        return SearchResult((ResearchVideo(
            video_id="same-video", title="家族修仙 external idea", channel_id="external", channel_title="Research Channel",
            published_at=NOW - timedelta(hours=10), duration_seconds=1800, view_count=self.views,
            like_count=50, comment_count=5, thumbnail_url="https://thumb", search_query=query_value["query"],
            collected_at=self.captured_at,
        ),), {"external": "uploads"})

    def channel_baselines(self, *args: Any, **kwargs: Any) -> dict[str, float]:
        return {"external": 200.0}


def test_scanner_deduplicates_matches_and_creates_repeated_snapshots(tmp_path: Path) -> None:
    store, user_a, _ = make_store(tmp_path)
    store.create_trend_query(user_a, "家族修仙")
    store.create_trend_query(user_a, "长生家族")
    provider = ScannerProvider()
    scanner = YouTubeTrendScanner(store, provider)

    first = scanner.scan(user_a)
    candidate = store.list_trend_candidates(user_a)[0]
    assert first["candidates_found"] == 1
    assert candidate["snapshot_count"] == 1
    assert candidate["observed_vph"] is None
    assert candidate["approx_vph"] is not None
    assert candidate["rights_status"] == "idea_only"
    assert candidate["niche_relevance_score"] >= 0.55
    assert candidate["opportunity_score"] == pytest.approx(
        candidate["trend_score"] * candidate["niche_relevance_score"]
    )
    assert candidate["relevance_status"] == "relevant"
    assert candidate["match_reasons"]
    assert "家族修仙" in candidate["matched_queries"] and "长生家族" in candidate["matched_queries"]
    assert candidate["outlier_ratio"] == 5.0

    provider.captured_at = NOW + timedelta(hours=2)
    provider.views = 2200
    scanner.scan(user_a)
    candidate = store.list_trend_candidates(user_a)[0]
    assert candidate["snapshot_count"] == 2
    assert candidate["observed_vph"] == 600.0
    assert len(store.list_trend_snapshots(user_a, candidate["id"])) == 2


def test_candidate_reads_are_per_user_and_do_not_expose_storage_fields(tmp_path: Path) -> None:
    store, user_a, user_b = make_store(tmp_path)
    store.create_trend_query(user_a, "query")
    YouTubeTrendScanner(store, ScannerProvider()).scan(user_a)
    candidate = store.list_trend_candidates(user_a)[0]

    assert store.get_trend_candidate(user_b, candidate["id"]) is None
    assert store.list_trend_snapshots(user_b, candidate["id"]) == []
    assert "raw_json" not in candidate and "local_path" not in candidate


def test_relevance_threshold_filter_and_opportunity_ranking_are_per_user(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, user_a, user_b = make_store(tmp_path)
    relevant_id = store.upsert_trend_candidate(user_a, {
        "scan_id": "scan", "source_id": "relevant", "source_url": "https://youtube.test/relevant",
        "title": "Relevant", "captured_at": 1.0,
    }, "idea_only")
    viral_id = store.upsert_trend_candidate(user_a, {
        "scan_id": "scan", "source_id": "viral", "source_url": "https://youtube.test/viral",
        "title": "Unrelated viral", "captured_at": 1.0,
    }, "idea_only")
    store.update_trend_candidate_score(
        user_a, relevant_id, trend_score=0.70, niche_relevance_score=0.90,
        opportunity_score=0.63, relevance_status="relevant", match_reason_json='["cultivation"]',
    )
    store.update_trend_candidate_score(
        user_a, viral_id, trend_score=0.99, niche_relevance_score=0.10,
        opportunity_score=0.099, relevance_status="low_relevance", match_reason_json="[]",
    )

    ranked = store.list_trend_candidates(
        user_a, min_relevance=0.55, include_filtered=False,
    )
    audit = store.list_trend_candidates(user_a, include_filtered=True)
    assert [item["id"] for item in ranked] == [relevant_id]
    assert [item["id"] for item in audit] == [relevant_id, viral_id]
    assert store.list_trend_candidates(user_b, include_filtered=True) == []
    monkeypatch.setenv("AI_CHANNEL_AGENT_ENABLED", "true")
    api_ranked = trend_candidates(
        limit=50, min_score=0.0, min_relevance=0.55, include_filtered=False,
        user_id=user_a, store=store,
    )
    api_audit = trend_candidates(
        limit=50, min_score=0.0, min_relevance=None, include_filtered=True,
        user_id=user_a, store=store,
    )
    assert [item["id"] for item in api_ranked] == [relevant_id]
    assert [item["id"] for item in api_audit] == [relevant_id, viral_id]


def test_duplicate_scan_for_same_user_is_rejected(tmp_path: Path) -> None:
    store, user_a, _ = make_store(tmp_path)
    store.create_trend_query(user_a, "query")
    scanner = YouTubeTrendScanner(store, ScannerProvider())
    YouTubeTrendScanner._running_users.add(user_a)
    try:
        with pytest.raises(TrendScanAlreadyRunning):
            scanner.scan(user_a)
    finally:
        YouTubeTrendScanner._running_users.discard(user_a)


def test_disabled_feature_blocks_candidate_endpoint(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AI_CHANNEL_AGENT_ENABLED", "false")
    store, user_a, _ = make_store(tmp_path)
    with pytest.raises(Exception) as exc_info:
        trend_candidates(limit=50, min_score=0.0, min_relevance=None,
                         include_filtered=False, user_id=user_a, store=store)
    assert getattr(exc_info.value, "status_code", None) == 404
