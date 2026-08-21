from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from universal_video_ai.channel_agent.execution import (
    ProductionExecutionError,
    ProductionExecutionNotFound,
    ProductionExecutionService,
    ProductionGenerationBusy,
    ProductionInvalidResponse,
)
from universal_video_ai.channel_agent.opportunities import ContentOpportunityService
from universal_video_ai.channel_agent.production import ProductionQueueService
from universal_video_ai.channel_agent.providers import OllamaProviderError
from universal_video_ai.web.store import Store
from universal_video_ai.web.channel_agent_router import (
    ProductionAssetEditBody,
    ProductionAssetStatusBody,
    production_asset_detail,
    production_assets,
)


class FakeProvider:
    name = "ollama"
    model = "fake-local"

    def __init__(self, responses: list[str] | None = None) -> None:
        self.responses = list(responses or [])
        self.calls: list[dict[str, Any]] = []

    def generate_structured(self, **kwargs: Any) -> str:
        self.calls.append(kwargs)
        if self.responses:
            return self.responses.pop(0)
        prompt = kwargs["user_prompt"]
        if "ASSET_TYPE: script_blueprint" in prompt:
            return json.dumps(blueprint_json(), ensure_ascii=False)
        if "ASSET_TYPE: script_section" in prompt:
            return json.dumps(section_json(), ensure_ascii=False)
        if "ASSET_TYPE: visual_plan" in prompt:
            return json.dumps(visual_json(), ensure_ascii=False)
        if "ASSET_TYPE: voice_plan" in prompt:
            return json.dumps(voice_json(), ensure_ascii=False)
        if "ASSET_TYPE: thumbnail_brief" in prompt:
            return json.dumps(thumbnail_json(), ensure_ascii=False)
        return json.dumps(metadata_json(), ensure_ascii=False)


class OfflineProvider(FakeProvider):
    def generate_structured(self, **kwargs: Any) -> str:
        raise OllamaProviderError("Ollama is not running. Start Ollama and try again.")


def blueprint_json(*, evidence_refs: list[str] | None = None) -> dict[str, Any]:
    return {
        "working_title": "Gia tộc trong bảy ngày cuối",
        "core_promise": "Theo dõi một lựa chọn có hậu quả qua nhiều thế hệ",
        "narrative_angle": "Góc nhìn tiến trình gia tộc Việt nguyên bản",
        "audience": "Khán giả Việt thích truyện dài kỳ",
        "tone": "Lôi cuốn, rõ ràng và có chiều sâu",
        "opening_hook": "Gia tộc chỉ còn bảy ngày để tồn tại.",
        "sections": [{
            "section_id": f"sec_{index:02d}", "title": f"Phần {index}",
            "purpose": "Phát triển câu chuyện", "conflict": "Lựa chọn khó khăn",
            "development": "Diễn biến mới và nguyên bản", "relative_weight": 1,
            "evidence_refs": evidence_refs or [],
        } for index in range(1, 5)],
        "ending_strategy": "Khép lại xung đột chính bằng lựa chọn của gia tộc",
        "open_loop": "Một hậu quả mới được hé lộ",
        "originality_notes": ["Cấu trúc và câu chữ hoàn toàn mới"],
        "rights_constraints": ["Nguồn bên thứ ba chỉ là nghiên cứu"],
    }


def section_json(text: str | None = None) -> dict[str, Any]:
    return {
        "section_text": text or " ".join(["Nội dung tiếng Việt nguyên bản có diễn biến riêng."] * 30),
        "section_summary": "Gia tộc đưa ra một lựa chọn làm thay đổi xung đột.",
        "continuity_notes": ["Giữ nhất quán tên gọi gia tộc"],
    }


def visual_json(
    strategy: str = "original_generation", *, rights_status: str = "planned",
) -> dict[str, Any]:
    return {
        "scenes": [{
            "scene_id": "scene_01", "script_section_id": "sec_01",
            "purpose": "Thiết lập xung đột", "visual_type": "illustration",
            "visual_description": "Minh họa nguyên bản về gia tộc",
            "approximate_duration_seconds": 30, "acquisition_strategy": strategy,
            "rights_requirement": "Phải do người dùng sở hữu hoặc được cấp phép",
            "rights_status": rights_status,
        }],
        "visual_rhythm": "Thay đổi theo nhịp kể",
        "originality_notes": ["Không dùng video nguồn"],
    }


