from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sqlite3

import pytest
from pydantic import ValidationError

from tests.test_channel_agent_opportunities import evidence_store
from tests.test_cp8_publishing import setup_publish
from tests.test_cp7b_production_render import prepared_item
from universal_video_ai.channel_agent.automation import (
    AutomationError, AutomationNotFound, AutomationOrchestrator,
)
from universal_video_ai.channel_agent.facebook_publishing import (
    FacebookPublishingError, FacebookPublishingNotFound,
    FacebookPublishingService, MetaGraphPublisher,
)
from universal_video_ai.channel_agent.opportunities import ContentOpportunityService
from universal_video_ai.channel_agent.social_publishing import PublishingCapability
from universal_video_ai.web.auth import get_current_user_id
from universal_video_ai.web.channel_agent_router import (
    AutomationStartBody, FacebookPublishingBody, router,
)
from universal_video_ai.web.oauth import FacebookOAuth
from universal_video_ai.web.store import Store


class StubMeta:
    def __init__(self, *, published=True, schedule=True):
        self.uploads = 0
        self.published = published
        self.schedule_supported = schedule

    def capabilities(self, user_id, verify=False):
        return PublishingCapability(
            True, True, self.schedule_supported, page_video_supported=True,
            reels_supported=False, reasons=("Reels not verified",),
        )

    def upload(self, user_id, job):
        self.uploads += 1
        assert job["platform"] == "facebook"
        return {"id": "fb-video-1"}

    def status(self, user_id, external_id):
        assert external_id == "fb-video-1"
        return {"id": external_id, "published": self.published,
                "permalink_url": "https://facebook.test/fb-video-1",
                "status": {"video_status": "ready" if self.published else "processing"}}


def facebook_setup(tmp_path, *, clear_rights=True, publisher=None):
    store, owner, foreign, item, render, video = setup_publish(tmp_path)
    if clear_rights:
        with store._connect() as conn:
            conn.execute(
                "UPDATE production_items SET rights_gate_status='cleared',rights_ready=1 WHERE id=?",
                (item,),
            )
    store.upsert_social_account(
        owner, "facebook", "page-secret", None, None,
        "Owned Page", "page-123", FacebookOAuth.SCOPE,
    )
    service = FacebookPublishingService(store, publisher=publisher or StubMeta())
    return store, owner, foreign, item, render, video, service


def test_facebook_dry_run_is_owner_scoped_and_persists_adaptation(tmp_path):
    store, owner, foreign, item, render, video, service = facebook_setup(tmp_path)
    job = service.submit(owner, item, render_job_id=render)
    assert job["platform"] == "facebook" and job["status"] == "draft" and job["dry_run"]
    assert job["package"]["video_path"] == str(video.resolve())
    assert job["package"]["adapted_metadata"]["deterministic"] is True
    assert job["package"]["target_page"] == {"id": "page-123", "name": "Owned Page"}
    destination = store.get_publishing_destination(owner, job["id"])
    assert destination["destination_type"] == "page_video"
    assert destination["adapted_metadata"]["source"] == "approved_metadata_package"
    assert "page-secret" not in str(job) + str(destination)
    with pytest.raises(FacebookPublishingNotFound):
        service.get(foreign, item, job["id"])
    assert store.get_publishing_destination(foreign, job["id"]) is None


def test_facebook_rights_confirmation_reels_and_schedule_guards(tmp_path):
    _, owner, _, item, render, _, blocked = facebook_setup(tmp_path, clear_rights=False)
    with pytest.raises(FacebookPublishingError, match="rights gate"):
        blocked.submit(owner, item, render_job_id=render)
    _, owner, _, item, render, _, service = facebook_setup(tmp_path / "reels")
    with pytest.raises(FacebookPublishingError, match="Reels capability"):
        service.submit(owner, item, render_job_id=render, destination_type="reel")
    with pytest.raises(FacebookPublishingError, match="explicit Facebook publish"):
        service.submit(owner, item, render_job_id=render, dry_run=False)
    _, owner, _, item, render, _, unsupported = facebook_setup(
        tmp_path / "schedule", publisher=StubMeta(schedule=False)
    )
    with pytest.raises(FacebookPublishingError, match="scheduling is unavailable"):
        unsupported.submit(
            owner, item, render_job_id=render,
            schedule_local="2030-01-02T07:00", schedule_timezone="Asia/Bangkok",
        )


