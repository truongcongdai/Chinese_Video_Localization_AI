"""CP7A script and asset execution; never renders, downloads, or publishes."""

from __future__ import annotations

import json
import math
import re
import time
from typing import Any, Callable, Optional

from universal_video_ai.channel_agent.production import ProductionNotFound, ProductionQueueService
from universal_video_ai.channel_agent.providers import AIProvider, OllamaProviderError
from universal_video_ai.web.store import Store

ASSET_TYPES = {
    "script_blueprint", "script_section", "script_draft", "visual_plan",
    "voice_plan", "thumbnail_brief", "metadata_package",
}
ASSET_STATUSES = {"draft", "review", "approved", "rejected", "superseded"}
JOB_STATUSES = {"queued", "running", "paused", "completed", "failed", "cancelled"}
APPROVABLE_ASSETS = {
    "script_draft": "SCRIPT", "visual_plan": "VISUAL_PLAN",
    "voice_plan": "VOICE_PLAN", "thumbnail_brief": "THUMBNAIL",
    "metadata_package": "METADATA",
}
SAFE_VISUAL_STRATEGIES = {
    "original_generation", "owned_asset", "licensed_stock", "public_domain",
    "manual_creation", "diagram", "map", "text_card",
}
PROHIBITED_REUSE = (
    "download competitor", "reuse competitor", "competitor footage",
    "mirror competitor", "crop competitor", "speed-change competitor",
    "remove watermark", "download competitor shorts", "download competitor reels",
)
WORD_RE = re.compile(r"\b[^\W_]+(?:['?-][^\W_]+)*\b", re.UNICODE)


class ProductionAssetError(RuntimeError):
    pass


class ProductionAssetNotFound(ProductionAssetError):
    pass


class ProductionGenerationError(ProductionAssetError):
    pass


