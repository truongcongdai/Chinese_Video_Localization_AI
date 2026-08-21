from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from universal_video_ai.channel_agent.opportunities import ContentOpportunityService
from universal_video_ai.channel_agent.production import (
    ProductionError,
    ProductionNotFound,
    ProductionQueueService,
)
from universal_video_ai.web.channel_agent_router import (
    ProductionCreateBody,
    ProductionEditBody,
    ProductionStatusBody,
    ProductionTaskEditBody,
    ProductionTaskStatusBody,
    create_production_item,
    edit_production_item,
    production_item_detail,
    production_queue,
)
from universal_video_ai.web.store import Store


def _candidate(store: Store, user_id: int, suffix: str = "a") -> int:
    candidate_id = store.upsert_trend_candidate(user_id, {
        "scan_id": "cp6", "source_id": f"video-{suffix}",
        "source_url": f"https://youtube.test/video-{suffix}",
        "title": f"家族老祖崛起 {suffix}", "channel_id": f"UC-{suffix}",
        "channel_title": f"Trend {suffix}", "captured_at": 1.0,
        "view_count": 10_000, "published_at": "2026-08-20T00:00:00Z",
    }, "idea_only")
    store.update_trend_candidate_score(
        user_id, candidate_id, observed_vph=100, approx_vph=50,
        engagement_rate=.05, outlier_ratio=5, trend_score=.8,
        niche_relevance_score=.9, opportunity_score=.75,
        relevance_status="relevant", score_confidence="high",
        available_signal_count=5, match_reason_json='["家族", "老祖"]',
    )
    return candidate_id


def _approved(tmp_path: Path, *, target_format: str = "long_form") -> tuple[Store, int, int, dict]:
    store = Store(tmp_path / "production.sqlite3")
    user = store.create_user("owner", "x")
    foreign = store.create_user("foreign", "x")
    candidate_id = _candidate(store, user)
    opportunities = ContentOpportunityService(store)
    card, _ = opportunities.create(user, source_type="candidate", source_id=str(candidate_id))
    opportunities.edit(
        user, card["id"], working_title="Lão tổ trở về", selected_angle="Gia tộc suy vong",
        notes="CP5 editorial note", priority=80, target_format=target_format,
        target_duration_min=60, target_duration_max=90,
    )
    # Simulate already-persisted CP4 enrichment consumed by CP5. CP6 never calls a model.
    store.update_content_opportunity(user, card["id"], {
        "ai_enrichment_status": "available", "brain_run_id": 42,
        "system_audience_promise": "Theo dõi gia tộc hồi sinh qua nhiều thế hệ",
        "system_core_conflict": "Lão tổ phải lộ diện khi gia tộc sắp diệt vong",
        "system_differentiation": "Góc nhìn tiến trình gia tộc Việt nguyên bản",
        "system_suggested_hook": "Gia tộc chỉ còn bảy ngày.",
        "system_suggested_title": "Khi lão tổ buộc phải xuất thế",
        "system_risks_json": json.dumps(["Mẫu nghiên cứu còn nhỏ"]),
    })
    approved = opportunities.change_status(user, card["id"], status="approved")
    return store, user, foreign, approved


def _task(item: dict, kind: str) -> dict:
    return next(task for task in item["tasks"] if task["task_type"] == kind)


def _complete(service: ProductionQueueService, user: int, item_id: int, kind: str) -> dict:
    item = service.get(user, item_id)
    task = _task(item, kind)
    service.change_task_status(user, item_id, task["id"], status="in_progress")
    return service.change_task_status(user, item_id, task["id"], status="completed")


def test_fresh_legacy_and_idempotent_migration(tmp_path: Path) -> None:
    fresh = Store(tmp_path / "fresh.sqlite3")
    with fresh._connect() as conn:
        names = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
    assert {"production_items", "production_tasks", "production_events"} <= names
    legacy = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(legacy) as conn:
        conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT UNIQUE, password_hash TEXT, created_at REAL)")
    Store(legacy)
    Store(legacy)
    with sqlite3.connect(legacy) as conn:
        assert conn.execute("SELECT COUNT(*) FROM production_items").fetchone()[0] == 0


@pytest.mark.parametrize("status", ["draft", "watch", "rejected", "archived"])
def test_creation_requires_current_approved_state(tmp_path: Path, status: str) -> None:
    store, user, _, card = _approved(tmp_path)
    if status == "draft":
        with store._connect() as conn:
            conn.execute("UPDATE content_opportunities SET status='draft' WHERE id=?", (card["id"],))
    elif status == "watch":
        with store._connect() as conn:
            conn.execute("UPDATE content_opportunities SET status='watch' WHERE id=?", (card["id"],))
    elif status == "rejected":
        with store._connect() as conn:
            conn.execute("UPDATE content_opportunities SET status='rejected' WHERE id=?", (card["id"],))
    else:
        ContentOpportunityService(store).change_status(user, card["id"], status="archived")
    with pytest.raises(ProductionError, match="approved"):
        ProductionQueueService(store).create(user, card["id"])


