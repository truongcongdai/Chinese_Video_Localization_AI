from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

import pytest
from pydantic import ValidationError

from tests.test_channel_agent_opportunities import evidence_store
from tests.test_channel_agent_production_assets import FakeProvider, _item
from tests.test_cp8_publishing import StubPublisher, setup_publish, submit
from universal_video_ai.channel_agent.feedback_learning import (
    FeedbackLearningError, FeedbackLearningNotFound, FeedbackLearningService,
    YouTubePerformanceProvider,
)
from universal_video_ai.channel_agent.opportunities import ContentOpportunityService
from universal_video_ai.channel_agent.production_assets import ProductionAssetService
from universal_video_ai.channel_agent.production_publishing import ProductionPublishingService
from universal_video_ai.channel_agent.youtube import YouTubeAnalyticsUnavailableError
from universal_video_ai.web.auth import get_current_user_id
from universal_video_ai.web.channel_agent_router import (
    LearningRecommendationBody, PerformanceRefreshBody, router,
)
from universal_video_ai.web.store import Store

NOW = datetime(2026, 9, 6, 12, tzinfo=timezone.utc).timestamp()


def metrics(**changes):
    result = {
        "youtube_video_id": "video-123", "channel_id": "channel-1",
        "video_published_at": NOW - 24 * 3600, "duration_seconds": 1800,
        "privacy": "public", "views": 1000, "impressions": 10000,
        "impressions_ctr": 6.0, "watch_time_minutes": 8000,
        "average_view_duration_seconds": 480, "average_view_percentage": 45,
        "likes": 80, "comments": 20, "subscribers_gained": 15,
        "subscribers_lost": 2, "retention_available": True,
        "retention": [{"elapsed_ratio": 0.0, "retention_ratio": .8},
                      {"elapsed_ratio": .5, "retention_ratio": .7},
                      {"elapsed_ratio": .8, "retention_ratio": .4}],
        "traffic_sources": [{"insightTrafficSourceType": "YT_SEARCH", "views": 500}],
        "device_types": [{"deviceType": "MOBILE", "views": 600}],
        "countries": [{"country": "VN", "views": 700}],
        "audience_segments": None, "playlist_sources": None,
    }
    result.update(changes)
    return result


class FakeAnalytics:
    def __init__(self, payload=None):
        self.payload = payload or metrics()
        self.calls = 0

    def fetch(self, user_id, job):
        self.calls += 1
        return dict(self.payload)


class LearningLLM:
    name = "ollama"

    def __init__(self, bad=False):
        self.bad, self.prompt = bad, ""

    def generate_structured(self, *, user_prompt, **kwargs):
        self.prompt = user_prompt
        evidence = ["invented_metric"] if self.bad else ["ctr_vs_baseline"]
        return json.dumps({
            "strengths": ["Packaging has a supported signal"], "weaknesses": [],
            "likely_causes": ["This is possible, not proven"],
            "recommendations": [{"category": "title", "reason": "Test a truthful variant",
                "evidence": evidence, "applicable_future_context": "Similar new videos"}],
        })


def cp9_setup(tmp_path, payload=None, llm=None):
    store, owner, foreign, item, render, _ = setup_publish(tmp_path)
    remote = {"status": {"privacyStatus": "public", "uploadStatus": "processed"},
              "processingDetails": {"processingStatus": "succeeded"}}
    publishing = ProductionPublishingService(store, publisher=StubPublisher(remote))
    job = submit(publishing, owner, item, render, privacy="unlisted",
                 dry_run=False, confirm_publish=True)
    job = publishing.run(owner, item, job["id"])
    provider = FakeAnalytics(payload or metrics())
    service = FeedbackLearningService(
        store, provider=provider, llm=llm, now=lambda: NOW)
    return store, owner, foreign, item, job, service, provider


