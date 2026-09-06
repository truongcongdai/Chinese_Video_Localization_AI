from pathlib import Path
import sqlite3

import pytest

from universal_video_ai.channel_agent.production import ProductionQueueService
from universal_video_ai.channel_agent.production_render import (
    ProductionRenderError,
    ProductionRenderNotFound,
    ProductionRenderService,
)
from universal_video_ai.content_os.renderer import (
    ValidationResult,
    ValidationStatus,
)
from universal_video_ai.web.auth import get_current_user_id
from universal_video_ai.web.channel_agent_router import router
from universal_video_ai.web.store import Store


def prepared_item(tmp_path: Path):
    store = Store(tmp_path / "cp7b.sqlite3")
    owner = store.create_user("owner", "x")
    foreign = store.create_user("foreign", "x")
    brief = {
        "working_title": "Owned production",
        "target_format": "long_form",
        "target_duration_min": 1,
        "target_duration_max": 1,
    }
    item_id = store.insert_production_item_with_tasks(owner, {
        "opportunity_id": 77, "status": "planning", "priority": 1,
        "opportunity_rank_score": 1, "working_title": "Owned production",
        "selected_angle": "Original", "target_format": "long_form",
        "target_duration_min": 1, "target_duration_max": 1,
        "production_brief": brief, "rights_status": "owned",
        "rights_gate_status": "approved", "planning_ready": True,
        "rights_ready": True,
    }, ProductionQueueService.build_tasks(brief, "long_form"))
    blueprint = store.insert_production_asset(
        owner, item_id, asset_type="script_blueprint",
        payload={
            "sections": [{"section_index": 1}],
            "section_count": 1,
            "narration_wpm": 145,
        },
    )
    section = store.insert_production_asset(
        owner, item_id, asset_type="script_section", asset_key="01",
        payload={
            "content": "Xin chào. Đây là nội dung thử nghiệm.",
            "target_duration_minutes": 0.04,
            "section_index": 1,
            "blueprint_asset_id": blueprint["id"],
            "blueprint_version": blueprint["version"],
            "budget_acceptable": True,
        },
    )
    script = store.insert_production_asset(
        owner, item_id, asset_type="script_draft", status="approved",
        payload={
            "blueprint_asset_id": blueprint["id"],
            "blueprint_version": blueprint["version"],
            "section_count": 1,
            "budget_acceptable": True,
            "source_section_versions": [{
                "asset_id": section["id"], "version": section["version"],
                "section_index": 1,
            }],
        },
    )
    common = {
        "source_script_asset_id": script["id"],
        "source_script_version": script["version"],
    }
    for kind, payload in (
        ("visual_plan", {**common, "scenes": [{
            "section": 1, "scene": 1, "purpose": "localize",
            "visual_description": "Use the owned source video",
            "source_strategy": "owned_asset", "duration": 2.4,
            "rights_notes": "Owner-attested source",
        }]}),
        ("voice_plan", {
            **common, "language": "vi", "voice_character": "narrator",
            "tone": "clear", "pace": "natural", "energy": "medium",
            "pronunciation_guidance": [], "section_delivery_notes": [],
            "pause_emphasis_guidance": [],
        }),
        ("thumbnail_brief", {
            **common, "core_idea": "test", "main_subject": "owned source",
            "composition": "centered", "visual_hierarchy": "subject first",
            "emotion": "clear", "contrast_guidance": "high",
            "text_suggestion": "Localized", "brand_guidance": "original",
            "forbidden_elements": ["competitor media"],
        }),
        ("metadata_package", {
            **common, "title_candidates": ["test"], "recommended_title": "test",
            "description": "Owned localization", "hashtags": ["localization"],
            "tags": ["Vietnamese"], "chapter_suggestions": ["00:00 Intro"],
            "cta": "Subscribe", "search_intent": "Vietnamese localization",
            "keyword_alignment": "localized video",
            "audience_alignment": "Vietnamese viewers",
        }),
    ):
        store.insert_production_asset(
            owner, item_id, asset_type=kind, status="approved", payload=payload
        )
    return store, owner, foreign, item_id


class StubTTS:
    def __init__(self):
        self.calls = 0

    def synthesize(self, text, language="vi", voice=None, output_path=None):
        self.calls += 1
        output_path.write_bytes(b"audio")
        return output_path