def test_create_dedup_brief_cp4_rights_and_graph(tmp_path: Path) -> None:
    store, user, _, card = _approved(tmp_path)
    service = ProductionQueueService(store)
    first, created = service.create(user, card["id"])
    second, created_again = service.create(user, card["id"])
    assert created and not created_again and first["id"] == second["id"]
    assert first["working_title"] == "Lão tổ trở về"
    assert first["production_brief"]["audience_promise"].startswith("Theo dõi")
    assert first["production_brief"]["source_evidence_hash"] == card["evidence_hash"]
    assert first["rights_status"] == "idea_only"
    assert first["rights_gate_status"] == "research_only" and not first["rights_ready"]
    assert first["planning_ready"] and len(first["tasks"]) == 6
    assert _task(first, "SCRIPT")["status"] == "ready"
    assert _task(first, "QA")["depends_on"] == [
        "SCRIPT", "VISUAL_PLAN", "VOICE_PLAN", "THUMBNAIL", "METADATA",
    ]
    assert "no competitor download" in first["production_brief"]["visual_direction"]["reuse_restrictions"]
    with store._connect() as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO production_items (user_id,opportunity_id,status,working_title,target_format,production_brief_json,rights_status,rights_gate_status,planning_ready,rights_ready,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (user, card["id"], "queued", "duplicate", "long_form", "{}", "idea_only", "research_only", 1, 0, 1, 1),
            )


def test_evidence_only_brief_works_without_cp4_or_ollama(tmp_path: Path) -> None:
    store, user, _, card = _approved(tmp_path)
    store.update_content_opportunity(user, card["id"], {
        "ai_enrichment_status": "missing", "brain_run_id": None,
        "system_audience_promise": None, "system_core_conflict": None,
        "system_differentiation": None, "system_suggested_hook": None,
    })
    item, _ = ProductionQueueService(store).create(user, card["id"])
    assert item["production_brief"]["audience_promise"] is None
    assert item["production_brief"]["evidence_summary"]["evidence_score"] == card["evidence_score"]
    assert item["tasks"] and item["status"] == "queued"


def test_task_dependencies_progress_blocking_and_completion_are_planning_only(tmp_path: Path) -> None:
    store, user, _, card = _approved(tmp_path)
    service = ProductionQueueService(store)
    item, _ = service.create(user, card["id"])
    assert item["progress"]["percent"] == 0
    visual = _task(item, "VISUAL_PLAN")
    with pytest.raises(ProductionError, match="transition"):
        service.change_task_status(user, item["id"], visual["id"], status="in_progress")
    item = _complete(service, user, item["id"], "SCRIPT")
    assert item["status"] == "planning"
    assert all(_task(item, kind)["status"] == "ready" for kind in (
        "VISUAL_PLAN", "VOICE_PLAN", "THUMBNAIL", "METADATA",
    ))
    assert 0 < item["progress"]["percent"] < 100 and _task(item, "QA")["status"] == "pending"
    visual = _task(item, "VISUAL_PLAN")
    service.edit_task(user, item["id"], visual["id"], manual_notes="Persistent visual note")
    blocked = service.change_task_status(user, item["id"], visual["id"], status="blocked", note="assets")
    assert blocked["status"] == "blocked" and blocked["blocker_reason"] == "manual_review_requested"
    assert _task(blocked, "VISUAL_PLAN")["manual_notes"] == "Persistent visual note"
    unblocked = service.change_task_status(user, item["id"], visual["id"], status="ready")
    assert unblocked["status"] == "planning" and unblocked["blocker_reason"] is None
    for kind in ("VISUAL_PLAN", "VOICE_PLAN", "THUMBNAIL", "METADATA"):
        _complete(service, user, item["id"], kind)
    ready_qa = service.get(user, item["id"])
    assert _task(ready_qa, "QA")["status"] == "ready"
    completed = _complete(service, user, item["id"], "QA")
    assert completed["progress"]["percent"] == 100 and completed["status"] == "completed"
    with store._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0


def test_optional_thumbnail_can_be_skipped_for_short_form(tmp_path: Path) -> None:
    store, user, _, card = _approved(tmp_path, target_format="short_form")
    service = ProductionQueueService(store)
    item, _ = service.create(user, card["id"])
    item = _complete(service, user, item["id"], "SCRIPT")
    thumbnail = _task(item, "THUMBNAIL")
    assert not thumbnail["required"]
    skipped = service.change_task_status(user, item["id"], thumbnail["id"], status="skipped")
    assert _task(skipped, "THUMBNAIL")["status"] == "skipped"


