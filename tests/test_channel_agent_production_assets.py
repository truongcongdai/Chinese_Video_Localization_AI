from __future__ import annotations

import json
import inspect
import re
import sqlite3
from pathlib import Path

import pytest
from fastapi import HTTPException

from universal_video_ai.channel_agent.production import ProductionQueueService
from universal_video_ai.channel_agent.production_assets import (
    ProductionAssetError,
    ProductionAssetNotFound,
    ProductionAssetService,
    ProductionGenerationError,
    count_words,
)
from universal_video_ai.channel_agent.providers import OllamaProviderError
from universal_video_ai.web.auth import get_current_user_id
from universal_video_ai.web.channel_agent_router import (
    ProductionAssetReviewBody,
    ProductionBlueprintBody,
    ProductionSectionBody,
    production_assets,
    router,
)
from universal_video_ai.web.store import Store


def _words(count: int, prefix: str = "narration") -> str:
    alphabet = "abcdefghijklmnopqrstuvwxyz"
    clean_prefix = "".join(character for character in prefix if character.isalpha())
    return " ".join(
        clean_prefix + alphabet[(index // 26) % 26] + alphabet[index % 26]
        for index in range(count)
    )


class FakeProvider:
    name = "fake-ollama"

    def __init__(self, *, unsafe_visual: bool = False) -> None:
        self.calls = 0
        self.unsafe_visual = unsafe_visual

    def generate_structured(self, *, system_prompt, user_prompt, **kwargs):
        del system_prompt, kwargs
        self.calls += 1
        if "Create exactly" in user_prompt:
            count = int(re.search(r"Create exactly (\d+)", user_prompt).group(1))
            return json.dumps({"sections": [{
                "title": f"Section {i}", "purpose": f"Purpose {i}",
                "content_goal": f"Goal {i}", "key_points": [f"Point {i}"],
                "transition_guidance": f"Transition {i}",
            } for i in range(1, count + 1)]})
        if "additional" in user_prompt:
            return json.dumps({"content": _words(1100, f"continuation{self.calls}_")})
        if '"paragraphs"' in user_prompt:
            return json.dumps({"paragraphs": [_words(25, f"draft{self.calls}_{part}_")
                                               for part in ("a", "b", "c", "d")]})
        if "source_strategy" in user_prompt:
            description = ("download competitor video" if self.unsafe_visual
                           else "Original illustrated family timeline")
            return json.dumps({"scenes": [{
                "section": 1, "scene": 1, "purpose": "Introduce",
                "visual_description": description,
                "source_strategy": "original_generation", "duration": 20,
                "overlay_text": "Opening", "transition": "fade",
                "rights_notes": "Created originally",
            }]})
        if "voice_character" in user_prompt:
            return json.dumps({"language": "Vietnamese", "voice_character": "warm",
                "tone": "immersive", "pace": "145 wpm", "energy": "measured",
                "pronunciation_guidance": [], "section_delivery_notes": [],
                "pause_emphasis_guidance": []})
        if "core_idea" in user_prompt:
            return json.dumps({"core_idea": "A family rises", "main_subject": "Ancestor",
                "composition": "Strong central portrait", "visual_hierarchy": "face then title",
                "emotion": "hope", "contrast_guidance": "warm/cool",
                "text_suggestion": "The Return", "brand_guidance": "editorial",
                "forbidden_elements": ["logos", "misleading claims"]})
        if "title_candidates" in user_prompt:
            return json.dumps({"title_candidates": ["The Return", "Seven Days"],
                "recommended_title": "The Return", "description": "An original story.",
                "hashtags": ["#story"], "tags": ["family"], "chapter_suggestions": ["00:00 Intro"],
                "cta": "Subscribe", "search_intent": "story", "keyword_alignment": ["family"],
                "audience_alignment": "long-form viewers"})
        raise AssertionError(user_prompt[:200])


class OfflineProvider:
    name = "ollama"

    def generate_structured(self, **kwargs):
        del kwargs
        raise OllamaProviderError("Ollama is not running. Start Ollama and try again.")


def _item(tmp_path: Path, name: str = "cp7a") -> tuple[Store, int, int, int]:
    store = Store(tmp_path / f"{name}.sqlite3")
    user = store.create_user(f"{name}-owner", "x")
    foreign = store.create_user(f"{name}-foreign", "x")
    brief = {
        "schema_version": "cp6-v1", "working_title": "Original family chronicle",
        "topic": "A family rebuilds over generations", "selected_angle": "The ancestor returns",
        "target_format": "long_form", "target_duration_min": 60,
        "target_duration_max": 60, "rights_status": "idea_only",
        "rights_guidance": "Research is reference-only; create original work.",
        "script_direction": {"language": "Vietnamese", "instruction": "Original narration"},
        "visual_direction": {"reuse_restrictions": ["no competitor download"]},
        "voice_direction": {"language": "Vietnamese"},
        "thumbnail_direction": {"focal_motif": "family"},
        "metadata_direction": {"working_title": "Original family chronicle"},
    }
    tasks = ProductionQueueService.build_tasks(brief, "long_form")
    item_id = store.insert_production_item_with_tasks(user, {
        "opportunity_id": 9001, "status": "queued", "priority": 80,
        "opportunity_rank_score": .9, "working_title": brief["working_title"],
        "selected_angle": brief["selected_angle"], "target_format": "long_form",
        "target_duration_min": 60, "target_duration_max": 60,
        "production_brief": brief, "rights_status": "idea_only",
        "rights_gate_status": "research_only", "planning_ready": True,
        "rights_ready": False,
    }, tasks)
    return store, user, foreign, item_id


def _task(store: Store, user: int, item: int, kind: str) -> dict:
    return next(row for row in store.list_production_tasks(user, item)
                if row["task_type"] == kind)


def _complete_script(service: ProductionAssetService, user: int, item: int):
    blueprint = service.generate_blueprint(user, item)["asset"]
    service.resume_script(user, item)
    draft = service.assemble_script(user, item)["asset"]
    return blueprint, draft


def _approve(service: ProductionAssetService, user: int, item: int, asset: dict):
    service.submit_for_review(user, item, asset["id"])
    return service.review_asset(user, item, asset["id"], decision="approved")


def test_additive_schema_is_fresh_and_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "schema.sqlite3"
    Store(path)
    Store(path)
    with Store(path)._connect() as conn:
        names = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"production_assets", "production_generation_jobs"} <= names


def test_additive_schema_preserves_an_existing_database(tmp_path: Path) -> None:
    path = tmp_path / "existing.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE legacy_marker (value TEXT NOT NULL)")
        connection.execute("INSERT INTO legacy_marker VALUES ('preserved')")
    Store(path)
    Store(path)
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT value FROM legacy_marker").fetchone()[0] == "preserved"
        names = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"production_assets", "production_generation_jobs"} <= names