def voice_json() -> dict[str, Any]:
    return {
        "language": "vi", "voice_style": "Kể chuyện trầm ấm", "tone": "Rõ ràng",
        "pace": "Vừa", "energy": "Tăng dần", "pronunciation_notes": [],
        "character_name_pronunciations": [], "pause_guidance": "Ngắt theo ý",
        "chapter_break_guidance": "Dừng ngắn giữa chương",
    }


def thumbnail_json(*, concept: str = "Lão tổ trở lại") -> dict[str, Any]:
    return {
        "primary_concept": concept, "focal_subject": "Lão tổ",
        "background": "Gia tộc suy tàn", "emotional_tension": "Bảy ngày cuối",
        "short_text_options": ["LÃO TỔ TRỞ LẠI", "7 NGÀY CUỐI"],
        "composition_notes": "Một chủ thể chính, tương phản rõ",
        "avoid_list": ["Không gây hiểu lầm"],
        "evidence_rationale": "Mô-típ gia tộc được quan sát trong nghiên cứu",
    }


def metadata_json() -> dict[str, Any]:
    return {
        "primary_title": "Gia tộc chỉ còn bảy ngày trước khi lão tổ trở lại",
        "alternate_titles": ["Khi lão tổ buộc phải xuất thế"],
        "description_draft": "Một câu chuyện Việt nguyên bản về lựa chọn và hậu quả.",
        "primary_keyword": "gia tộc tu tiên", "secondary_terms": ["lão tổ"],
        "chapter_titles": ["Bảy ngày cuối", "Lựa chọn"],
        "pinned_comment_draft": "Bạn sẽ chọn bảo vệ điều gì?", "hashtags": ["#giatoc"],
    }


def setup_item(tmp_path: Path) -> tuple[Store, int, int, dict[str, Any]]:
    store = Store(tmp_path / "cp7a.sqlite3")
    user = store.create_user("owner", "x")
    foreign = store.create_user("foreign", "x")
    candidate = store.upsert_trend_candidate(user, {
        "scan_id": "cp7a", "source_id": "video", "source_url": "https://youtube.test/video",
        "title": "家族老祖", "channel_id": "UC", "channel_title": "Research",
        "captured_at": 1.0, "view_count": 10_000,
        "published_at": "2026-08-20T00:00:00Z",
    }, "idea_only")
    store.update_trend_candidate_score(
        user, candidate, observed_vph=100, approx_vph=50, engagement_rate=.05,
        outlier_ratio=5, trend_score=.8, niche_relevance_score=.9,
        opportunity_score=.75, relevance_status="relevant", score_confidence="high",
        available_signal_count=5, match_reason_json='["家族"]',
    )
    opportunities = ContentOpportunityService(store)
    card, _ = opportunities.create(user, source_type="candidate", source_id=str(candidate))
    opportunities.edit(
        user, card["id"], working_title="Lão tổ trở lại", selected_angle="Gia tộc suy vong",
        priority=80, target_format="long_form", target_duration_min=8,
        target_duration_max=8,
    )
    card = opportunities.change_status(user, card["id"], status="approved")
    item, _ = ProductionQueueService(store).create(user, card["id"])
    return store, user, foreign, item


def generate_sections(service: ProductionExecutionService, user: int, item_id: int) -> list[dict[str, Any]]:
    blueprint = service.generate_blueprint(user, item_id)
    return [
        service.generate_section(user, item_id, section["section_id"])
        for section in blueprint["content"]["sections"]
    ]


def approve_script(service: ProductionExecutionService, user: int, item_id: int) -> dict[str, Any]:
    generate_sections(service, user, item_id)
    draft = service.assemble_script(user, item_id)
    return service.change_asset_status(user, draft["id"], status="approved")


def test_fresh_legacy_and_idempotent_migration(tmp_path: Path) -> None:
    store = Store(tmp_path / "fresh.sqlite3")
    with store._connect() as conn:
        names = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        job_columns = {row[1] for row in conn.execute(
            "PRAGMA table_info(production_generation_jobs)"
        ).fetchall()}
    assert {"production_assets", "production_asset_events", "production_generation_jobs"} <= names
    assert "failure_stage" in job_columns
    legacy = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(legacy) as conn:
        conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT UNIQUE, password_hash TEXT, created_at REAL)")
    Store(legacy)
    Store(legacy)
    with sqlite3.connect(legacy) as conn:
        assert conn.execute("SELECT COUNT(*) FROM production_assets").fetchone()[0] == 0