def test_lifecycle_validation_and_separate_readiness(tmp_path: Path) -> None:
    store, user, _, card = _approved(tmp_path)
    service = ProductionQueueService(store)
    item, _ = service.create(user, card["id"])
    planning = service.change_status(user, item["id"], status="planning")
    ready = service.change_status(user, item["id"], status="ready")
    assert planning["planning_ready"] and ready["planning_ready"] and not ready["rights_ready"]
    in_progress = service.change_status(user, item["id"], status="in_progress")
    with pytest.raises(ProductionError, match="required planning tasks"):
        service.change_status(user, item["id"], status="completed")
    blocked = service.change_status(
        user, item["id"], status="blocked", blocker_reason="rights_review_needed"
    )
    assert blocked["blocker_reason"] == "rights_review_needed"
    with pytest.raises(ProductionError, match="Invalid"):
        service.change_status(user, item["id"], status="completed")
    assert in_progress["events"]


def test_sync_updates_brief_but_preserves_tasks_notes_priority_and_blocker(tmp_path: Path) -> None:
    store, user, _, card = _approved(tmp_path)
    service = ProductionQueueService(store)
    item, _ = service.create(user, card["id"])
    script = _task(item, "SCRIPT")
    service.edit_task(user, item["id"], script["id"], manual_notes="Keep task note")
    service.change_status(user, item["id"], status="planning")
    service.change_status(
        user, item["id"], status="blocked", blocker_reason="other", note="Keep blocker"
    )
    service.edit(user, item["id"], priority=99, manual_notes="Keep production note")
    ContentOpportunityService(store).edit(
        user, card["id"], working_title="Updated editorial title",
        selected_angle="Updated editorial angle", priority=1,
    )
    synced = service.sync(user, item["id"])
    assert synced["working_title"] == "Updated editorial title"
    assert synced["selected_angle"] == "Updated editorial angle"
    assert synced["priority"] == 99 and synced["manual_notes"] == "Keep production note"
    assert synced["status"] == "blocked" and synced["blocker_reason"] == "other"
    assert _task(synced, "SCRIPT")["manual_notes"] == "Keep task note"
    assert any(event["event_type"] == "editorial_synced" for event in synced["events"])


def test_rights_gate_change_does_not_change_source_rights(tmp_path: Path) -> None:
    store, user, _, card = _approved(tmp_path)
    service = ProductionQueueService(store)
    item, _ = service.create(user, card["id"])
    cleared = service.edit(user, item["id"], rights_gate_status="cleared")
    assert cleared["rights_ready"] and cleared["rights_status"] == "idea_only"
    assert store.get_content_opportunity(user, card["id"])["rights_status"] == "idea_only"


def test_user_isolation_and_foreign_task_access(tmp_path: Path) -> None:
    store, user, foreign, card = _approved(tmp_path)
    service = ProductionQueueService(store)
    item, _ = service.create(user, card["id"])
    with pytest.raises(ProductionError, match="not found"):
        service.create(foreign, card["id"])
    with pytest.raises(ProductionNotFound):
        service.get(foreign, item["id"])
    with pytest.raises(ProductionNotFound):
        service.edit_task(foreign, item["id"], item["tasks"][0]["id"], manual_notes="x")
    assert service.list(foreign) == []


def test_api_owner_scope_filters_feature_flag_and_fake_ownership_forbidden(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store, user, foreign, card = _approved(tmp_path)
    monkeypatch.setenv("AI_CHANNEL_AGENT_ENABLED", "true")
    with pytest.raises(ValidationError):
        ProductionCreateBody(opportunity_id=card["id"], user_id=foreign, working_title="fake")
    response = create_production_item(
        ProductionCreateBody(opportunity_id=card["id"]), user_id=user, store=store,
    )
    item_id = response["production_item"]["id"]
    rows = production_queue(
        status="queued", min_priority=0, rights="research_only", target_format="long_form",
        opportunity_id=card["id"], limit=50, user_id=user, store=store,
    )
    assert [row["id"] for row in rows] == [item_id]
    with pytest.raises(HTTPException) as exc:
        production_item_detail(item_id, user_id=foreign, store=store)
    assert exc.value.status_code == 404
    edited = edit_production_item(
        item_id, ProductionEditBody(priority=77, manual_notes="manual"),
        user_id=user, store=store,
    )
    assert edited["priority"] == 77
    monkeypatch.setenv("AI_CHANNEL_AGENT_ENABLED", "false")
    with pytest.raises(HTTPException) as disabled:
        production_queue(user_id=user, store=store)
    assert disabled.value.status_code == 404


def test_request_models_forbid_fake_state_and_task_ownership() -> None:
    with pytest.raises(ValidationError):
        ProductionEditBody(priority=1, opportunity_rank_score=100)
    with pytest.raises(ValidationError):
        ProductionStatusBody(status="ready", user_id=1)
    with pytest.raises(ValidationError):
        ProductionTaskEditBody(manual_notes="x", user_id=1)
    with pytest.raises(ValidationError):
        ProductionTaskStatusBody(status="completed", production_item_id=1)
