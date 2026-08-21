from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from universal_video_ai import config
from universal_video_ai.channel_agent.brain import ContentEvidenceAssembler, evidence_hash
from universal_video_ai.channel_agent.opportunities import (
    ContentOpportunityService,
    OpportunityError,
    OpportunityNotFound,
    gap_key,
)
from universal_video_ai.web.channel_agent_router import (
    OpportunityCreateBody,
    OpportunityEditBody,
    OpportunityStatusBody,
    change_content_opportunity_status,
    content_opportunities,
    content_opportunity_detail,
    create_content_opportunity,
    edit_content_opportunity,
    refresh_content_opportunity,
)
from universal_video_ai.web.store import Store


def candidate(
    store: Store, user_id: int, suffix: str = "a", *, relevance: float = .9,
    status: str = "relevant", trend: float = .8, opportunity: float = .75,
    observed: float | None = 100.0,
) -> int:
    item_id = store.upsert_trend_candidate(user_id, {
        "scan_id": "scan", "source_id": f"video-{suffix}",
        "source_url": f"https://youtube.test/video-{suffix}",
        "title": f"家族老祖崛起 {suffix}", "channel_id": f"UC-{suffix}",
        "channel_title": f"Trend {suffix}", "captured_at": 1.0,
        "view_count": 10_000, "published_at": "2026-08-20T00:00:00Z",
    }, "idea_only")
    store.update_trend_candidate_score(
        user_id, item_id, observed_vph=observed, approx_vph=50.0,
        engagement_rate=.05, outlier_ratio=5.0, trend_score=trend,
        niche_relevance_score=relevance, opportunity_score=opportunity,
        relevance_status=status, score_confidence="high" if observed else "low",
        available_signal_count=5 if observed else 2,
        match_reason_json='["家族", "老祖"]',
    )
    return item_id


def competitor(store: Store, user_id: int, suffix: str, *, score: float = .8) -> int:
    competitor_id = store.upsert_competitor(user_id, {
        "channel_id": f"COMP-{suffix}", "channel_title": f"Competitor {suffix}",
        "channel_url": f"https://youtube.test/channel/{suffix}",
    })
    video_id = f"breakout-{suffix}"
    store.upsert_competitor_video(competitor_id, {
        "video_id": video_id, "video_url": f"https://youtube.test/{video_id}",
        "title": "家族老祖长生", "duration_seconds": 3600,
        "published_at": "2026-08-19T00:00:00Z", "view_count": 20_000,
        "engagement_rate": .04, "outlier_ratio": 5.0, "breakout_strength": "strong",
    })
    store.update_competitor_analysis(user_id, competitor_id, {
        "sample_mode": "long", "recent_upload_count": 10, "median_views": 4_000,
        "breakout_frequency": .3, "breakout_count": 3, "competitor_score": score,
        "competitor_relevance_score": .9, "competitor_relevance_status": "qualified",
        "competitor_match_reasons": ["8/10 niche videos"], "niche_hit_rate": .8,
        "niche_matching_video_count": 8, "niche_analyzed_video_count": 10,
        "score_confidence": "high", "patterns": [{
            "pattern": "家族 + 老祖", "pattern_quality_score": .9,
            "pattern_quality_status": "qualified", "pattern_support": 3,
            "video_count": 3, "breakout_count": 2, "median_outlier": 5.0,
            "evidence": [{"video_id": video_id, "video_url": f"https://youtube.test/{video_id}",
                          "title": "家族老祖长生", "outlier_ratio": 5.0}],
        }], "duration_buckets": [], "analyzed_at": 1.0,
    })
    return competitor_id


def evidence_store(tmp_path: Path) -> tuple[Store, int, int, int]:
    store = Store(tmp_path / "cp5.sqlite3")
    user_a = store.create_user("a", "x")
    user_b = store.create_user("b", "x")
    candidate_id = candidate(store, user_a)
    competitor(store, user_a, "one")
    competitor(store, user_a, "two")
    return store, user_a, user_b, candidate_id