def test_blueprint_persisted_budgeted_and_invalid_evidence_is_security_failure(tmp_path: Path) -> None:
    store, user, _, item = setup_item(tmp_path)
    provider = FakeProvider()
    service = ProductionExecutionService(store, provider, words_per_minute=150)
    asset = service.generate_blueprint(user, item["id"])
    assert asset["asset_type"] == "script_blueprint" and asset["version"] == 1
    assert asset["content"]["target_total_words"] == 1200
    assert sum(row["target_word_budget"] for row in asset["content"]["sections"]) == 1200
    assert all(row["target_duration_minutes"] > 0 for row in asset["content"]["sections"])
    bad = FakeProvider([json.dumps(blueprint_json(evidence_refs=["video:invented"]))])
    with pytest.raises(ProductionInvalidResponse, match="evidence"):
        ProductionExecutionService(store, bad).generate_blueprint(user, item["id"])
    assert len(bad.calls) == 1


def test_structured_normalization_extra_fields_and_exactly_one_repair(tmp_path: Path) -> None:
    store, user, _, item = setup_item(tmp_path)
    fenced = "```json\n" + json.dumps({**blueprint_json(), "extra": "ignored"}, ensure_ascii=False) + "\n```"
    service = ProductionExecutionService(store, FakeProvider([fenced]))
    assert service.generate_blueprint(user, item["id"])["content"]["working_title"]
    repaired = FakeProvider(["not json", json.dumps(blueprint_json(), ensure_ascii=False)])
    assert ProductionExecutionService(store, repaired).generate_blueprint(user, item["id"])["version"] == 2
    assert len(repaired.calls) == 2
    failed = FakeProvider(["bad", "still bad"])
    with pytest.raises(ProductionInvalidResponse):
        ProductionExecutionService(store, failed).generate_blueprint(user, item["id"])
    assert len(failed.calls) == 2


def test_sections_independent_stable_resume_and_approved_not_overwritten(tmp_path: Path) -> None:
    store, user, _, item = setup_item(tmp_path)
    service = ProductionExecutionService(store, FakeProvider())
    blueprint = service.generate_blueprint(user, item["id"])
    first_plan = blueprint["content"]["sections"][0]
    first = service.generate_section(user, item["id"], first_plan["section_id"])
    service.change_asset_status(user, first["id"], status="approved")
    missing = service._missing_sections(user, item["id"], blueprint)
    assert missing == ["sec_02", "sec_03", "sec_04"]
    regenerated = service.generate_section(user, item["id"], "sec_01")
    assert regenerated["version"] == 2
    assert service.store.latest_production_asset(user, item["id"], "script_section:sec_01", status="approved")["id"] == first["id"]
    for section_id in missing:
        service.generate_section(user, item["id"], section_id)
    assert service._missing_sections(user, item["id"], blueprint) == []
    ordered = [row["section_id"] for row in service.assemble_script(user, item["id"])["content"]["sections"]]
    assert ordered == ["sec_01", "sec_02", "sec_03", "sec_04"]


def test_manual_edit_versions_preserve_old_and_single_approved_selection(tmp_path: Path) -> None:
    store, user, _, item = setup_item(tmp_path)
    service = ProductionExecutionService(store, FakeProvider())
    first = generate_sections(service, user, item["id"])[0]
    service.change_asset_status(user, first["id"], status="approved")
    second = service.manual_version(user, first["id"], content_text=" ".join(["Bản sửa thủ công nguyên bản."] * 30), manual_notes="human")
    third = service.manual_version(user, second["id"], content_text=" ".join(["Bản sửa thứ ba nguyên bản."] * 30))
    assert [row["version"] for row in service.versions(user, third["id"])] == [3, 2, 1]
    assert service.store.get_production_asset(user, first["id"])["status"] == "approved"
    service.change_asset_status(user, third["id"], status="approved")
    assert service.store.get_production_asset(user, first["id"])["status"] == "superseded"
    assert service.store.get_production_asset(user, third["id"])["status"] == "approved"