def test_facebook_upload_status_idempotency_and_result(tmp_path):
    publisher = StubMeta(published=True)
    store, owner, _, item, render, _, service = facebook_setup(tmp_path, publisher=publisher)
    job = service.submit(
        owner, item, render_job_id=render, dry_run=False, confirm_publish=True
    )
    result = service.run(owner, item, job["id"])
    assert result["status"] == "published"
    assert result["external_video_id"] == "fb-video-1"
    assert service.run(owner, item, job["id"])["status"] == "published"
    assert publisher.uploads == 1
    stored = store.get_publishing_result(owner, job["id"])
    assert stored["status"] == "published" and stored["external_id"] == "fb-video-1"


def test_facebook_scheduled_remote_state_is_not_marked_published(tmp_path):
    publisher = StubMeta(published=False)
    _, owner, _, item, render, _, service = facebook_setup(tmp_path, publisher=publisher)
    service.now = lambda: datetime(2029, 1, 1, tzinfo=timezone.utc)
    job = service.submit(
        owner, item, render_job_id=render,
        schedule_local="2030-01-02T07:00", schedule_timezone="Asia/Bangkok",
        dry_run=False, confirm_publish=True,
    )
    result = service.run(owner, item, job["id"])
    assert result["status"] == "scheduled" and publisher.uploads == 1


class Response:
    def __init__(self, code, payload):
        self.status_code, self._payload = code, payload

    def json(self):
        return self._payload


class MetaHTTP:
    def __init__(self):
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        if url.endswith("/me/accounts"):
            return Response(200, {"data": [
                {"id": "page-123", "name": "Owned", "access_token": "page-secret", "tasks": ["CREATE_CONTENT"]},
                {"id": "page-456", "name": "Second Page", "access_token": "second-secret", "tasks": ["CREATE_CONTENT"]},
            ]})
        if url.endswith("page-123"):
            return Response(200, {"id": "page-123", "name": "Owned", "tasks": ["CREATE_CONTENT"]})
        return Response(200, {"id": "remote", "published": False, "status": {"video_status": "processing"}})

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return Response(200, {"id": "remote"})


def test_official_meta_boundary_uses_header_and_never_query_token(tmp_path):
    store, owner, _, item, render, _, _ = facebook_setup(tmp_path)
    http = MetaHTTP()
    provider = MetaGraphPublisher(store, http=http)
    assert provider.capabilities(owner, verify=True).page_video_supported
    service = FacebookPublishingService(store, publisher=provider)
    job = service.submit(owner, item, render_job_id=render, dry_run=False, confirm_publish=True)
    provider.upload(owner, store.get_production_publishing_job(owner, item, job["id"], private=True))
    for _, _, kwargs in http.calls:
        assert kwargs["headers"]["Authorization"] == "Bearer page-secret"
        assert "access_token" not in (kwargs.get("params") or {})


def test_owner_can_list_and_select_managed_page_without_token_leak(tmp_path):
    store, owner, foreign, _, _, _, _ = facebook_setup(tmp_path)
    store.upsert_social_account(
        owner, "facebook", "page-secret", "user-secret", None,
        "Owned", "page-123", FacebookOAuth.SCOPE,
    )
    provider = MetaGraphPublisher(store, http=MetaHTTP())
    pages = provider.pages(owner)
    assert [page["page_id"] for page in pages] == ["page-123", "page-456"]
    assert "secret" not in str(pages)
    selected = provider.select_page(owner, "page-456")
    assert selected == {"page_id": "page-456", "page_name": "Second Page", "selected": True}
    account = store.get_social_account(owner, "facebook")
    assert account["account_ref"] == "page-456" and account["access_token"] == "second-secret"
    with pytest.raises(FacebookPublishingError):
        provider.select_page(foreign, "page-456")