class StubMixer:
    def build_dubbed_track(self, clips, total_duration, output_path):
        output_path.write_bytes(b"voice")
        return output_path

    def mix(self, spec, output_path):
        output_path.write_bytes(b"mix")
        return output_path


class StubRenderer:
    def __init__(self, fail_once=False):
        self.fail_once = fail_once

    def get_cleanup_geometry(self, overlays, frame_w, frame_h):
        return [{
            "height_ratio": 0.06,
            "cleanup_safe": True,
            "frame_width": frame_w,
            "frame_height": frame_h,
        } for _ in overlays]

    def render(self, video_path, audio_path, subtitles=None, output_path=None, text_overlays=None):
        if self.fail_once:
            self.fail_once = False
            raise RuntimeError("interrupted render")
        output_path.write_bytes(b"mp4")
        return output_path


def valid_result(path):
    return ValidationResult(
        ValidationStatus.VALID, str(path), 3, 2.4, "640x360",
        "h264", "aac", 1000, [], [],
    )


def test_render_job_persistence_owner_isolation_and_rights_gate(tmp_path: Path):
    store, owner, foreign, item = prepared_item(tmp_path)
    source = tmp_path / "source.mp4"
    source.write_bytes(b"video")
    service = ProductionRenderService(store, output_root=tmp_path / "outputs")
    with pytest.raises(ProductionRenderError, match="attested"):
        service.submit(owner, item, source_video_path=str(source), source_media_rights="research_only")
    job = service.submit(
        owner, item, source_video_path=str(source), source_media_rights="owned"
    )
    restarted = ProductionRenderService(Store(store.db_path), output_root=tmp_path / "outputs")
    assert restarted.get(owner, item, job["id"])["status"] == "queued"
    with pytest.raises(ProductionRenderNotFound):
        restarted.get(foreign, item, job["id"])


def test_approved_assets_required(tmp_path: Path):
    store, owner, _, item = prepared_item(tmp_path)
    with store._connect() as conn:
        conn.execute(
            "UPDATE production_assets SET status='rejected' "
            "WHERE production_item_id=? AND asset_type='voice_plan'",
            (item,),
        )
    source = tmp_path / "source.mp4"
    source.write_bytes(b"video")
    with pytest.raises(ProductionRenderError, match="voice_plan"):
        ProductionRenderService(store).submit(
            owner, item, source_video_path=str(source), source_media_rights="owned"
        )


def test_malformed_approved_asset_cannot_bypass_cp7a_package_qa(tmp_path: Path):
    store, owner, _, item = prepared_item(tmp_path)
    with store._connect() as conn:
        conn.execute(
            "UPDATE production_assets SET payload_json='{}' "
            "WHERE production_item_id=? AND asset_type='visual_plan'",
            (item,),
        )
    source = tmp_path / "source.mp4"
    source.write_bytes(b"video")
    with pytest.raises(ProductionRenderError, match="Production Asset Package"):
        ProductionRenderService(store).submit(
            owner, item, source_video_path=str(source), source_media_rights="owned"
        )


def test_render_executes_voice_plan_subtitles_cleanup_and_qc(monkeypatch, tmp_path: Path):
    store, owner, _, item = prepared_item(tmp_path)
    source = tmp_path / "source.mp4"
    source.write_bytes(b"video")
    tts = StubTTS()
    service = ProductionRenderService(
        store, tts_service=tts, mixer=StubMixer(), renderer=StubRenderer(),
        output_root=tmp_path / "outputs",
    )
    monkeypatch.setattr(service, "_probe", lambda _: {
        "format": {"duration": "2.4"},
        "streams": [
            {"codec_type": "video", "width": 640, "height": 360, "duration": "2.4"},
            {"codec_type": "audio", "duration": "2.4"},
        ],
    })
    monkeypatch.setattr(
        "universal_video_ai.channel_agent.production_render.MP4Validator.validate",
        lambda self, file_path, expected_duration, expected_resolution: valid_result(file_path),
    )
    job = service.submit(
        owner, item, source_video_path=str(source), source_media_rights="owned",
        voice_id="vi-VN-HoaiMyNeural",
        source_subtitle_boxes=[{
            "start": 0.0, "end": 0.8, "x": 100, "y": 300,
            "width": 400, "height": 20,
        }],
    )
    completed = service.run(owner, item, job["id"])
    assert completed["status"] == "completed"
    assert completed["current_stage"] == "completed"
    assert completed["qc"]["subtitle_coverage"] == 1.0
    assert completed["qc"]["subtitle_readability_safe"] is True
    assert completed["qc"]["tts_coverage"] == 1.0
    assert completed["qc"]["av_duration_match"] is True
    assert completed["qc"]["start_coverage"] is True
    assert completed["qc"]["maximum_cleanup_height_ratio"] == 0.06
    assert Path(completed["output_path"]).read_bytes() == b"mp4"
    assert tts.calls == 1