def test_script_task_only_completes_after_approved_draft(tmp_path: Path) -> None:
    store, user, _, item = setup_item(tmp_path)
    service = ProductionExecutionService(store, FakeProvider())
    generate_sections(service, user, item["id"])
    draft = service.assemble_script(user, item["id"])
    queue = ProductionQueueService(store)
    assert next(t for t in queue.get(user, item["id"])["tasks"] if t["task_type"] == "SCRIPT")["status"] == "ready"
    service.change_asset_status(user, draft["id"], status="review")
    assert next(t for t in queue.get(user, item["id"])["tasks"] if t["task_type"] == "SCRIPT")["status"] == "ready"
    service.change_asset_status(user, draft["id"], status="approved")
    tasks = {t["task_type"]: t["status"] for t in queue.get(user, item["id"])["tasks"]}
    assert tasks["SCRIPT"] == "completed"
    assert all(tasks[kind] == "ready" for kind in ("VISUAL_PLAN", "VOICE_PLAN", "THUMBNAIL", "METADATA"))


def test_word_count_duration_and_tolerance_metadata(tmp_path: Path) -> None:
    store, user, _, item = setup_item(tmp_path)
    service = ProductionExecutionService(store, FakeProvider(), words_per_minute=100)
    blueprint = service.generate_blueprint(user, item["id"])
    section = service.generate_section(user, item["id"], "sec_01")
    words = section["content"]["word_count"]
    assert section["content"]["estimated_duration_minutes"] == pytest.approx(words / 100, abs=.01)
    assert isinstance(section["generation_metadata"]["within_word_budget_tolerance"], bool)
    assert blueprint["content"]["narration_words_per_minute"] == 100
    repair = FakeProvider([
        json.dumps(section_json("Đoạn quá ngắn. " * 20), ensure_ascii=False),
        json.dumps(section_json(), ensure_ascii=False),
    ])
    repaired = ProductionExecutionService(store, repair, words_per_minute=100).generate_section(
        user, item["id"], "sec_02"
    )
    assert repaired["generation_metadata"]["attempt_count"] == 2
    assert len(repair.calls) == 2


def test_visual_rights_voice_thumbnail_and_metadata_validation(tmp_path: Path) -> None:
    store, user, _, item = setup_item(tmp_path)
    service = ProductionExecutionService(store, FakeProvider())
    approve_script(service, user, item["id"])
    visual = service.generate_package_asset(user, item["id"], "visual_plan")
    assert visual["content"]["scenes"][0]["rights_status"] == "planned"
    assert not visual["content"]["rights_cleared"]
    model_claimed_clear = ProductionExecutionService(
        store, FakeProvider([json.dumps(visual_json(rights_status="cleared"))])
    ).generate_package_asset(user, item["id"], "visual_plan")
    assert model_claimed_clear["content"]["scenes"][0]["rights_status"] == "needs_review"
    assert not model_claimed_clear["content"]["rights_cleared"]
    voice = service.generate_package_asset(user, item["id"], "voice_plan")
    assert voice["content"]["language"] == "vi"
    thumb = service.generate_package_asset(user, item["id"], "thumbnail_brief")
    assert 2 <= len(thumb["content"]["short_text_options"]) <= 5
    metadata = service.generate_package_asset(user, item["id"], "metadata_package")
    assert metadata["content"]["primary_title"] and len(metadata["content"]["alternate_titles"]) <= 5
    forbidden = FakeProvider([json.dumps(visual_json("download_competitor")), json.dumps(visual_json("download_competitor"))])
    with pytest.raises(ProductionInvalidResponse):
        ProductionExecutionService(store, forbidden).generate_package_asset(user, item["id"], "visual_plan")
    assert len(forbidden.calls) == 2
    guarantee = thumbnail_json(concept="Guaranteed viral")
    unsafe = FakeProvider([json.dumps(guarantee)])
    with pytest.raises(ProductionInvalidResponse, match="policy"):
        ProductionExecutionService(store, unsafe).generate_package_asset(user, item["id"], "thumbnail_brief")
    assert len(unsafe.calls) == 1