def test_facebook_connection_reports_capabilities_without_token(tmp_path):
    _, owner, _, _, _, _, service = facebook_setup(tmp_path)
    status = service.connection(owner)
    assert status["page_id"] == "page-123" and status["page_video_supported"]
    assert status["reels_supported"] is False
    assert "page-secret" not in str(status)


def test_shared_render_has_independent_youtube_and_facebook_jobs(tmp_path):
    store, owner, _, item, render, _, service = facebook_setup(tmp_path)
    facebook = service.submit(owner, item, render_job_id=render)
    from universal_video_ai.channel_agent.production_publishing import ProductionPublishingService
    youtube = ProductionPublishingService(store).submit(owner, item, render_job_id=render)
    assert facebook["render_job_id"] == youtube["render_job_id"] == render
    assert facebook["id"] != youtube["id"]
    assert {row["platform"] for row in store.list_production_publishing_jobs(owner, item)} == {"facebook", "youtube"}
    service.cancel(owner, item, facebook["id"])
    assert store.get_production_publishing_job(owner, item, youtube["id"])["status"] == "draft"
    from universal_video_ai.channel_agent.feedback_learning import (
        FeedbackLearningNotFound, FeedbackLearningService,
    )
    with pytest.raises(FeedbackLearningNotFound):
        FeedbackLearningService(store).snapshots(owner, item, facebook["id"])


def test_automation_run_and_steps_persist_across_restart(tmp_path):
    path = tmp_path / "automation.sqlite3"
    store = Store(path)
    owner = store.create_user("owner", "x")
    service = AutomationOrchestrator(store, handlers={
        "research": lambda user, config, refs: {"trend_scan_id": "scan-1"}
    })
    run, created = service.start(owner, mode="MANUAL_STEP", configuration={
        "target_platforms": [], "research_refresh": True, "competitor_refresh": False,
    }, idempotency_key="same")
    assert created and len(run["steps"]) == 10
    same, created = service.start(owner, mode="MANUAL_STEP", configuration={
        "target_platforms": [], "research_refresh": True, "competitor_refresh": False,
    }, idempotency_key="same")
    assert not created and same["id"] == run["id"]
    advanced = service.advance(owner, run["id"])
    assert advanced["steps"][0]["status"] == "completed"
    restarted = AutomationOrchestrator(Store(path))
    assert restarted.get(owner, run["id"])["refs"]["trend_scan_id"] == "scan-1"


def test_restart_recovers_running_step_without_duplicate_completed_work(tmp_path):
    store = Store(tmp_path / "restart.sqlite3")
    owner = store.create_user("owner", "x")
    calls = {"count": 0}
    def handler(user, config, refs):
        calls["count"] += 1
        return {"trend_scan_id": "recovered"}
    service = AutomationOrchestrator(store, handlers={"research": handler})
    run, _ = service.start(owner, mode="MANUAL_STEP", configuration={
        "target_platforms": [], "research_refresh": True, "competitor_refresh": False,
    })
    store.update_automation_step(owner, run["id"], "research", {"status": "running"})
    recovered = AutomationOrchestrator(Store(store.db_path), handlers={"research": handler})
    result = recovered.resume(owner, run["id"])
    assert result["steps"][0]["status"] == "completed" and calls["count"] == 1
    recovered.advance(owner, run["id"])
    assert calls["count"] == 1