def historical_snapshot(store, owner, item, source_job, video_id, *,
                        age=24, content_type="long_form", duration=1800,
                        views=500, impressions=10000, ctr=10, watch_pct=50,
                        likes=30, comments=10, captured=NOW):
    data = {
        "render_job_id": source_job["render_job_id"],
        "metadata_asset_id": source_job["metadata_asset_id"],
        "channel_id": "channel-1", "channel_title": "Owned",
        "title": video_id, "description": "history", "tags": [],
        "privacy": "public", "status": "published", "dry_run": False,
        "idempotency_key": "history-" + video_id, "upload_size": 1, "package": {},
    }
    job = store.create_production_publishing_job(owner, item, data)
    store.update_production_publishing_job(
        owner, item, job["id"], {"external_video_id": video_id})
    return store.insert_video_performance_snapshot(owner, {
        "channel_id": "channel-1", "production_item_id": item,
        "publishing_job_id": job["id"], "youtube_video_id": video_id,
        "captured_at": captured, "video_published_at": captured - age * 3600,
        "video_age_hours": age, "evaluation_window_hours": age,
        "content_type": content_type, "duration_seconds": duration,
        "duration_bucket": ("short" if duration <= 60 else
                            "medium" if duration <= 1200 else "long"),
        "views": views, "impressions": impressions,
        "impressions_ctr": ctr, "watch_time_minutes": views * 5,
        "average_view_duration_seconds": 300, "average_view_percentage": watch_pct,
        "likes": likes, "comments": comments, "subscribers_gained": 2,
        "subscribers_lost": 0, "retention_available": False, "retention": [],
        "traffic_sources": None, "device_types": None, "countries": None,
        "audience_segments": None, "playlist_sources": None, "data_quality": {},
    })


def test_snapshot_history_is_immutable_and_explicitly_linked(tmp_path):
    store, owner, _, item, job, service, _ = cp9_setup(tmp_path)
    first = service.refresh(owner, item, job["id"])
    second = service.refresh(owner, item, job["id"], evaluation_window_hours=48)
    assert first["snapshot"]["id"] != second["snapshot"]["id"]
    assert len(service.snapshots(owner, item, job["id"])) == 2
    assert first["snapshot"]["production_item_id"] == item
    assert first["snapshot"]["publishing_job_id"] == job["id"]
    assert first["snapshot"]["youtube_video_id"] == "video-123"
    assert second["snapshot"]["evaluation_window_hours"] == 48
    restarted = FeedbackLearningService(
        Store(store.db_path), provider=FakeAnalytics(), now=lambda: NOW)
    assert len(restarted.snapshots(owner, item, job["id"])) == 2


def test_missing_metrics_stay_null_and_retention_unavailable(tmp_path):
    missing = metrics(**{name: None for name in (
        "views", "impressions", "impressions_ctr", "watch_time_minutes",
        "average_view_duration_seconds", "average_view_percentage", "likes",
        "comments", "subscribers_gained", "subscribers_lost")})
    missing.update(retention_available=False, retention=[])
    _, owner, _, item, job, service, _ = cp9_setup(tmp_path, missing)
    result = service.refresh(owner, item, job["id"])
    assert all(result["snapshot"][name] is None for name in (
        "views", "impressions", "impressions_ctr", "subscribers_gained"))
    assert result["snapshot"]["retention_available"] is False
    assert result["evaluation"]["status"] == "INSUFFICIENT_DATA"


def test_zero_impressions_and_small_new_sample_are_insufficient(tmp_path):
    payload = metrics(impressions=0, impressions_ctr=None, views=10,
                      video_published_at=NOW - 1800)
    _, owner, _, item, job, service, _ = cp9_setup(tmp_path, payload)
    result = service.refresh(owner, item, job["id"])
    assert result["snapshot"]["impressions"] == 0
    assert result["snapshot"]["impressions_ctr"] is None
    assert result["evaluation"]["dimensions"]["click"]["status"] == "insufficient_data"
    assert result["evaluation"]["status"] == "INSUFFICIENT_DATA"


def test_owner_isolation_idor_and_channel_mismatch(tmp_path):
    store, owner, foreign, item, job, service, _ = cp9_setup(tmp_path)
    service.refresh(owner, item, job["id"])
    with pytest.raises(FeedbackLearningNotFound):
        service.snapshots(foreign, item, job["id"])
    expiry = NOW + 3600
    store.upsert_social_account(owner, "youtube", "token", "refresh", expiry,
                                "Other", "other-channel", "scope")
    with pytest.raises(FeedbackLearningError, match="no longer matches"):
        service.refresh(owner, item, job["id"])