def test_visual_plan_normalizes_harmless_local_model_shape_drift(tmp_path: Path) -> None:
    store, user, _, item = setup_item(tmp_path)
    base = ProductionExecutionService(store, FakeProvider())
    approve_script(base, user, item["id"])
    drifted = {
        "scenes": {"first": {
            "section_id": "sec_01", "purpose": "Thiết lập",
            "visual_type": "illustration", "description": "Minh họa nguyên bản",
            "approximate_duration": 30.0, "acquisition": "ai_generated",
            "rights_requirements": "Tài sản phải được sở hữu hoặc cấp phép",
            "rights_status": "planned",
        }}
    }
    asset = ProductionExecutionService(
        store, FakeProvider([json.dumps(drifted, ensure_ascii=False)])
    ).generate_package_asset(user, item["id"], "visual_plan")
    scene = asset["content"]["scenes"][0]
    assert scene["scene_id"] == "scene_01"
    assert scene["script_section_id"] == "sec_01"
    assert scene["approximate_duration_seconds"] == 30
    assert scene["acquisition_strategy"] == "original_generation"
    assert asset["content"]["originality_notes"]


def test_all_asset_approvals_qa_and_rights_separation(tmp_path: Path) -> None:
    store, user, _, item = setup_item(tmp_path)
    service = ProductionExecutionService(store, FakeProvider())
    approve_script(service, user, item["id"])
    for kind in ("visual_plan", "voice_plan", "thumbnail_brief", "metadata_package"):
        asset = service.generate_package_asset(user, item["id"], kind)
        service.change_asset_status(user, asset["id"], status="approved")
    before_qa = service.asset_package(user, item["id"])
    assert not before_qa["asset_ready"] and not before_qa["rights_ready"]
    qa = service.run_qa(user, item["id"])
    assert qa["content"]["passed"] and not qa["content"]["renders_or_publishes"]
    service.change_asset_status(user, qa["id"], status="approved")
    package = service.asset_package(user, item["id"])
    assert package["asset_ready"] and package["qa_status"] == "approved"
    assert not package["rights_ready"] and package["rights_gate_status"] == "research_only"
    assert ProductionQueueService(store).get(user, item["id"])["status"] == "completed"
    with store._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0


def test_generation_jobs_progress_resume_cancel_and_single_concurrency(tmp_path: Path) -> None:
    store, user, _, item = setup_item(tmp_path)
    service = ProductionExecutionService(store, FakeProvider())
    service.generate_blueprint(user, item["id"])
    job = service.start_job(user, item["id"], job_type="script_resume", asset_type="script_section")
    deadline = time.time() + 3
    while time.time() < deadline:
        current = service.get_job(user, job["id"])
        if current["status"] not in {"queued", "running"}:
            break
        time.sleep(.01)
    assert current["status"] == "completed" and current["progress_current"] == 4
    assert len(service.assets(user, item["id"])["sections"]) == 4

    release = threading.Event()
    entered = threading.Event()

    class Blocking(FakeProvider):
        def generate_structured(self, **kwargs: Any) -> str:
            entered.set()
            release.wait(2)
            return super().generate_structured(**kwargs)

    blocking = ProductionExecutionService(store, Blocking())
    regen = blocking.start_job(user, item["id"], job_type="script_section", asset_type="script_section", section_id="sec_01")
    assert entered.wait(1)
    with pytest.raises(ProductionGenerationBusy):
        blocking.start_job(user, item["id"], job_type="script_section", asset_type="script_section", section_id="sec_02")
    blocking.cancel_job(user, regen["id"])
    release.set()
    deadline = time.time() + 3
    while time.time() < deadline:
        current = blocking.get_job(user, regen["id"])
        if current["status"] not in {"queued", "running"}:
            break
        time.sleep(.01)
    assert current["status"] == "cancelled"


def test_failed_generation_job_persists_bounded_retry_diagnostics(tmp_path: Path) -> None:
    store, user, _, item = setup_item(tmp_path)
    service = ProductionExecutionService(store, FakeProvider(["bad", "still bad"]))
    job = service.start_job(
        user, item["id"], job_type="script_blueprint", asset_type="script_blueprint"
    )
    deadline = time.time() + 3
    while time.time() < deadline:
        current = service.get_job(user, job["id"])
        if current["status"] not in {"queued", "running"}:
            break
        time.sleep(.01)
    assert current["status"] == "failed"
    assert current["attempt_count"] == 2
    assert current["failure_stage"] == "json_parse"
    assert len(service.provider.calls) == 2
    assert "prompt" not in (current["error_message"] or "").casefold()