def completed_brain_run(store: Store, user_id: int, candidate_id: int, mode: str) -> int:
    bundle = ContentEvidenceAssembler(store).assemble(
        user_id, selector_type="candidate", selector_id=str(candidate_id)
    )
    run_id = store.create_content_brain_run(
        user_id, request_type=mode, provider="ollama", model="local",
        evidence_hash=evidence_hash(bundle), evidence=bundle,
    )
    result: dict[str, Any] = {"request_type": mode, "evidence_confidence": bundle["evidence_confidence"]}
    if mode == "content_angles":
        result["angles"] = [{
            "angle_name": "Lão tổ ẩn thế", "audience_promise": "Theo dõi gia tộc qua nhiều thế hệ",
            "core_conflict": "Gia tộc suy vong", "differentiation": "Bình luận Việt nguyên bản",
            "why_supported": "Motif đã quan sát", "evidence_ids": [f"candidate:{candidate_id}"],
            "risk": "Mẫu nghiên cứu còn nhỏ",
        }]
    elif mode == "title_hooks":
        result.update({
            "titles": [{"title": "Khi lão tổ buộc phải xuất thế", "primary_motif": "lão tổ",
                        "reason": "Motif đã quan sát", "evidence_ids": [f"candidate:{candidate_id}"]}],
            "hooks": [{"hook": "Một gia tộc còn đúng bảy ngày.",
                       "evidence_ids": [f"candidate:{candidate_id}"]}],
        })
    else:
        result.update({"differentiation": "Kể qua góc nhìn gia tộc Việt", "risks": ["Không sao chép nguồn"]})
    store.complete_content_brain_run(run_id, user_id, result)
    return run_id