def test_age_content_duration_baseline_and_normalization(tmp_path):
    store, owner, _, item, job, service, _ = cp9_setup(tmp_path)
    for index in range(3):
        historical_snapshot(store, owner, item, job, f"history-{index}",
                            age=23 + index, ctr=10, watch_pct=50, views=500)
    old = historical_snapshot(store, owner, item, job, "two-year-lifetime",
                              age=24 * 730, ctr=1, views=999999)
    result = service.refresh(owner, item, job["id"])
    baseline = result["evaluation"]["baseline"]
    assert baseline["sufficient"] and baseline["sample_count"] == 3
    assert baseline["matching"] == "age_content_duration"
    assert result["evaluation"]["normalized"]["ctr_vs_baseline"] == .6
    assert old["id"] not in {row.get("id") for row in baseline.get("rows", [])}


def test_learning_signals_and_cautious_root_cause(tmp_path):
    store, owner, _, item, job, service, _ = cp9_setup(
        tmp_path, metrics(views=1500, impressions_ctr=5))
    for index in range(3):
        historical_snapshot(store, owner, item, job, f"base-{index}",
                            views=500, ctr=10, watch_pct=45)
    result = service.refresh(owner, item, job["id"])
    assert {row["dimension"] for row in result["signals"]} == {
        "topic_strength", "hook_strength", "title_strength", "thumbnail_strength",
        "retention_strength", "pacing_strength", "length_fit", "audience_fit",
        "search_fit"}
    evidence = result["signals"][0]["evidence"]
    assert any(row["id"] == "likely_packaging_issue" for row in evidence)
    assert all(row["likelihood"] in {"likely", "possible", "low-confidence"}
               for row in evidence)


def test_grounded_ollama_report_and_metric_hallucination_rejection(tmp_path):
    llm = LearningLLM()
    _, owner, _, item, job, service, _ = cp9_setup(tmp_path, llm=llm)
    service.refresh(owner, item, job["id"])
    report = service.generate_report(owner, item, job["id"])
    assert report["llm_status"] == "completed"
    assert "allowed_evidence_ids" in llm.prompt
    assert report["performance_summary"]["metrics"]["views"] == 1000
    bad = LearningLLM(bad=True)
    service.llm = bad
    rejected = service.generate_report(owner, item, job["id"])
    assert rejected["llm_status"] == "offline"
    assert "invented_metric" not in json.dumps(rejected)
    assert rejected["version"] == 2
    with pytest.raises(ValueError, match="numeric metric"):
        service._llm_validator({
            "strengths": ["CTR was 99 percent"], "weaknesses": [],
            "likely_causes": [], "recommendations": []}, {"ctr_vs_baseline"})


def test_offline_report_profile_recency_and_human_review(tmp_path):
    _, owner, _, item, job, service, _ = cp9_setup(tmp_path)
    service.refresh(owner, item, job["id"])
    report = service.generate_report(owner, item, job["id"])
    assert report["llm_status"] == "offline"
    assert report["recommendations"][0]["review_status"] == "pending"
    recommendation = report["recommendations"][0]
    applied = service.review_recommendation(
        owner, item, job["id"], report["id"], recommendation["id"], "applied")
    assert applied["recommendations"][0]["review_status"] == "applied"
    profile = service.profile(owner)
    assert profile["sample_count"] == 1
    assert profile["evidence_refs"] == [f"report:{report['id']}"]
    assert profile["patterns"]["applied_recommendations"]
    old_weight = service.recency_weight(NOW - 720 * 86400, NOW)
    recent_weight = service.recency_weight(NOW - 10 * 86400, NOW)
    assert old_weight < recent_weight
    ignored = service.review_recommendation(
        owner, item, job["id"], report["id"], recommendation["id"], "ignored")
    assert ignored["recommendations"][0]["review_status"] == "ignored"


def test_cp5_learning_adjustment_is_bounded_and_inspectable(tmp_path):
    store, owner, _, candidate_id = evidence_store(tmp_path)
    opportunity, _ = ContentOpportunityService(store).create(
        owner, source_type="candidate", source_id=str(candidate_id))
    base = opportunity["opportunity_rank_score"]
    store.upsert_channel_learning_profile(owner, "channel-1", {
        "sample_count": 6, "evidence_refs": ["report:1"],
        "patterns": {"winning_topics": [{
            "topic": opportunity["topic"], "weighted_score": 1.3,
            "sample_count": 6, "confidence": .9, "evidence_refs": ["report:1"]}]}})
    learned = ContentOpportunityService(store).get(owner, opportunity["id"])
    assert 0 < learned["learning_adjustment"] <= 5
    assert learned["opportunity_rank_score"] > base
    assert set(learned["learning_signals"]) == {
        "channel_topic_affinity", "historical_performance_fit",
        "format_fit", "duration_fit"}