def count_words(text: str) -> int:
    return len(WORD_RE.findall(text or ""))


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class ProductionAssetService:
    def __init__(
        self, store: Store, provider: AIProvider, *, narration_wpm: int = 145,
        minimum_word_ratio: float = .80, max_continuations: int = 5,
        temperature: float = .35, repair_temperature: float = 0, top_p: float = .85,
        blueprint_num_predict: int = 1800, section_num_predict: int = 2400,
        asset_num_predict: int = 2600,
    ) -> None:
        self.store, self.provider = store, provider
        self.queue = ProductionQueueService(store)
        self.narration_wpm = min(220, max(80, int(narration_wpm)))
        self.minimum_word_ratio = min(1., max(.5, float(minimum_word_ratio)))
        self.max_continuations = min(6, max(0, int(max_continuations)))
        self.temperature = min(1., max(0., float(temperature)))
        self.repair_temperature = min(.2, max(0., float(repair_temperature)))
        self.top_p = min(1., max(.1, float(top_p)))
        self.blueprint_num_predict = int(blueprint_num_predict)
        self.section_num_predict = int(section_num_predict)
        self.asset_num_predict = int(asset_num_predict)

    def _item(self, user_id: int, item_id: int) -> dict[str, Any]:
        try:
            return self.queue.get(user_id, item_id)
        except ProductionNotFound as exc:
            raise ProductionAssetNotFound(str(exc)) from exc

    def list_assets(self, user_id: int, item_id: int, *, asset_type: Optional[str] = None,
                    asset_key: Optional[str] = None) -> list[dict[str, Any]]:
        self._item(user_id, item_id)
        if asset_type is not None and asset_type not in ASSET_TYPES:
            raise ProductionAssetError("Unsupported production asset type.")
        return self.store.list_production_assets(
            user_id, item_id, asset_type=asset_type, asset_key=asset_key
        )

    def get_asset(self, user_id: int, item_id: int, asset_id: int) -> dict[str, Any]:
        self._item(user_id, item_id)
        asset = self.store.get_production_asset(user_id, item_id, asset_id)
        if not asset:
            raise ProductionAssetNotFound("Production asset not found.")
        return asset

    def list_jobs(self, user_id: int, item_id: int) -> list[dict[str, Any]]:
        self._item(user_id, item_id)
        return self.store.list_production_generation_jobs(user_id, item_id)

    def get_job(self, user_id: int, item_id: int, job_id: int) -> dict[str, Any]:
        self._item(user_id, item_id)
        job = self.store.get_production_generation_job(user_id, item_id, job_id)
        if not job:
            raise ProductionAssetNotFound("Production generation job not found.")
        return job

    def _new_job(self, user_id: int, item_id: int, job_type: str, *,
                 total_sections: int = 0, request: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        job = self.store.create_production_generation_job(
            user_id, item_id, job_type=job_type, total_sections=total_sections, request=request
        )
        if not job:
            raise ProductionAssetNotFound("Production item not found.")
        return job

    def _update_job(self, user_id: int, item_id: int, job_id: int, **changes: Any) -> None:
        self.store.update_production_generation_job(user_id, item_id, job_id, changes)

    def _run_job(self, user_id: int, item_id: int, job: dict[str, Any],
                 operation: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        job_id = int(job["id"])
        self._update_job(user_id, item_id, job_id, status="running",
                         current_stage="generating", started_at=time.time(), error=None)
        try:
            result = operation()
        except Exception as exc:
            self._update_job(user_id, item_id, job_id, status="failed", current_stage="failed",
                             error=(str(exc)[:1000] or "Generation failed."), completed_at=time.time())
            if isinstance(exc, ProductionAssetError):
                raise
            if isinstance(exc, OllamaProviderError):
                raise ProductionGenerationError(str(exc)) from exc
            raise
        self._update_job(user_id, item_id, job_id, status="completed",
                         current_stage="completed", progress=100., completed_at=time.time())
        result["generation_job"] = self.get_job(user_id, item_id, job_id)
        return result

    def _structured(self, *, system: str, prompt: str, num_predict: int,
                    validator: Callable[[Any], dict[str, Any]]) -> dict[str, Any]:
        error = "invalid response"
        request = prompt
        for attempt in range(2):
            try:
                raw = self.provider.generate_structured(
                    system_prompt=system, user_prompt=request,
                    temperature=self.temperature if not attempt else self.repair_temperature,
                    top_p=self.top_p, num_predict=num_predict,
                )
                return validator(json.loads(raw))
            except OllamaProviderError:
                raise
            except (json.JSONDecodeError, TypeError, ValueError, KeyError) as exc:
                error = str(exc)[:500] or error
                request = (
                    "Repair the prior response. Return only one valid JSON object matching the "
                    "requested schema; no markdown. Validation error: " + error
                    + "\nOriginal request:\n" + prompt
                )
        raise ProductionGenerationError(
            "The local model did not return a valid structured asset after one repair retry: "
            + error
        )

    def _target_budget(self, item: dict[str, Any], duration: Optional[float],
                       wpm: Optional[int]) -> tuple[float, int, int]:
        brief = item["production_brief"]
        if duration is None:
            low = item.get("target_duration_min") or brief.get("target_duration_min")
            high = item.get("target_duration_max") or brief.get("target_duration_max")
            duration = ((float(low) + float(high)) / 2 if low and high else
                        float(low or high or (60 if item["target_format"] == "long_form" else 3)))
        duration = min(180., max(1., float(duration)))
        rate = min(220, max(80, int(wpm or brief.get("narration_wpm") or self.narration_wpm)))
        return duration, rate, round(duration * rate)

    @staticmethod
    def _section_count(duration: float, target_format: str) -> int:
        return (min(4, max(2, round(duration / 1.5))) if target_format == "short_form"
                else min(12, max(4, math.ceil(duration / 7.5))))

    def generate_blueprint(self, user_id: int, item_id: int, *,
                           duration_minutes: Optional[float] = None,
                           narration_wpm: Optional[int] = None) -> dict[str, Any]:
        item = self._item(user_id, item_id)
        duration, wpm, target_words = self._target_budget(item, duration_minutes, narration_wpm)
        count = self._section_count(duration, str(item["target_format"]))
        job = self._new_job(user_id, item_id, "script_blueprint", total_sections=count,
                            request={"duration_minutes": duration, "narration_wpm": wpm})

        def operation() -> dict[str, Any]:
            def validate(value: Any) -> dict[str, Any]:
                rows = value.get("sections") if isinstance(value, dict) else None
                if not isinstance(rows, list):
                    raise ValueError("sections must be an array")
                maximum = 12 if item["target_format"] == "long_form" else count
                if not count <= len(rows) <= maximum:
                    raise ValueError(
                        f"{count}-{maximum} sections are required; received {len(rows)}")
                result = []
                for index, row in enumerate(rows, 1):
                    if not isinstance(row, dict) or not all(row.get(key) for key in
                            ("title", "purpose", "content_goal", "key_points")):
                        raise ValueError("section fields are incomplete")
                    if not isinstance(row["key_points"], list):
                        raise ValueError("key_points must be an array")
                    result.append({
                        "section_index": index, "title": str(row["title"])[:300],
                        "purpose": str(row["purpose"])[:1000],
                        "content_goal": str(row["content_goal"])[:1500],
                        "key_points": [str(x)[:500] for x in row["key_points"][:10]],
                        "transition_guidance": str(row.get("transition_guidance") or "")[:800],
                    })
                return {"sections": result}
            value = self._structured(
                system="Create an original long-form script architecture. Return JSON only.",
                prompt=(f"Create exactly {count} coherent sections. Each needs title, purpose, "
                        "content_goal, key_points, transition_guidance. Research is inspiration only.\n"
                        + _json_text(item["production_brief"])),
                num_predict=self.blueprint_num_predict, validator=validate,
            )
            actual_count = len(value["sections"])
            base_w, rem_w = divmod(target_words, actual_count)
            base_s, rem_s = divmod(round(duration * 60), actual_count)
            for offset, section in enumerate(value["sections"]):
                words, seconds = base_w + (offset < rem_w), base_s + (offset < rem_s)
                section.update({"target_words": words, "actual_words": 0,
                    "target_duration_minutes": round(seconds / 60, 2),
                    "estimated_duration_minutes": 0., "completion_percentage": 0.})
            self._update_job(
                user_id, item_id, int(job["id"]), total_sections=actual_count,
                completed_sections=actual_count)
            payload = {"schema_version": "cp7a-v1", "section_count": actual_count,
                "target_duration_minutes": duration, "narration_wpm": wpm,
                "target_total_words": target_words, "sections": value["sections"]}
            asset = self.store.insert_production_asset(
                user_id, item_id, asset_type="script_blueprint", payload=payload)
            if not asset:
                raise ProductionAssetNotFound("Production item not found.")
            self.store.add_production_event(user_id, item_id,
                event_type="production_asset_generated",
                note=f"script_blueprint v{asset['version']} created")
            return {"asset": asset}
        return self._run_job(user_id, item_id, job, operation)

    def _current_blueprint(self, user_id: int, item_id: int) -> dict[str, Any]:
        rows = self.store.list_production_assets(user_id, item_id, asset_type="script_blueprint")
        usable = [row for row in rows if row["status"] not in {"rejected", "superseded"}]
        if not usable:
            raise ProductionAssetError("Generate a Script Blueprint first.")
        return usable[0]

    @staticmethod
    def _blueprint_section(blueprint: dict[str, Any], index: int) -> dict[str, Any]:
        for section in blueprint["payload"].get("sections") or []:
            if int(section.get("section_index") or 0) == index:
                return section
        raise ProductionAssetError("Section is not present in the current blueprint.")

    @staticmethod
    def _section_response(value: Any) -> dict[str, Any]:
        content = str(value.get("content") or "").strip() if isinstance(value, dict) else ""
        if not content and isinstance(value, dict) and isinstance(value.get("paragraphs"), list):
            paragraphs = [str(row).strip() for row in value["paragraphs"] if str(row).strip()]
            if not 1 <= len(paragraphs) <= 6:
                raise ValueError("paragraphs must contain 1-6 strings")
            content = "\n\n".join(paragraphs)
        if not content:
            raise ValueError("content or paragraphs is required")
        return {"content": content}

    @staticmethod
    def _append_unique(existing: str, continuation: str) -> str:
        known = {re.sub(r"\s+", " ", p).strip().casefold() for p in existing.split("\n\n")}
        additions = []
        for paragraph in continuation.split("\n\n"):
            normalized = re.sub(r"\s+", " ", paragraph).strip()
            if normalized and normalized.casefold() not in known:
                known.add(normalized.casefold())
                additions.append(paragraph.strip())
        return existing.rstrip() + ("\n\n" + "\n\n".join(additions) if additions else "")

    def generate_section(self, user_id: int, item_id: int, section_index: int, *,
                         blueprint_asset_id: Optional[int] = None) -> dict[str, Any]:
        item = self._item(user_id, item_id)
        blueprint = (self.get_asset(user_id, item_id, blueprint_asset_id)
                     if blueprint_asset_id else self._current_blueprint(user_id, item_id))
        if blueprint["asset_type"] != "script_blueprint":
            raise ProductionAssetError("Selected asset is not a Script Blueprint.")
        section = self._blueprint_section(blueprint, int(section_index))
        target, key = int(section["target_words"]), f"{int(section_index):02d}"
        job = self._new_job(user_id, item_id, "script_section", total_sections=1,
                            request={"section_index": section_index,
                                     "blueprint_asset_id": blueprint["id"]})
        self._update_job(
            user_id, item_id, int(job["id"]), current_section=int(section_index)
        )

        def operation() -> dict[str, Any]:
            value = self._structured(
                system="Write original Vietnamese narration. Return JSON only.",
                prompt=("Return exactly {\"paragraphs\":[\"...\",\"...\",\"...\",\"...\"]}. "
                        "Write exactly four paragraph strings of about 35-50 Vietnamese words each "
                        f"as the first chunk of this {target}-word section. "
                        "Close the array and JSON object after paragraph four. "
                        "Do not conclude the overall section if more narration will be needed. "
                        "Stay on topic; use paragraphs; avoid repetition and "
                        "source copying.\nSection:\n" + _json_text(section)
                        + "\nProduction brief:\n" + _json_text(item["production_brief"])),
                num_predict=min(self.section_num_predict, 900),
                validator=self._section_response)
            content, attempts = value["content"], 0
            continuation_failures: list[str] = []
            minimum = math.ceil(target * self.minimum_word_ratio)
            while count_words(content) < minimum and attempts < self.max_continuations:
                attempts += 1
                missing = max(1, target - count_words(content))
                try:
                    extra = self._structured(
                        system="Continue original Vietnamese narration without repetition. JSON only.",
                        prompt=("Return exactly {\"paragraphs\":[\"...\",\"...\",\"...\",\"...\"]} "
                                "with exactly four new paragraph strings of about 35-50 Vietnamese words "
                                f"each, contributing toward {missing} additional words. "
                                "Close the array and JSON object after paragraph four. Continue naturally; "
                                "do not restart or repeat. Existing ending:\n"
                                + content[-3000:] + "\nGoal:\n" + _json_text(section)),
                        num_predict=min(self.section_num_predict, 900),
                        validator=self._section_response)
                except ProductionGenerationError as exc:
                    continuation_failures.append(str(exc)[:500])
                    continue
                content = self._append_unique(content, extra["content"])
            actual = count_words(content)
            acceptable = actual >= minimum
            payload = {
                "schema_version": "cp7a-v1", "blueprint_asset_id": int(blueprint["id"]),
                "blueprint_version": int(blueprint["version"]), "section_index": section_index,
                "title": section["title"], "content": content, "target_words": target,
                "actual_words": actual,
                "target_duration_minutes": section["target_duration_minutes"],
                "estimated_duration_minutes": round(
                    actual / int(blueprint["payload"]["narration_wpm"]), 2),
                "completion_percentage": round(actual / target * 100, 1) if target else 100.,
                "budget_acceptable": acceptable, "continuation_attempts": attempts,
                "continuation_failures": continuation_failures,
            }
            asset = self.store.insert_production_asset(
                user_id, item_id, asset_type="script_section", asset_key=key, payload=payload)
            if not asset:
                raise ProductionAssetNotFound("Production item not found.")
            self.store.add_production_event(user_id, item_id,
                event_type="production_asset_generated",
                note=f"script_section {key} v{asset['version']} created")
            if not acceptable:
                raise ProductionGenerationError(
                    f"Section {section_index} reached {actual}/{target} words after "
                    f"{attempts} bounded continuation attempts.")
            self._update_job(user_id, item_id, int(job["id"]), current_section=section_index,
                             completed_sections=1, progress=100.)
            return {"asset": asset}
        return self._run_job(user_id, item_id, job, operation)

    def resume_script(self, user_id: int, item_id: int) -> dict[str, Any]:
        blueprint = self._current_blueprint(user_id, item_id)
        sections = blueprint["payload"].get("sections") or []
        job = self._new_job(user_id, item_id, "script_resume", total_sections=len(sections),
                            request={"blueprint_asset_id": blueprint["id"]})
        job_id = int(job["id"])
        self._update_job(user_id, item_id, job_id, status="running",
                         current_stage="sections", started_at=time.time())
        skipped, generated = [], []
        try:
            for section in sections:
                index = int(section["section_index"])
                versions = self.store.list_production_assets(
                    user_id, item_id, asset_type="script_section", asset_key=f"{index:02d}")
                current = next((row for row in versions
                    if row["status"] not in {"rejected", "superseded"}
                    and row["payload"].get("blueprint_asset_id") == blueprint["id"]
                    and row["payload"].get("budget_acceptable")), None)
                if current:
                    skipped.append(index)
                else:
                    self.generate_section(user_id, item_id, index,
                                          blueprint_asset_id=int(blueprint["id"]))
                    generated.append(index)
                done = len(skipped) + len(generated)
                self._update_job(user_id, item_id, job_id, current_section=index,
                    completed_sections=done, progress=round(done / len(sections) * 100, 1))
        except Exception as exc:
            self._update_job(user_id, item_id, job_id, status="failed", current_stage="failed",
                             error=str(exc)[:1000], completed_at=time.time())
            raise
        self._update_job(user_id, item_id, job_id, status="completed",
                         current_stage="completed", progress=100., completed_at=time.time())
        return {"generation_job": self.get_job(user_id, item_id, job_id),
                "generated_sections": generated, "preserved_sections": skipped}

    def assemble_script(self, user_id: int, item_id: int) -> dict[str, Any]:
        self._item(user_id, item_id)
        blueprint = self._current_blueprint(user_id, item_id)
        selected = []
        for section in blueprint["payload"].get("sections") or []:
            index = int(section["section_index"])
            versions = self.store.list_production_assets(
                user_id, item_id, asset_type="script_section", asset_key=f"{index:02d}")
            current = next((row for row in versions
                if row["status"] not in {"rejected", "superseded"}
                and row["payload"].get("blueprint_asset_id") == blueprint["id"]
                and row["payload"].get("budget_acceptable")), None)
            if not current:
                raise ProductionAssetError(f"Section {index} is missing or below its word budget.")
            selected.append(current)
        content = "\n\n".join(
            f"## {row['payload']['title']}\n\n{row['payload']['content']}" for row in selected)
        actual = sum(int(row["payload"]["actual_words"]) for row in selected)
        target = int(blueprint["payload"]["target_total_words"])
        wpm = int(blueprint["payload"]["narration_wpm"])
        payload = {
            "schema_version": "cp7a-v1", "blueprint_asset_id": blueprint["id"],
            "blueprint_version": blueprint["version"], "content": content,
            "target_total_words": target, "actual_total_words": actual,
            "target_duration_minutes": blueprint["payload"]["target_duration_minutes"],
            "estimated_duration_minutes": round(actual / wpm, 2),
            "completion_percentage": round(actual / target * 100, 1) if target else 100.,
            "section_count": len(selected),
            "source_section_versions": [{"section_index": row["payload"]["section_index"],
                "asset_id": row["id"], "version": row["version"]} for row in selected],
            "budget_acceptable": actual >= math.ceil(target * self.minimum_word_ratio),
        }
        asset = self.store.insert_production_asset(
            user_id, item_id, asset_type="script_draft", payload=payload)
        if not asset:
            raise ProductionAssetNotFound("Production item not found.")
        self.store.add_production_event(user_id, item_id,
            event_type="production_asset_assembled",
            note=f"script_draft v{asset['version']} assembled deterministically")
        return {"asset": asset}

    def _approved_script(self, user_id: int, item_id: int) -> dict[str, Any]:
        rows = self.store.list_production_assets(
            user_id, item_id, asset_type="script_draft", status="approved")
        if not rows:
            raise ProductionAssetError("Approve a specific Script Draft version first.")
        return rows[0]

    @staticmethod
    def _object_with(value: Any, fields: tuple[str, ...]) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise ValueError("response must be an object")
        for field in fields:
            if field not in value or value[field] in (None, "", []):
                raise ValueError(f"{field} is required")
        return value

    @staticmethod
    def _visual(value: Any) -> dict[str, Any]:
        value = ProductionAssetService._object_with(value, ("scenes",))
        if not isinstance(value["scenes"], list) or not value["scenes"]:
            raise ValueError("scenes must be a non-empty array")
        for scene in value["scenes"]:
            required = (
                "section", "scene", "purpose", "visual_description",
                "source_strategy", "duration", "rights_notes",
            )
            if not isinstance(scene, dict) or any(
                scene.get(field) in (None, "") for field in required
            ):
                raise ValueError("every scene requires complete planning fields")
            if scene.get("source_strategy") not in SAFE_VISUAL_STRATEGIES:
                raise ValueError("every scene requires a rights-safe source_strategy")
        if any(phrase in _json_text(value).casefold() for phrase in PROHIBITED_REUSE):
            raise ValueError("prohibited competitor-media reuse strategy")
        return value

    @staticmethod
    def _voice(value: Any) -> dict[str, Any]:
        value = ProductionAssetService._object_with(
            value,
            (
                "language", "voice_character", "tone", "pace", "energy",
            ),
        )
        if "pronunciation_guidance" not in value:
            raise ValueError("pronunciation_guidance is required")
        if not isinstance(value.get("section_delivery_notes"), list):
            raise ValueError("section_delivery_notes must be an array")
        if not isinstance(value.get("pause_emphasis_guidance"), list):
            raise ValueError("pause_emphasis_guidance must be an array")
        return value

    @staticmethod
    def _thumbnail(value: Any) -> dict[str, Any]:
        value = ProductionAssetService._object_with(
            value,
            (
                "core_idea", "main_subject", "composition", "visual_hierarchy",
                "emotion", "contrast_guidance", "text_suggestion", "brand_guidance",
                "forbidden_elements",
            ),
        )
        if not isinstance(value["forbidden_elements"], list):
            raise ValueError("forbidden_elements must be an array")
        return value

    @staticmethod
    def _metadata(value: Any) -> dict[str, Any]:
        value = ProductionAssetService._object_with(
            value,
            (
                "title_candidates", "recommended_title", "description", "hashtags",
                "tags", "chapter_suggestions", "cta", "search_intent",
                "keyword_alignment", "audience_alignment",
            ),
        )
        for field in ("title_candidates", "hashtags", "tags", "chapter_suggestions"):
            if not isinstance(value[field], list):
                raise ValueError(f"{field} must be an array")
        return value

    def _generate_downstream(self, user_id: int, item_id: int, asset_type: str,
                             requirements: str,
                             validator: Callable[[Any], dict[str, Any]]) -> dict[str, Any]:
        item, script = self._item(user_id, item_id), self._approved_script(user_id, item_id)
        job = self._new_job(user_id, item_id, asset_type)
        script_context = {
            key: script["payload"].get(key) for key in (
                "target_total_words", "actual_total_words", "target_duration_minutes",
                "estimated_duration_minutes", "completion_percentage",
            )
        }
        script_context["sections"] = []
        for source in script["payload"].get("source_section_versions") or []:
            row = self.store.get_production_asset(
                user_id, item_id, int(source.get("asset_id") or 0))
            if row:
                script_context["sections"].append({
                    "section_index": row["payload"].get("section_index"),
                    "title": row["payload"].get("title"),
                    "excerpt": str(row["payload"].get("content") or "")[:700],
                })

        def operation() -> dict[str, Any]:
            value = self._structured(
                system=("Create an original production planning artifact. Research is reference-only. "
                        "Return JSON only."),
                prompt=(requirements + "\nApproved script summary:\n" + _json_text(script_context)
                        + "\nProduction brief:\n" + _json_text(item["production_brief"])),
                num_predict=self.asset_num_predict, validator=validator)
            value.update({"schema_version": "cp7a-v1",
                "source_script_asset_id": script["id"],
                "source_script_version": script["version"]})
            asset = self.store.insert_production_asset(
                user_id, item_id, asset_type=asset_type, payload=value)
            if not asset:
                raise ProductionAssetNotFound("Production item not found.")
            self.store.add_production_event(user_id, item_id,
                event_type="production_asset_generated",
                note=f"{asset_type} v{asset['version']} created")
            return {"asset": asset}
        return self._run_job(user_id, item_id, job, operation)

    def generate_visual_plan(self, user_id: int, item_id: int) -> dict[str, Any]:
        return self._generate_downstream(
            user_id, item_id, "visual_plan",
            "Return {scenes:[{section,scene,purpose,visual_description,source_strategy,duration,"
            "overlay_text,transition,rights_notes}]}. source_strategy must be one of: "
            + ", ".join(sorted(SAFE_VISUAL_STRATEGIES)) + ".",
            self._visual)

    def generate_voice_plan(self, user_id: int, item_id: int) -> dict[str, Any]:
        return self._generate_downstream(
            user_id, item_id, "voice_plan",
            "Return {language,voice_character,tone,pace,energy,pronunciation_guidance,"
            "section_delivery_notes,pause_emphasis_guidance}. Planning only; do not synthesize audio.",
            self._voice)

    def generate_thumbnail_brief(self, user_id: int, item_id: int) -> dict[str, Any]:
        return self._generate_downstream(
            user_id, item_id, "thumbnail_brief",
            "Return {core_idea,main_subject,composition,visual_hierarchy,emotion,contrast_guidance,"
            "text_suggestion,brand_guidance,forbidden_elements}. Do not generate an image.",
            self._thumbnail)

    def generate_metadata_package(self, user_id: int, item_id: int) -> dict[str, Any]:
        return self._generate_downstream(
            user_id, item_id, "metadata_package",
            "Return {title_candidates,recommended_title,description,hashtags,tags,"
            "chapter_suggestions,cta,search_intent,keyword_alignment,audience_alignment}. "
            "Do not publish.",
            self._metadata)

    def _validate_draft_sources(self, user_id: int, item_id: int,
                                payload: dict[str, Any]) -> None:
        sources = payload.get("source_section_versions") or []
        blueprint = self.store.get_production_asset(
            user_id, item_id, int(payload.get("blueprint_asset_id") or 0)
        )
        if (not blueprint or blueprint["asset_type"] != "script_blueprint"
                or int(blueprint["version"]) != int(payload.get("blueprint_version") or 0)):
            raise ProductionAssetError("Script Draft references an invalid blueprint version.")
        if len(sources) != int(payload.get("section_count") or 0):
            raise ProductionAssetError("Script Draft source sections are incomplete.")
        expected_indices = [
            int(section.get("section_index") or 0)
            for section in blueprint["payload"].get("sections") or []
        ]
        source_indices = [int(source.get("section_index") or 0) for source in sources]
        if source_indices != expected_indices:
            raise ProductionAssetError("Script Draft source section selection is invalid.")
        for source in sources:
            row = self.store.get_production_asset(
                user_id, item_id, int(source.get("asset_id") or 0))
            if (not row or row["asset_type"] != "script_section"
                    or int(row["version"]) != int(source.get("version") or 0)
                    or int(row["payload"].get("section_index") or 0)
                        != int(source.get("section_index") or 0)
                    or int(row["payload"].get("blueprint_asset_id") or 0)
                        != int(blueprint["id"])
                    or not row["payload"].get("budget_acceptable")):
                raise ProductionAssetError("Script Draft references an invalid section version.")

    def _complete_task_for_asset(self, user_id: int, item_id: int,
                                 asset: dict[str, Any]) -> None:
        task_type = APPROVABLE_ASSETS[str(asset["asset_type"])]
        tasks = self.store.list_production_tasks(user_id, item_id)
        task = next((row for row in tasks if row["task_type"] == task_type), None)
        if not task:
            raise ProductionAssetError("Matching CP6 production task is missing.")
        if not self.queue._dependencies_satisfied(task, tasks):
            raise ProductionAssetError("Production task dependencies are not complete.")
        if task["status"] not in {"ready", "in_progress", "completed"}:
            raise ProductionAssetError("Production task is not ready for approval.")
        prior = str(task["status"])
        self.store.update_production_task(user_id, item_id, int(task["id"]), {
            "status": "completed", "completed_at": time.time(),
            "output_json": _json_text({"approved_asset_id": asset["id"],
                "asset_type": asset["asset_type"], "version": asset["version"]})})
        item = self.store.get_production_item(user_id, item_id)
        if item and item["status"] == "queued":
            self.store.update_production_item(
                user_id, item_id, {"status": "planning", "started_at": time.time()})
            self.store.add_production_event(user_id, item_id,
                event_type="item_status_changed", from_status="queued", to_status="planning",
                note="First CP7A asset approved.")
        if prior != "completed":
            self.store.add_production_event(user_id, item_id, event_type="task_completed",
                task_id=int(task["id"]), from_status=prior, to_status="completed",
                note=f"Approved {asset['asset_type']} v{asset['version']}")
        self.queue._recompute(user_id, item_id)

    def review_asset(self, user_id: int, item_id: int, asset_id: int, *,
                     decision: str, note: Optional[str] = None) -> dict[str, Any]:
        self._item(user_id, item_id)
        asset = self.get_asset(user_id, item_id, asset_id)
        if asset["asset_type"] not in APPROVABLE_ASSETS:
            raise ProductionAssetError("This asset type is not directly approvable.")
        if decision not in {"approved", "rejected"}:
            raise ProductionAssetError("Decision must be approved or rejected.")
        if decision == "approved" and asset["status"] != "review":
            raise ProductionAssetError(
                "Submit this exact asset version for review before approval."
            )
        if decision == "rejected" and asset["status"] not in {"draft", "review"}:
            raise ProductionAssetError(
                "Only draft or review asset versions can be rejected."
            )
        if decision == "approved" and asset["asset_type"] == "script_draft":
            if not asset["payload"].get("budget_acceptable"):
                raise ProductionAssetError("Script Draft does not meet the minimum word budget.")
            self._validate_draft_sources(user_id, item_id, asset["payload"])
        elif decision == "approved":
            script = self._approved_script(user_id, item_id)
            if (int(asset["payload"].get("source_script_asset_id") or 0) != int(script["id"])
                    or int(asset["payload"].get("source_script_version") or 0)
                        != int(script["version"])):
                raise ProductionAssetError(
                    "Production asset is not based on the current approved Script Draft version."
                )
        if decision == "approved":
            # Validate the CP6 gate before mutating the asset status.
            task_type = APPROVABLE_ASSETS[str(asset["asset_type"])]
            tasks = self.store.list_production_tasks(user_id, item_id)
            task = next((row for row in tasks if row["task_type"] == task_type), None)
            if not task or not self.queue._dependencies_satisfied(task, tasks):
                raise ProductionAssetError("Production task dependencies are not complete.")
            if task["status"] not in {"ready", "in_progress", "completed"}:
                raise ProductionAssetError("Production task is not ready for approval.")
        self.store.update_production_asset_status(user_id, item_id, asset_id, decision)
        if decision == "approved":
            self.store.supersede_other_production_assets(
                user_id, item_id, asset_type=asset["asset_type"],
                asset_key=str(asset["asset_key"]), keep_asset_id=asset_id)
            self._complete_task_for_asset(user_id, item_id, asset)
        self.store.add_production_event(user_id, item_id,
            event_type=("production_asset_approved" if decision == "approved"
                        else "production_asset_rejected"),
            from_status=str(asset["status"]), to_status=decision,
            note=(note or f"{asset['asset_type']} asset {asset['id']} v{asset['version']}")[:1000])
        return {"asset": self.get_asset(user_id, item_id, asset_id),
                "production_item": self.queue.get(user_id, item_id)}

    def submit_for_review(self, user_id: int, item_id: int, asset_id: int, *,
                          note: Optional[str] = None) -> dict[str, Any]:
        self._item(user_id, item_id)
        asset = self.get_asset(user_id, item_id, asset_id)
        if asset["asset_type"] not in APPROVABLE_ASSETS:
            raise ProductionAssetError("This asset type cannot be submitted for review.")
        if asset["status"] != "draft":
            raise ProductionAssetError("Only a draft asset version can enter review.")
        self.store.update_production_asset_status(
            user_id, item_id, asset_id, "review"
        )
        self.store.add_production_event(
            user_id, item_id, event_type="production_asset_review_requested",
            from_status="draft", to_status="review",
            note=(note or f"{asset['asset_type']} asset {asset['id']} v{asset['version']}")[:1000],
        )
        return {"asset": self.get_asset(user_id, item_id, asset_id),
                "production_item": self.queue.get(user_id, item_id)}

    def _approved_assets(self, user_id: int, item_id: int) -> dict[str, dict[str, Any]]:
        result = {}
        for asset_type in APPROVABLE_ASSETS:
            rows = self.store.list_production_assets(
                user_id, item_id, asset_type=asset_type, status="approved")
            if rows:
                result[asset_type] = rows[0]
        return result

    def inspect_qa(self, user_id: int, item_id: int, *,
                   complete_task: bool = False) -> dict[str, Any]:
        item = self._item(user_id, item_id)
        approved = self._approved_assets(user_id, item_id)
        reasons = []
        required = list(APPROVABLE_ASSETS)
        if item["target_format"] == "short_form":
            required.remove("thumbnail_brief")
        for asset_type in required:
            if asset_type not in approved:
                reasons.append(f"approved {asset_type} is missing")
        script = approved.get("script_draft")
        if script:
            if not script["payload"].get("budget_acceptable"):
                reasons.append("script word budget is below the minimum")
            try:
                self._validate_draft_sources(user_id, item_id, script["payload"])
            except ProductionAssetError as exc:
                reasons.append(str(exc))
        visual = approved.get("visual_plan")
        valid = {"visual_plan": False, "voice_plan": False,
                 "thumbnail_brief": item["target_format"] == "short_form",
                 "metadata_package": False}
        if visual:
            try:
                self._visual(visual["payload"])
            except ValueError as exc:
                reasons.append(str(exc))
            else:
                valid["visual_plan"] = True
        for asset_type, validator in (
            ("voice_plan", self._voice),
            ("thumbnail_brief", self._thumbnail),
            ("metadata_package", self._metadata),
        ):
            asset = approved.get(asset_type)
            if not asset:
                continue
            try:
                validator(asset["payload"])
            except ValueError as exc:
                reasons.append(f"{asset_type}: {exc}")
            else:
                valid[asset_type] = True
        if script:
            for asset_type in ("visual_plan", "voice_plan", "thumbnail_brief", "metadata_package"):
                asset = approved.get(asset_type)
                if asset and (
                    int(asset["payload"].get("source_script_asset_id") or 0) != int(script["id"])
                    or int(asset["payload"].get("source_script_version") or 0)
                        != int(script["version"])
                ):
                    reasons.append(
                        f"approved {asset_type} is based on a superseded Script Draft version"
                    )
        passed = not reasons
        tasks = self.store.list_production_tasks(user_id, item_id)
        qa_task = next((row for row in tasks if row["task_type"] == "QA"), None)
        if complete_task and passed and qa_task:
            if not self.queue._dependencies_satisfied(qa_task, tasks):
                reasons.append("QA task dependencies are not complete")
                passed = False
            elif qa_task["status"] in {"ready", "in_progress"}:
                self.store.update_production_task(user_id, item_id, int(qa_task["id"]), {
                    "status": "completed", "completed_at": time.time(),
                    "output_json": _json_text({"cp7a_qa": "approved"})})
                self.store.add_production_event(user_id, item_id,
                    event_type="cp7a_qa_completed", task_id=int(qa_task["id"]),
                    from_status=str(qa_task["status"]), to_status="completed",
                    note="Deterministic CP7A asset QA passed.")
                self.queue._recompute(user_id, item_id)
        return {"status": "approved" if passed else "failed", "passed": passed,
            "reasons": reasons, "checks": {
                "approved_script": "script_draft" in approved,
                "word_budget": bool(script and script["payload"].get("budget_acceptable")),
                "required_sections": bool(
                    script and not any("section" in reason.casefold() for reason in reasons)),
                "visual_plan_safe": valid["visual_plan"],
                "voice_plan": valid["voice_plan"],
                "thumbnail_brief": valid["thumbnail_brief"],
                "metadata_package": valid["metadata_package"]}}

    def package(self, user_id: int, item_id: int) -> dict[str, Any]:
        item = self._item(user_id, item_id)
        approved, qa = self._approved_assets(user_id, item_id), self.inspect_qa(user_id, item_id)
        required = list(APPROVABLE_ASSETS)
        if item["target_format"] == "short_form":
            required.remove("thumbnail_brief")
        return {"production_item_id": item_id,
            "planning_ready": bool(item["planning_ready"]),
            "asset_ready": all(kind in approved for kind in required) and qa["passed"],
            "qa_status": qa["status"], "rights_ready": bool(item["rights_ready"]),
            "rights_gate": item["rights_gate_status"],
            "approved_assets": {kind: {"asset_id": row["id"], "version": row["version"]}
                                for kind, row in approved.items()},
            "reasons": qa["reasons"]}