def test_automation_with_gates_reuses_production_item(tmp_path):
    store, owner, _, candidate = evidence_store(tmp_path)
    opportunities = ContentOpportunityService(store)
    opportunity, _ = opportunities.create(owner, source_type="candidate", source_id=str(candidate))
    opportunities.change_status(owner, opportunity["id"], status="approved")
    handlers = {
        "research": lambda user, config, refs: {"trend_scan_id": "scan"},
        "competitor": lambda user, config, refs: {"competitor_result": {}},
        "opportunity": lambda user, config, refs: {"opportunity_id": opportunity["id"]},
    }
    service = AutomationOrchestrator(store, handlers=handlers)
    run, _ = service.start(owner, mode="AUTOMATION_WITH_GATES", configuration={
        "target_platforms": [], "research_refresh": True, "competitor_refresh": True,
    })
    waiting = service.advance(owner, run["id"])
    assert waiting["status"] == "waiting_approval"
    assert "opportunity approval" in waiting["waiting_reason"]
    service.approve(owner, run["id"], "opportunity", refs={"opportunity_id": opportunity["id"]})
    after = service.advance(owner, run["id"])
    item_id = after["refs"]["production_item_id"]
    assert after["status"] == "waiting_approval" and after["current_stage"] == "script"
    assert store.get_production_item_by_opportunity(owner, opportunity["id"])["id"] == item_id
    service.resume(owner, run["id"])
    assert store.get_production_item_by_opportunity(owner, opportunity["id"])["id"] == item_id