def test_blueprint_has_deterministic_longform_budget(tmp_path: Path) -> None:
    store, user, _, item = _item(tmp_path)
    asset = ProductionAssetService(store, FakeProvider()).generate_blueprint(user, item)["asset"]
    payload = asset["payload"]
    assert payload["section_count"] == 8
    assert payload["target_duration_minutes"] == 60
    assert payload["narration_wpm"] == 145
    assert payload["target_total_words"] == 8700
    assert sum(row["target_words"] for row in payload["sections"]) == 8700
    assert sum(round(row["target_duration_minutes"] * 60)
               for row in payload["sections"]) == 3600
    assert ProductionAssetService(store, FakeProvider()).list_jobs(user, item)[0][
        "completed_sections"
    ] == 8


def test_section_budget_versioning_resume_and_assembly(tmp_path: Path) -> None:
    store, user, _, item = _item(tmp_path)
    service = ProductionAssetService(store, FakeProvider())
    blueprint = service.generate_blueprint(user, item)["asset"]
    first = service.generate_section(user, item, 1)["asset"]
    second_version = service.generate_section(user, item, 1)["asset"]
    assert first["version"] == 1 and second_version["version"] == 2
    assert first["payload"]["actual_words"] >= first["payload"]["target_words"] * .8
    assert first["payload"]["continuation_attempts"] == 1
    assert len(service.list_assets(user, item, asset_type="script_section",
                                   asset_key="01")) == 2
    # A fresh service/Store instance models an application restart.
    restarted = ProductionAssetService(Store(store.db_path), FakeProvider())
    resumed = restarted.resume_script(user, item)
    assert 1 in resumed["preserved_sections"]
    assert resumed["generated_sections"] == list(range(2, 9))
    draft = restarted.assemble_script(user, item)["asset"]
    assert draft["payload"]["source_section_versions"][0]["version"] == 2
    assert draft["payload"]["section_count"] == blueprint["payload"]["section_count"]
    assert draft["payload"]["actual_total_words"] >= 6960
    assert draft["payload"]["budget_acceptable"]
    assert _task(store, user, item, "SCRIPT")["status"] == "ready"