def test_voice_plan_language_name_normalizes_to_vietnamese_code():
    assert ProductionRenderService._voice_language({"language": "Vietnamese"}) == "vi"
    assert ProductionRenderService._voice_language({"language": "Tiếng Việt"}) == "vi"


def test_observed_subtitle_timing_drives_localization_start(tmp_path: Path):
    store, owner, _, item = prepared_item(tmp_path)
    service = ProductionRenderService(store)
    script = service._approved(owner, item)["script_draft"]
    segments = service._section_segments(
        owner, item, script,
        [{"start": 0.0, "end": 0.8}],
    )
    assert segments[0].start == 0.0
    assert segments[0].end == 0.8


def test_failed_render_resumes_without_regenerating_completed_tts(monkeypatch, tmp_path: Path):
    store, owner, _, item = prepared_item(tmp_path)
    source = tmp_path / "source.mp4"
    source.write_bytes(b"video")
    tts, renderer = StubTTS(), StubRenderer(fail_once=True)
    service = ProductionRenderService(
        store, tts_service=tts, mixer=StubMixer(), renderer=renderer,
        output_root=tmp_path / "outputs",
    )
    monkeypatch.setattr(service, "_probe", lambda _: {
        "format": {"duration": "2.4"},
        "streams": [
            {"codec_type": "video", "width": 640, "height": 360, "duration": "2.4"},
            {"codec_type": "audio", "duration": "2.4"},
        ],
    })
    monkeypatch.setattr(
        "universal_video_ai.channel_agent.production_render.MP4Validator.validate",
        lambda self, file_path, expected_duration, expected_resolution: valid_result(file_path),
    )
    job = service.submit(
        owner, item, source_video_path=str(source), source_media_rights="licensed"
    )
    with pytest.raises(ProductionRenderError, match="interrupted"):
        service.run(owner, item, job["id"])
    assert service.get(owner, item, job["id"])["status"] == "failed"
    restarted_tts = StubTTS()
    restarted = ProductionRenderService(
        Store(store.db_path), tts_service=restarted_tts,
        mixer=StubMixer(), renderer=StubRenderer(),
        output_root=tmp_path / "outputs",
    )
    monkeypatch.setattr(restarted, "_probe", lambda _: {
        "format": {"duration": "2.4"},
        "streams": [
            {"codec_type": "video", "width": 640, "height": 360, "duration": "2.4"},
            {"codec_type": "audio", "duration": "2.4"},
        ],
    })
    completed = restarted.resume(owner, item, job["id"])
    assert completed["status"] == "completed"
    assert tts.calls == 1
    assert restarted_tts.calls == 0


def test_render_job_migration_is_additive_and_idempotent(tmp_path: Path):
    database = tmp_path / "old-cp7a.sqlite3"
    Store(database)
    with sqlite3.connect(database) as conn:
        conn.execute("DROP TABLE production_render_jobs")
        conn.execute("DROP INDEX IF EXISTS idx_production_render_jobs_owner")

    Store(database)
    Store(database)
    with sqlite3.connect(database) as conn:
        table = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='production_render_jobs'"
        ).fetchone()
        columns = {
            row[1] for row in conn.execute("PRAGMA table_info(production_render_jobs)")
        }
    assert table == ("production_render_jobs",)
    assert {"approved_asset_refs_json", "qc_json", "output_path"} <= columns


def test_render_routes_are_authenticated_and_do_not_accept_user_id():
    paths = {
        route.path: route
        for route in router.routes
        if "/render-jobs" in route.path
    }
    assert paths
    for route in paths.values():
        assert any(dep.call is get_current_user_id for dep in route.dependant.dependencies)
        assert "user_id" not in {
            field.name for field in route.dependant.body_params
        }