def test_automation_failure_retry_pause_cancel_and_boundaries(tmp_path):
    store = Store(tmp_path / "lifecycle.sqlite3")
    owner = store.create_user("owner", "x")
    calls = {"count": 0}
    def flaky(user, config, refs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("exact failure")
        return {"trend_scan_id": "ok"}
    service = AutomationOrchestrator(store, handlers={"research": flaky})
    run, _ = service.start(owner, mode="MANUAL_STEP", configuration={
        "target_platforms": [], "research_refresh": True, "competitor_refresh": False,
    })
    failed = service.advance(owner, run["id"])
    assert failed["status"] == "failed" and failed["error"] == "exact failure"
    with pytest.raises(AutomationError, match="bounded step retry"):
        service.advance(owner, run["id"])
    retried = service.retry(owner, run["id"])
    assert retried["steps"][0]["retry_count"] == 1 and calls["count"] == 2
    paused = service.pause(owner, run["id"])
    assert paused["status"] == "paused"
    cancelled = service.cancel(owner, run["id"])
    assert cancelled["status"] == "cancelled"
    with pytest.raises(AutomationError):
        service.resume(owner, run["id"])


@pytest.mark.parametrize("mode", ["MANUAL_STEP", "ASSISTED"])
def test_manual_and_assisted_modes_advance_one_executed_stage(tmp_path, mode):
    store = Store(tmp_path / (mode + ".sqlite3"))
    owner = store.create_user(mode, "x")
    handlers = {
        "research": lambda user, config, refs: {"research": True},
        "competitor": lambda user, config, refs: {"competitor": True},
    }
    service = AutomationOrchestrator(store, handlers=handlers)
    run, _ = service.start(owner, mode=mode, configuration={
        "target_platforms": [], "research_refresh": True, "competitor_refresh": True,
    })
    result = service.advance(owner, run["id"])
    assert result["steps"][0]["status"] == "completed"
    assert result["steps"][1]["status"] == "pending"


def test_all_asset_publish_gates_render_reuse_and_partial_destination_result(tmp_path):
    store, owner, _, item, render_id, _ = setup_publish(tmp_path)
    calls = {"render": 0, "publish": 0}
    def render_handler(user, config, refs):
        calls["render"] += 1
        raise AssertionError("completed render must be reused")
    def publish_handler(user, config, refs):
        calls["publish"] += 1
        return {"publishing_jobs": {
            "youtube": {"job_id": 101, "status": "published"},
            "facebook": {"job_id": 102, "status": "failed", "error": "Meta unavailable"},
        }, "partial_success": True}
    service = AutomationOrchestrator(store, handlers={
        "render": render_handler, "publish": publish_handler,
    })
    run, _ = service.start(owner, mode="AUTOMATION_WITH_GATES", configuration={
        "target_platforms": ["youtube", "facebook"], "research_refresh": False,
        "competitor_refresh": False, "production_item_id": item,
    })
    refs = {"production_item_id": item}
    store.update_automation_run(owner, run["id"], {"refs": refs})
    for stage in ("research", "competitor", "opportunity", "production"):
        store.update_automation_step(owner, run["id"], stage, {
            "status": "completed", "completed_at": 1,
        })
    store.add_automation_approval(owner, run["id"], "opportunity", "approved")
    first = service.advance(owner, run["id"])
    assert first["status"] == "waiting_approval" and first["current_stage"] == "script"
    service.approve(owner, run["id"], "script")
    second = service.advance(owner, run["id"])
    assert second["status"] == "waiting_approval" and second["current_stage"] == "assets"
    service.approve(owner, run["id"], "assets")
    third = service.advance(owner, run["id"])
    assert third["status"] == "waiting_approval" and third["current_stage"] == "render"
    assert third["refs"]["render_job_id"] == render_id and calls["render"] == 0
    service.approve(owner, run["id"], "publish")
    finished = service.advance(owner, run["id"])
    assert calls["publish"] == 1 and finished["status"] == "completed"
    assert finished["refs"]["partial_success"] is True
    assert finished["refs"]["publishing_jobs"]["youtube"]["status"] == "published"
    assert finished["refs"]["publishing_jobs"]["facebook"]["status"] == "failed"


def test_automation_owner_isolation_modes_and_secret_guard(tmp_path):
    store = Store(tmp_path / "security.sqlite3")
    owner = store.create_user("owner", "x")
    foreign = store.create_user("foreign", "x")
    service = AutomationOrchestrator(store)
    run, _ = service.start(owner, mode="ASSISTED", configuration={"target_platforms": []})
    with pytest.raises(AutomationNotFound):
        service.get(foreign, run["id"])
    with pytest.raises(AutomationError):
        service.start(owner, mode="FULL_AUTONOMOUS", configuration={"target_platforms": []})
    with pytest.raises(AutomationError, match="credentials"):
        service.start(owner, mode="MANUAL_STEP", configuration={
            "target_platforms": [], "nested": {"access_token": "secret"},
        })
    with pytest.raises(AutomationError, match="references"):
        service.approve(owner, run["id"], "opportunity", refs={"token": "secret"})
    assert "FULL_AUTONOMOUS" not in {run["mode"] for run in service.list(owner)}


def test_automation_routes_auth_models_and_ui_contract():
    routes = [route for route in router.routes
              if "automation" in route.path or "publishing/capabilities" in route.path
              or route.path.endswith("publishing-jobs/facebook")]
    assert routes
    for route in routes:
        assert any(dep.call is get_current_user_id for dep in route.dependant.dependencies)
    with pytest.raises(ValidationError):
        AutomationStartBody(mode="MANUAL_STEP", configuration={}, user_id=999)
    with pytest.raises(ValidationError):
        FacebookPublishingBody(render_job_id=1, user_id=999)
    root = Path(__file__).parents[1]
    html = (root / "src/universal_video_ai/web/static/index.html").read_text(encoding="utf-8")
    js = (root / "src/universal_video_ai/web/static/app.js").read_text(encoding="utf-8")
    for text in ("Automation", "AUTOMATION_WITH_GATES", "publishing-capabilities"):
        assert text in html
    for text in ("automationStageGraph", "Approve current gate", "Open Production Item"):
        assert text in js


def test_cp10_schema_is_additive_and_repeated_initialization_safe(tmp_path):
    path = tmp_path / "repeat.sqlite3"
    # Simulate the pre-CP10 boundary: existing application and CP8 publishing
    # tables contain data, while CP10 tables do not exist yet.
    with sqlite3.connect(path) as conn:
        conn.executescript("""
            CREATE TABLE legacy_marker (id INTEGER PRIMARY KEY, value TEXT);
            INSERT INTO legacy_marker (value) VALUES ('preserve-me');
        """)
    Store(path)
    Store(path)
    with Store(path)._connect() as conn:
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        marker = conn.execute("SELECT value FROM legacy_marker WHERE id=1").fetchone()[0]
    assert {"publishing_destinations", "publishing_results", "automation_runs",
            "automation_steps", "automation_approval_events"}.issubset(tables)
    assert marker == "preserve-me"