def test_explicit_approval_completes_script_and_preserves_superseded_history(tmp_path: Path) -> None:
    store, user, _, item = _item(tmp_path)
    service = ProductionAssetService(store, FakeProvider())
    _, draft1 = _complete_script(service, user, item)
    approved1 = _approve(service, user, item, draft1)
    assert approved1["asset"]["status"] == "approved"
    assert _task(store, user, item, "SCRIPT")["status"] == "completed"
    assert _task(store, user, item, "VISUAL_PLAN")["status"] == "ready"
    draft2 = service.assemble_script(user, item)["asset"]
    _approve(service, user, item, draft2)
    versions = service.list_assets(user, item, asset_type="script_draft")
    assert versions[0]["status"] == "approved"
    assert versions[1]["status"] == "superseded"
    assert versions[1]["payload"] == draft1["payload"]


def test_rejection_does_not_complete_script(tmp_path: Path) -> None:
    store, user, _, item = _item(tmp_path)
    service = ProductionAssetService(store, FakeProvider())
    _, draft = _complete_script(service, user, item)
    result = service.review_asset(user, item, draft["id"], decision="rejected")
    assert result["asset"]["status"] == "rejected"
    assert _task(store, user, item, "SCRIPT")["status"] == "ready"


def test_approval_requires_review_and_terminal_versions_are_immutable(tmp_path: Path) -> None:
    store, user, _, item = _item(tmp_path)
    service = ProductionAssetService(store, FakeProvider())
    _, draft = _complete_script(service, user, item)
    with pytest.raises(ProductionAssetError, match="Submit this exact asset version"):
        service.review_asset(user, item, draft["id"], decision="approved")
    approved = _approve(service, user, item, draft)["asset"]
    assert approved["status"] == "approved" and approved["approved_at"]
    with pytest.raises(ProductionAssetError, match="draft or review"):
        service.review_asset(user, item, draft["id"], decision="rejected")


def test_owner_isolation_for_assets_jobs_and_api_adapter(tmp_path: Path) -> None:
    store, user, foreign, item = _item(tmp_path)
    service = ProductionAssetService(store, FakeProvider())
    asset = service.generate_blueprint(user, item)["asset"]
    with pytest.raises(ProductionAssetNotFound):
        service.get_asset(foreign, item, asset["id"])
    with pytest.raises(ProductionAssetNotFound):
        service.list_jobs(foreign, item)
    with pytest.raises(HTTPException) as caught:
        production_assets(item, None, None, foreign, store)
    assert caught.value.status_code == 404


def test_word_budget_failure_is_persisted_and_bounded(tmp_path: Path) -> None:
    class Tiny(FakeProvider):
        def generate_structured(self, **kwargs):
            if "Create exactly" in kwargs["user_prompt"]:
                return super().generate_structured(**kwargs)
            self.calls += 1
            return json.dumps({"content": "too short"})

    store, user, _, item = _item(tmp_path)
    service = ProductionAssetService(store, Tiny(), max_continuations=2)
    service.generate_blueprint(user, item)
    with pytest.raises(ProductionGenerationError, match="bounded continuation"):
        service.generate_section(user, item, 1)
    assets = service.list_assets(user, item, asset_type="script_section")
    assert len(assets) == 1 and not assets[0]["payload"]["budget_acceptable"]
    assert assets[0]["payload"]["continuation_attempts"] == 2
    assert service.list_jobs(user, item)[0]["status"] == "failed"
    assert service.list_jobs(user, item)[0]["current_section"] == 1


def test_saved_assets_survive_offline_provider_and_failure_is_clear(tmp_path: Path) -> None:
    store, user, _, item = _item(tmp_path)
    online = ProductionAssetService(store, FakeProvider())
    blueprint = online.generate_blueprint(user, item)["asset"]
    offline = ProductionAssetService(Store(store.db_path), OfflineProvider())
    assert offline.get_asset(user, item, blueprint["id"])["payload"] == blueprint["payload"]
    with pytest.raises(ProductionGenerationError, match="Ollama is not running"):
        offline.generate_section(user, item, 1)
    assert offline.get_asset(user, item, blueprint["id"])["status"] == "draft"
    assert offline.list_jobs(user, item)[0]["status"] == "failed"