def test_fresh_db_legacy_migration_and_idempotent_initialization(tmp_path: Path) -> None:
    fresh = Store(tmp_path / "fresh.sqlite3")
    with fresh._connect() as conn:
        assert conn.execute("SELECT name FROM sqlite_master WHERE name='content_opportunities'").fetchone()
        assert conn.execute("SELECT name FROM sqlite_master WHERE name='content_opportunity_events'").fetchone()
    legacy_path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(legacy_path) as conn:
        conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT UNIQUE, password_hash TEXT, created_at REAL)")
    Store(legacy_path)
    Store(legacy_path)
    with sqlite3.connect(legacy_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM content_opportunities").fetchone()[0] == 0


def test_create_from_qualified_candidate_score_snapshot_and_dedupe(tmp_path: Path) -> None:
    store, user, _, candidate_id = evidence_store(tmp_path)
    service = ContentOpportunityService(store)
    first, created = service.create(user, source_type="candidate", source_id=str(candidate_id))
    duplicate, created_again = service.create(user, source_type="candidate", source_id=str(candidate_id))
    assert created and not created_again and duplicate["id"] == first["id"]
    assert 0 <= first["evidence_score"] <= 100
    assert first["evidence_hash"] == evidence_hash(first["evidence_snapshot"])
    assert first["rights_status"] == "idea_only"
    assert first["status"] == "draft"
    assert first["score_breakdown"]["formula"].startswith("available-weight")
    assert first["events"][0]["event_type"] == "created"


def test_create_from_gap_and_completed_brain_run_reuses_cp4_without_provider(tmp_path: Path) -> None:
    store, user, _, candidate_id = evidence_store(tmp_path)
    service = ContentOpportunityService(store)
    gap, created = service.create(user, source_type="gap", source_id=gap_key("家族 + 老祖"))
    assert created and gap["gap_quality_score"] > .85
    run_id = completed_brain_run(store, user, candidate_id, "content_angles")
    completed_brain_run(store, user, candidate_id, "title_hooks")
    card, was_created = service.create(user, source_type="brain_run", source_id=str(run_id))
    assert was_created
    assert card["ai_enrichment_status"] == "available"
    assert card["system_recommended_angle"] == "Lão tổ ẩn thế"
    assert card["system_suggested_title"] == "Khi lão tổ buộc phải xuất thế"


def test_existing_candidate_card_is_enriched_by_later_matching_brain_source(tmp_path: Path) -> None:
    store, user, _, candidate_id = evidence_store(tmp_path)
    service = ContentOpportunityService(store)
    original, _ = service.create(user, source_type="candidate", source_id=str(candidate_id))
    run_id = completed_brain_run(store, user, candidate_id, "content_angles")
    duplicate, created = service.create(user, source_type="brain_run", source_id=str(run_id))
    assert not created and duplicate["id"] == original["id"]
    assert duplicate["system_recommended_angle"] == "Lão tổ ẩn thế"
    assert duplicate["working_title"] is None


def test_low_relevance_and_foreign_sources_are_rejected(tmp_path: Path) -> None:
    store, user_a, user_b, _ = evidence_store(tmp_path)
    low_id = candidate(store, user_a, "low", relevance=.1, status="low_relevance")
    service = ContentOpportunityService(store)
    with pytest.raises(OpportunityError, match="niche relevance"):
        service.create(user_a, source_type="candidate", source_id=str(low_id))
    with pytest.raises(OpportunityError, match="not found"):
        service.create(user_b, source_type="candidate", source_id=str(low_id))


def test_scoring_strong_higher_missing_not_fake_zero_and_high_score_low_confidence() -> None:
    strong_breakdown, strong = ContentOpportunityService._score({
        "trend": .9, "niche_relevance": .9, "candidate_strength": .85,
        "competitor_evidence": .8, "pattern_gap_quality": .9, "evidence_confidence": 1.0,
    })
    weak_breakdown, weak = ContentOpportunityService._score({
        "trend": .4, "niche_relevance": .5, "candidate_strength": .3,
        "competitor_evidence": None, "pattern_gap_quality": None, "evidence_confidence": .3,
    })
    high_but_sparse, high_score = ContentOpportunityService._score({
        "trend": 1.0, "niche_relevance": 1.0, "candidate_strength": None,
        "competitor_evidence": None, "pattern_gap_quality": None, "evidence_confidence": None,
    })
    assert 0 <= weak < strong <= 100
    assert weak_breakdown["components"]["competitor_evidence"]["raw_points"] is None
    assert high_score == 100 and high_but_sparse["available_weight"] == 40


@pytest.mark.parametrize(
    ("competitors_count", "supply", "expected"),
    [(0, 0, "unknown"), (1, 1, "unknown"), (2, 0, "low"), (2, 2, "medium"), (4, 5, "high")],
)
def test_competition_levels(competitors_count: int, supply: int, expected: str) -> None:
    competitors_rows = [{"competitor_score": .8}] * competitors_count
    assert ContentOpportunityService._competition(
        competitors_rows, {"qualified_candidate_supply": supply}
    ) == expected


def test_rank_confidence_factor_separates_equal_scores() -> None:
    score = 80
    assert score * ContentOpportunityService.CONFIDENCE_FACTORS["high"] > score * ContentOpportunityService.CONFIDENCE_FACTORS["low"]
    assert ContentOpportunityService.COMPETITION_FACTORS["high"] == .95


def test_high_candidate_signals_can_remain_low_confidence_without_support(tmp_path: Path) -> None:
    store = Store(tmp_path / "sparse.sqlite3")
    user = store.create_user("sparse", "x")
    candidate_id = candidate(
        store, user, "sparse", trend=1.0, relevance=1.0, opportunity=1.0,
        observed=None,
    )
    card, _ = ContentOpportunityService(store).create(
        user, source_type="candidate", source_id=str(candidate_id)
    )
    assert card["evidence_score"] > 60
    assert card["evidence_confidence"] == "low"
    assert card["competition_level"] == "unknown"
    assert "qualified competitor evidence" in card["waiting_for"]


def test_status_transitions_events_and_approval_preserves_rights(tmp_path: Path) -> None:
    store, user, _, candidate_id = evidence_store(tmp_path)
    service = ContentOpportunityService(store)
    card, _ = service.create(user, source_type="candidate", source_id=str(candidate_id))
    watch = service.change_status(user, card["id"], status="watch")
    approved = service.change_status(user, card["id"], status="approved")
    archived = service.change_status(user, card["id"], status="archived")
    assert watch["status"] == "watch" and approved["rights_status"] == "idea_only"
    assert approved["approved_for_production"] is True
    assert archived["status"] == "archived"
    assert [event["event_type"] for event in archived["events"]].count("status_changed") == 3
    with pytest.raises(OpportunityError, match="Invalid"):
        service.change_status(user, card["id"], status="approved")


def test_reject_reason_and_return_to_draft(tmp_path: Path) -> None:
    store, user, _, candidate_id = evidence_store(tmp_path)
    service = ContentOpportunityService(store)
    card, _ = service.create(user, source_type="candidate", source_id=str(candidate_id))
    rejected = service.change_status(user, card["id"], status="rejected", rejection_reason="low_evidence")
    assert rejected["rejection_reason"] == "low_evidence"
    draft = service.change_status(user, card["id"], status="draft")
    assert draft["rejection_reason"] is None


def test_refresh_updates_research_and_preserves_editorial_overrides(tmp_path: Path) -> None:
    store, user, _, candidate_id = evidence_store(tmp_path)
    service = ContentOpportunityService(store)
    card, _ = service.create(user, source_type="candidate", source_id=str(candidate_id))
    service.edit(user, card["id"], working_title="My title", selected_angle="My angle", notes="Keep me", priority=88)
    store.update_trend_candidate_score(
        user, candidate_id, observed_vph=200, approx_vph=100, engagement_rate=.08,
        outlier_ratio=8, trend_score=.99, niche_relevance_score=.95,
        opportunity_score=.94, relevance_status="relevant", score_confidence="high",
        available_signal_count=5, match_reason_json='["new"]',
    )
    refreshed = service.refresh(user, card["id"])
    assert refreshed["trend_score"] == pytest.approx(.99)
    assert (refreshed["working_title"], refreshed["selected_angle"], refreshed["notes"], refreshed["priority"]) == ("My title", "My angle", "Keep me", 88)
    assert refreshed["events"][0]["event_type"] == "refreshed"


def test_list_filters_delete_and_per_user_isolation(tmp_path: Path) -> None:
    store, user_a, user_b, candidate_id = evidence_store(tmp_path)
    service = ContentOpportunityService(store)
    card, _ = service.create(user_a, source_type="candidate", source_id=str(candidate_id))
    assert service.list(user_a, statuses=["draft"], limit=20)[0]["id"] == card["id"]
    assert service.list(user_b, statuses=["draft"], limit=20) == []
    with pytest.raises(OpportunityNotFound):
        service.get(user_b, card["id"])
    with pytest.raises(OpportunityNotFound):
        service.delete(user_b, card["id"])
    service.delete(user_a, card["id"])
    with pytest.raises(OpportunityNotFound):
        service.get(user_a, card["id"])


def test_freshness_is_timestamp_based_and_does_not_invalidate_card(tmp_path: Path) -> None:
    store, user, _, candidate_id = evidence_store(tmp_path)
    card, _ = ContentOpportunityService(store).create(
        user, source_type="candidate", source_id=str(candidate_id)
    )
    with store._connect() as conn:
        conn.execute(
            "UPDATE content_opportunities SET last_refreshed_at=? WHERE id=? AND user_id=?",
            (1.0, card["id"], user),
        )
    stale = ContentOpportunityService(store).get(user, card["id"])
    assert stale["freshness_status"] == "stale" and stale["status"] == "draft"
    assert stale["opportunity_rank_score"] < card["opportunity_rank_score"]


def test_bulk_generation_is_bounded_deduplicated_and_offline(tmp_path: Path) -> None:
    store, user, _, _ = evidence_store(tmp_path)
    service = ContentOpportunityService(store)
    result = service.generate(user, limit=20)
    assert len(result["created"]) <= 20
    again = service.generate(user, limit=20)
    assert not again["created"] and again["existing"]


def test_api_filters_feature_flag_and_frontend_metrics_forbidden(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store, user, _, candidate_id = evidence_store(tmp_path)
    ContentOpportunityService(store).create(user, source_type="candidate", source_id=str(candidate_id))
    monkeypatch.setenv("AI_CHANNEL_AGENT_ENABLED", "true")
    rows = content_opportunities(status="draft", confidence=None, competition=None,
                                 source_type=None, min_score=0, limit=20,
                                 user_id=user, store=store)
    assert len(rows) == 1 and "evidence_snapshot" not in rows[0]
    assert store.get_content_opportunity(user, rows[0]["id"])["score_breakdown"]["total"] == rows[0]["evidence_score"]
    with pytest.raises(ValidationError):
        OpportunityCreateBody(source_type="candidate", source_id=str(candidate_id), evidence_score=100)
    monkeypatch.setenv("AI_CHANNEL_AGENT_ENABLED", "false")
    with pytest.raises(HTTPException) as exc:
        content_opportunities(user_id=user, store=store)
    assert exc.value.status_code == 404


def test_api_create_detail_edit_status_refresh_are_owner_scoped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store, user, foreign_user, candidate_id = evidence_store(tmp_path)
    monkeypatch.setenv("AI_CHANNEL_AGENT_ENABLED", "true")
    response = create_content_opportunity(
        OpportunityCreateBody(source_type="candidate", source_id=str(candidate_id)),
        user_id=user, store=store,
    )
    opportunity_id = response["opportunity"]["id"]
    detail = content_opportunity_detail(opportunity_id, user_id=user, store=store)
    assert detail["source_candidate_id"] == candidate_id
    edited = edit_content_opportunity(
        opportunity_id, OpportunityEditBody(working_title="Editorial", priority=50),
        user_id=user, store=store,
    )
    assert edited["working_title"] == "Editorial"
    watched = change_content_opportunity_status(
        opportunity_id, OpportunityStatusBody(status="watch"), user_id=user, store=store,
    )
    assert watched["status"] == "watch"
    refreshed = refresh_content_opportunity(opportunity_id, user_id=user, store=store)
    assert refreshed["working_title"] == "Editorial"
    with pytest.raises(HTTPException) as exc:
        content_opportunity_detail(opportunity_id, user_id=foreign_user, store=store)
    assert exc.value.status_code == 404