def test_interrupted_persisted_job_does_not_block_resume_after_restart(tmp_path: Path) -> None:
    store, user, _, item = setup_item(tmp_path)
    service = ProductionExecutionService(store, FakeProvider())
    blueprint = service.generate_blueprint(user, item["id"])
    service.generate_section(user, item["id"], blueprint["content"]["sections"][0]["section_id"])
    interrupted_id = store.create_production_generation_job(user, {
        "production_item_id": item["id"], "job_type": "script_resume",
        "asset_type": "script_section", "progress_total": 3,
        "model_provider": "ollama", "model_name": "stopped-model",
    })
    store.update_production_generation_job(
        user, interrupted_id, {"status": "running", "started_at": time.time()}
    )
    resumed = ProductionExecutionService(store, FakeProvider()).start_job(
        user, item["id"], job_type="script_resume", asset_type="script_section"
    )
    deadline = time.time() + 3
    while time.time() < deadline:
        current = store.get_production_generation_job(user, resumed["id"])
        if current["status"] not in {"queued", "running"}:
            break
        time.sleep(.01)
    interrupted = store.get_production_generation_job(user, interrupted_id)
    assert interrupted["status"] == "failed"
    assert interrupted["error_message"] == "Generation interrupted by application restart."
    assert current["status"] == "completed" and current["progress_current"] == 3


def test_offline_existing_assets_readable_editable_and_generation_fails_cleanly(tmp_path: Path) -> None:
    store, user, _, item = setup_item(tmp_path)
    online = ProductionExecutionService(store, FakeProvider())
    blueprint = online.generate_blueprint(user, item["id"])
    offline = ProductionExecutionService(store, OfflineProvider())
    assert offline.assets(user, item["id"])["blueprint"]["id"] == blueprint["id"]
    edited = offline.manual_version(user, blueprint["id"], content=blueprint["content"], manual_notes="offline edit")
    assert edited["version"] == 2 and edited["manual_notes"] == "offline edit"
    with pytest.raises(OllamaProviderError, match="not running"):
        offline.generate_blueprint(user, item["id"])


def test_user_isolation_foreign_asset_and_fake_item_blocked(tmp_path: Path) -> None:
    store, user, foreign, item = setup_item(tmp_path)
    service = ProductionExecutionService(store, FakeProvider())
    asset = service.generate_blueprint(user, item["id"])
    with pytest.raises(ProductionExecutionNotFound):
        service.get_asset(foreign, asset["id"])
    with pytest.raises(ProductionExecutionNotFound):
        service.versions(foreign, asset["id"])
    with pytest.raises(ProductionExecutionNotFound):
        service.manual_version(foreign, asset["id"], content=asset["content"])
    with pytest.raises(ProductionExecutionNotFound):
        service.assets(foreign, item["id"])


def test_no_execution_pipeline_dependencies() -> None:
    source = Path("src/universal_video_ai/channel_agent/execution.py").read_text(encoding="utf-8")
    assert "yt_dlp" not in source and "ffmpeg" not in source.casefold()
    assert "create_job(" not in source and "publish(" not in source
    assert "generate_audio(" not in source and "create_subtitle(" not in source


def test_api_feature_flag_owner_scope_and_fake_ownership_forbidden(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, user, foreign, item = setup_item(tmp_path)
    asset = ProductionExecutionService(store, FakeProvider()).generate_blueprint(user, item["id"])
    monkeypatch.setenv("AI_CHANNEL_AGENT_ENABLED", "true")
    bundle = production_assets(item["id"], user_id=user, store=store)
    assert bundle["blueprint"]["id"] == asset["id"]
    with pytest.raises(HTTPException) as denied:
        production_asset_detail(asset["id"], user_id=foreign, store=store)
    assert denied.value.status_code == 404
    with pytest.raises(ValidationError):
        ProductionAssetEditBody(content=asset["content"], user_id=foreign)
    with pytest.raises(ValidationError):
        ProductionAssetStatusBody(status="approved", production_item_id=item["id"])
    monkeypatch.setenv("AI_CHANNEL_AGENT_ENABLED", "false")
    with pytest.raises(HTTPException) as disabled:
        production_assets(item["id"], user_id=user, store=store)
    assert disabled.value.status_code == 404