def test_downstream_asset_cannot_approve_against_superseded_script(tmp_path: Path) -> None:
    store, user, _, item = _item(tmp_path)
    service = ProductionAssetService(store, FakeProvider())
    _, first_draft = _complete_script(service, user, item)
    _approve(service, user, item, first_draft)
    visual = service.generate_visual_plan(user, item)["asset"]
    second_draft = service.assemble_script(user, item)["asset"]
    _approve(service, user, item, second_draft)
    service.submit_for_review(user, item, visual["id"])
    with pytest.raises(ProductionAssetError, match="current approved Script Draft"):
        service.review_asset(user, item, visual["id"], decision="approved")


def test_failed_task_gate_does_not_partially_approve_asset(tmp_path: Path) -> None:
    store, user, _, item = _item(tmp_path)
    service = ProductionAssetService(store, FakeProvider())
    _, draft = _complete_script(service, user, item)
    _approve(service, user, item, draft)
    visual = service.generate_visual_plan(user, item)["asset"]
    service.submit_for_review(user, item, visual["id"])
    visual_task = _task(store, user, item, "VISUAL_PLAN")
    service.queue.change_task_status(
        user, item, visual_task["id"], status="blocked", note="editorial hold"
    )
    with pytest.raises(ProductionAssetError, match="not ready for approval"):
        service.review_asset(user, item, visual["id"], decision="approved")
    assert service.get_asset(user, item, visual["id"])["status"] == "review"


def test_downstream_assets_qa_package_and_separate_rights(tmp_path: Path) -> None:
    store, user, _, item = _item(tmp_path)
    service = ProductionAssetService(store, FakeProvider())
    _, draft = _complete_script(service, user, item)
    _approve(service, user, item, draft)
    generated = [
        service.generate_visual_plan(user, item)["asset"],
        service.generate_voice_plan(user, item)["asset"],
        service.generate_thumbnail_brief(user, item)["asset"],
        service.generate_metadata_package(user, item)["asset"],
    ]
    assert generated[0]["payload"]["scenes"][0]["source_strategy"] == "original_generation"
    for asset in generated:
        _approve(service, user, item, asset)
    before = service.package(user, item)
    assert before["asset_ready"] and before["qa_status"] == "approved"
    assert before["planning_ready"] and not before["rights_ready"]
    assert before["rights_gate"] == "research_only"
    qa = service.inspect_qa(user, item, complete_task=True)
    assert qa["passed"] and _task(store, user, item, "QA")["status"] == "completed"
    assert ProductionQueueService(store).get(user, item)["status"] == "completed"


def test_visual_plan_rejects_competitor_media_strategy_after_one_repair(tmp_path: Path) -> None:
    store, user, _, item = _item(tmp_path)
    service = ProductionAssetService(store, FakeProvider())
    _, draft = _complete_script(service, user, item)
    _approve(service, user, item, draft)
    unsafe = ProductionAssetService(store, FakeProvider(unsafe_visual=True))
    with pytest.raises(ProductionGenerationError, match="one repair retry"):
        unsafe.generate_visual_plan(user, item)
    assert unsafe.list_assets(user, item, asset_type="visual_plan") == []
    assert unsafe.list_jobs(user, item)[0]["status"] == "failed"


def test_count_words_supports_vietnamese_and_no_shell_or_media_execution() -> None:
    assert count_words("M?t gia t?c tr? l?i ? th?t m?nh m?.") == 8
    source = Path(
        "src/universal_video_ai/channel_agent/production_assets.py"
    ).read_text(encoding="utf-8")
    assert "shell=True" not in source
    assert "subprocess" not in source
    assert "ffmpeg" not in source.casefold()
    assert "urlretrieve" not in source


def test_cp7a_api_models_forbid_frontend_identity_and_routes_require_auth() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ProductionBlueprintBody(duration_minutes=60, user_id=7)
    with pytest.raises(ValidationError):
        ProductionSectionBody(blueprint_asset_id=1, user_id=7)
    with pytest.raises(ValidationError):
        ProductionAssetReviewBody(note="ok", user_id=7)
    cp7a_routes = [
        route for route in router.routes
        if "/production/{item_id}/" in route.path
        and any(marker in route.path for marker in ("assets", "generation-jobs", "/qa"))
    ]
    assert len(cp7a_routes) >= 15
    for route in cp7a_routes:
        parameters = inspect.signature(route.endpoint).parameters.values()
        dependencies = [
            parameter.default.dependency for parameter in parameters
            if getattr(parameter.default, "dependency", None)
        ]
        assert get_current_user_id in dependencies, route.path