class CaptureProvider(FakeProvider):
    def __init__(self):
        super().__init__()
        self.prompts = []

    def generate_structured(self, *, user_prompt, **kwargs):
        self.prompts.append(user_prompt)
        return super().generate_structured(user_prompt=user_prompt, **kwargs)


def test_cp7a_uses_learning_only_for_new_generation(tmp_path):
    store, owner, _, item = _item(tmp_path, "cp9-context")
    store.upsert_channel_learning_profile(owner, "channel-1", {
        "sample_count": 4, "evidence_refs": ["report:4"],
        "patterns": {"strong_hook_patterns": [{"pattern": "short promise"}],
                     "applied_recommendations": [{"category": "hook",
                                                  "reason": "Open directly"}]}})
    provider = CaptureProvider()
    asset = ProductionAssetService(store, provider).generate_blueprint(owner, item)["asset"]
    assert any("Owner learning for NEW generation only" in prompt
               and "short promise" in prompt for prompt in provider.prompts)
    assert asset["payload"]["learning_profile_version"] == 1


def test_cp9_routes_are_authenticated_and_bodies_reject_user_id():
    routes = [route for route in router.routes
              if "performance" in route.path or "learning" in route.path]
    assert routes
    for route in routes:
        assert any(dep.call is get_current_user_id
                   for dep in route.dependant.dependencies)
    with pytest.raises(ValidationError):
        PerformanceRefreshBody(user_id=999)
    with pytest.raises(ValidationError):
        LearningRecommendationBody(user_id=999)


def test_cp9_ui_has_metrics_learning_and_review_controls():
    root = Path(__file__).parents[1]
    index = (root / "src/universal_video_ai/web/static/index.html").read_text("utf-8")
    app = (root / "src/universal_video_ai/web/static/app.js").read_text("utf-8")
    for marker in ("Winning Topics", "Underperforming Topics", "Strong Hook Patterns",
                   "Weak Retention Patterns", "Title / Packaging Trends",
                   "Preferred Duration Range"):
        assert marker in index
    for marker in ("CP9 Performance / Learning", "INSUFFICIENT DATA",
                   "Apply learning suggestion", "Refresh metrics",
                   "Generate learning report", "/performance/refresh"):
        assert marker in app
    assert "user_id:" not in app[app.index("CP9 Performance / Learning"):]


def test_cp9_fresh_old_and_repeated_database_initialization(tmp_path):
    fresh = tmp_path / "fresh.sqlite3"
    Store(fresh); Store(fresh)
    with sqlite3.connect(fresh) as conn:
        for table in ("video_performance_snapshots", "content_learning_signals",
                      "video_learning_reports", "learning_recommendation_actions",
                      "channel_learning_profiles"):
            assert conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (table,)).fetchone()
    legacy = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(legacy) as conn:
        conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT UNIQUE,"
                     " password_hash TEXT, created_at REAL)")
    Store(legacy); Store(legacy)


class FakeYouTube:
    def data_request(self, user_id, resource, params):
        return {"items": [{"id": "video-123", "snippet": {
            "channelId": "channel-1", "publishedAt": "2026-09-05T12:00:00Z"},
            "contentDetails": {"duration": "PT30M"},
            "status": {"privacyStatus": "unlisted"}}]}

    def _analytics_report(self, user_id, start, end, *, metrics, **params):
        unavailable = (
            "videoThumbnailImpressions" in metrics
            or params.get("dimensions") == "elapsedVideoTimeRatio"
        )
        if unavailable:
            raise YouTubeAnalyticsUnavailableError()
        if "dimensions" in params:
            return [params["dimensions"], "views"], [[params["dimensions"], 12]]
        headers = ["views", "estimatedMinutesWatched", "averageViewDuration",
                   "averageViewPercentage", "subscribersGained", "subscribersLost",
                   "likes", "comments"]
        return headers, [[25, 100, 240, 40, 2, 1, 5, 1]]


def test_official_provider_keeps_unavailable_packaging_and_retention_null():
    provider = YouTubePerformanceProvider(FakeYouTube())
    result = provider.fetch(1, {
        "external_video_id": "video-123", "channel_id": "channel-1",
        "published_at": NOW - 86400})
    assert result["views"] == 25
    assert result["impressions"] is None
    assert result["impressions_ctr"] is None
    assert result["retention_available"] is False
    assert result["retention"] == []
    assert result["duration_seconds"] == 1800
