"""CP6 deterministic production planning queue.

Production items are traceable to approved CP5 opportunities.  This module
creates briefs and manual task plans only; it has no provider, media, legacy
job, rendering, TTS, uploader, or publishing dependency.
"""
from __future__ import annotations

import json
import sqlite3
import time
from typing import Any, Optional


ITEM_STATUSES = frozenset({
    "queued", "planning", "ready", "in_progress", "blocked", "completed", "cancelled",
})
ACTIVE_ITEM_STATUSES = frozenset({"queued", "planning", "ready", "in_progress", "blocked"})
ITEM_TRANSITIONS = {
    "queued": frozenset({"planning", "cancelled"}),
    "planning": frozenset({"ready", "blocked", "cancelled"}),
    "ready": frozenset({"in_progress", "blocked", "cancelled"}),
    "blocked": frozenset({"planning", "ready", "cancelled"}),
    "in_progress": frozenset({"completed", "blocked", "cancelled"}),
    "completed": frozenset(),
    "cancelled": frozenset(),
}
TASK_TYPES = ("SCRIPT", "VISUAL_PLAN", "VOICE_PLAN", "THUMBNAIL", "METADATA", "QA")
TASK_STATUSES = frozenset({"pending", "ready", "in_progress", "blocked", "completed", "skipped"})
RIGHTS_GATES = frozenset({"research_only", "needs_review", "cleared"})
BLOCKER_REASONS = frozenset({
    "missing_angle", "missing_title", "missing_target_duration", "rights_review_needed",
    "insufficient_brief", "manual_review_requested", "other",
})


class ProductionError(RuntimeError):
    pass


class ProductionNotFound(ProductionError):
    pass


def _text(value: Any, maximum: int) -> Optional[str]:
    normalized = " ".join(str(value or "").split())
    return normalized[:maximum] or None


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _evidence_references(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for section in ("trend_candidates", "competitors", "opportunity_gaps", "patterns", "breakout_videos"):
        for row in snapshot.get(section) or []:
            evidence_id = str(row.get("evidence_id") or "")
            if not evidence_id or evidence_id in seen:
                continue
            seen.add(evidence_id)
            result.append({
                "evidence_id": evidence_id,
                "type": section,
                "label": _text(
                    row.get("title") or row.get("channel_title") or row.get("pattern") or evidence_id,
                    300,
                ),
                "url": row.get("url") or row.get("channel_url"),
                "rights_status": row.get("rights_status"),
            })
    return result[:30]


class ProductionQueueService:
    def __init__(self, store: Any) -> None:
        self.store = store

    def create(self, user_id: int, opportunity_id: int) -> tuple[dict[str, Any], bool]:
        opportunity = self.store.get_content_opportunity(user_id, opportunity_id)
        if not opportunity:
            raise ProductionError("Approved Content Opportunity not found.")
        if opportunity.get("status") != "approved":
            raise ProductionError("Only an approved Content Opportunity can enter production planning.")
        existing = self.store.get_production_item_by_opportunity(user_id, opportunity_id)
        if existing:
            return self.get(user_id, int(existing["id"])), False
        brief = self.build_brief(opportunity)
        tasks = self.build_tasks(brief, str(opportunity.get("target_format") or "unspecified"))
        rights_status = str(opportunity.get("rights_status") or "unknown")
        rights_gate = self._initial_rights_gate(rights_status)
        data = {
            "opportunity_id": opportunity_id,
            "status": "queued",
            "priority": int(opportunity.get("priority") or 0),
            "opportunity_rank_score": float(opportunity.get("opportunity_rank_score") or 0),
            "working_title": brief["working_title"],
            "selected_angle": brief.get("selected_angle"),
            "target_format": brief["target_format"],
            "target_duration_min": brief.get("target_duration_min"),
            "target_duration_max": brief.get("target_duration_max"),
            "production_brief": brief,
            "rights_status": rights_status,
            "rights_gate_status": rights_gate,
            "planning_ready": self._brief_ready(brief) and len(tasks) == 6,
            "rights_ready": rights_gate == "cleared",
        }
        try:
            item_id = self.store.insert_production_item_with_tasks(user_id, data, tasks)
        except sqlite3.IntegrityError:
            existing = self.store.get_production_item_by_opportunity(user_id, opportunity_id)
            if existing:
                return self.get(user_id, int(existing["id"])), False
            raise
        return self.get(user_id, item_id), True

    @staticmethod
    def build_brief(opportunity: dict[str, Any]) -> dict[str, Any]:
        snapshot = opportunity.get("evidence_snapshot") or {}
        patterns = [
            str(row.get("pattern")) for row in snapshot.get("patterns") or [] if row.get("pattern")
        ]
        gaps = [
            str(row.get("pattern")) for row in snapshot.get("opportunity_gaps") or [] if row.get("pattern")
        ]
        title = (
            opportunity.get("working_title") or opportunity.get("system_suggested_title")
            or opportunity.get("topic") or "Untitled production plan"
        )
        angle = opportunity.get("selected_angle") or opportunity.get("system_recommended_angle")
        duration_min = opportunity.get("target_duration_min")
        duration_max = opportunity.get("target_duration_max")
        duration = (
            f"{duration_min}–{duration_max} minutes" if duration_min and duration_max
            else f"approximately {duration_min or duration_max} minutes" if duration_min or duration_max
            else "duration requires editorial decision"
        )
        evidence_refs = _evidence_references(snapshot)
        risks = list(opportunity.get("system_risks") or [])[:10]
        rights = str(opportunity.get("rights_status") or "unknown")
        primary_motif = opportunity.get("primary_motif") or (patterns + gaps + [None])[0]
        hook = opportunity.get("system_suggested_hook")
        return {
            "schema_version": "cp6-v1",
            "source_opportunity_id": int(opportunity["id"]),
            "source_evidence_hash": opportunity.get("evidence_hash"),
            "topic": _text(opportunity.get("topic"), 500),
            "working_title": _text(title, 300) or "Untitled production plan",
            "selected_angle": _text(angle, 1500),
            "audience_promise": _text(opportunity.get("system_audience_promise"), 1500),
            "core_conflict": _text(opportunity.get("system_core_conflict"), 1500),
            "differentiation": _text(opportunity.get("system_differentiation"), 2000),
            "hook_direction": _text(hook, 1000),
            "target_format": str(opportunity.get("target_format") or "unspecified"),
            "target_duration_min": duration_min,
            "target_duration_max": duration_max,
            "target_duration": duration,
            "primary_motif": _text(primary_motif, 300),
            "supporting_motifs": list(dict.fromkeys(patterns + gaps))[:8],
            "evidence_summary": {
                "evidence_score": opportunity.get("evidence_score"),
                "evidence_confidence": opportunity.get("evidence_confidence"),
                "competition_level": opportunity.get("competition_level"),
                "breakout_support_count": opportunity.get("breakout_support_count"),
                "references": evidence_refs,
            },
            "rights_status": rights,
            "rights_guidance": (
                "Research evidence is reference-only. Create an original Vietnamese script, commentary, "
                "and sequence, and use only owned, licensed, or permitted production visuals. "
                "Opportunity approval does not grant source-media reuse rights."
            ),
            "risk_summary": {"level": opportunity.get("risk_level"), "risks": risks},
            "script_direction": {
                "language": "Vietnamese",
                "instruction": "Develop an original structure from the approved angle; never copy a source transcript or plot wording.",
                "topic": _text(opportunity.get("topic"), 500),
                "angle": _text(angle, 1500),
                "audience_promise": _text(opportunity.get("system_audience_promise"), 1500),
                "conflict": _text(opportunity.get("system_core_conflict"), 1500),
                "hook": _text(hook, 1000),
                "differentiation": _text(opportunity.get("system_differentiation"), 2000),
            },
            "visual_direction": {
                "instruction": "Plan original, owned, licensed, or permitted visuals; evidence links are research references only.",
                "scene_categories": ["original narrative scenes", "licensed illustrations or diagrams", "original transitions and chapter cards"],
                "reuse_restrictions": ["no competitor download", "no mirroring/cropping source media", "no watermark or Content ID evasion"],
            },
            "voice_direction": {
                "language": "Vietnamese", "tone": "clear, immersive, and evidence-appropriate",
                "pace": "consistent with the approved format and target duration",
                "execution": "manual or later approved TTS execution; CP6 does not invoke TTS",
            },
            "thumbnail_direction": {
                "focal_motif": _text(primary_motif, 300),
                "tension": _text(opportunity.get("system_core_conflict"), 500),
                "text_concept": "Use one short truthful Vietnamese promise; avoid misleading or guaranteed-performance claims.",
            },
            "metadata_direction": {
                "working_title": _text(title, 300), "primary_motif": _text(primary_motif, 300),
                "secondary_terms": list(dict.fromkeys(patterns + gaps))[:8],
                "description": "Explain the original audience promise and chapters without keyword stuffing or viral guarantees.",
                "chapters": "Plan chapters from the final original structure.",
            },
        }

    @staticmethod
    def build_tasks(brief: dict[str, Any], target_format: str) -> list[dict[str, Any]]:
        task_data = [
            ("SCRIPT", "Script brief", [], True,
             "Plan the original Vietnamese script structure, hook, conflict, differentiation, runtime, evidence references, and rights constraints."),
            ("VISUAL_PLAN", "Visual plan", ["SCRIPT"], True,
             "Plan original/licensed/permitted scene categories and visual rhythm. Research videos remain reference-only."),
            ("VOICE_PLAN", "Voice plan", ["SCRIPT"], True,
             "Plan Vietnamese narration tone, pace, duration, pronunciation review, and manual/later TTS preference."),
            ("THUMBNAIL", "Thumbnail brief", ["SCRIPT"], target_format != "short_form",
             "Plan focal subject, truthful short text, motif, and emotional tension without generating an image."),
            ("METADATA", "Metadata and SEO brief", ["SCRIPT"], True,
             "Plan title direction, motifs, description, chapters, and restrained keyword guidance without performance guarantees."),
            ("QA", "Production QA checklist", ["SCRIPT", "VISUAL_PLAN", "VOICE_PLAN", "THUMBNAIL", "METADATA"], True,
             "Verify approved angle, original storytelling, rights-ready visuals, duration, audio, subtitles, thumbnail, and metadata. Do not publish."),
        ]
        tasks = []
        for order, (kind, title, dependencies, required, description) in enumerate(task_data, 1):
            tasks.append({
                "task_type": kind, "status": "ready" if not dependencies else "pending",
                "required": required, "title": title, "description": description,
                "depends_on": dependencies, "order_index": order, "assignee_type": "manual",
            })
        return tasks

    def list(self, user_id: int, **filters: Any) -> list[dict[str, Any]]:
        rows = self.store.list_production_items(user_id, **filters)
        for row in rows:
            tasks = self.store.list_production_tasks(user_id, int(row["id"]))
            row["progress"] = self._progress(tasks)
        return rows

    def get(self, user_id: int, item_id: int) -> dict[str, Any]:
        item = self.store.get_production_item(user_id, item_id)
        if not item:
            raise ProductionNotFound("Production item not found.")
        tasks = self.store.list_production_tasks(user_id, item_id)
        item["tasks"] = tasks
        item["progress"] = self._progress(tasks)
        item["events"] = self.store.list_production_events(user_id, item_id)
        opportunity = self.store.get_content_opportunity(user_id, int(item["opportunity_id"]))
        item["source_opportunity"] = self._opportunity_summary(opportunity)
        return item

    def edit(
        self, user_id: int, item_id: int, *, priority: Optional[int] = None,
        manual_notes: Optional[str] = None, rights_gate_status: Optional[str] = None,
        note: Optional[str] = None,
    ) -> dict[str, Any]:
        item = self.get(user_id, item_id)
        changes: dict[str, Any] = {}
        if priority is not None:
            changes["priority"] = min(100, max(0, int(priority)))
        if manual_notes is not None:
            changes["manual_notes"] = _text(manual_notes, 5000)
        if rights_gate_status is not None:
            if rights_gate_status not in RIGHTS_GATES:
                raise ProductionError("Unsupported rights gate status.")
            changes["rights_gate_status"] = rights_gate_status
            changes["rights_ready"] = int(rights_gate_status == "cleared")
            if rights_gate_status != item["rights_gate_status"]:
                self.store.add_production_event(
                    user_id, item_id, event_type="rights_status_changed",
                    from_status=item["rights_gate_status"], to_status=rights_gate_status,
                    note=_text(note, 1000),
                )
        self.store.update_production_item(user_id, item_id, changes)
        return self.get(user_id, item_id)

    def change_status(
        self, user_id: int, item_id: int, *, status: str,
        blocker_reason: Optional[str] = None, note: Optional[str] = None,
    ) -> dict[str, Any]:
        item = self.get(user_id, item_id)
        current = str(item["status"])
        if status not in ITEM_STATUSES or status not in ITEM_TRANSITIONS[current]:
            raise ProductionError(f"Invalid production transition: {current} → {status}.")
        if status == "blocked":
            if blocker_reason not in BLOCKER_REASONS:
                raise ProductionError("A supported blocker reason is required.")
        if status == "ready" and not item["planning_ready"]:
            raise ProductionError("Production planning requirements are not ready.")
        if status == "completed" and item["progress"]["percent"] != 100:
            raise ProductionError("All required planning tasks must be completed first.")
        now = time.time()
        changes = {
            "status": status,
            "blocker_reason": blocker_reason if status == "blocked" else None,
            "planning_ready": int(status != "blocked" and self._brief_ready(item["production_brief"])),
        }
        if status in {"planning", "in_progress"} and not item.get("started_at"):
            changes["started_at"] = now
        if status == "completed":
            changes["completed_at"] = now
        self.store.update_production_item(user_id, item_id, changes)
        self.store.add_production_event(
            user_id, item_id, event_type="item_status_changed", from_status=current,
            to_status=status, note=_text(note or blocker_reason, 1000),
        )
        return self.get(user_id, item_id)

    def edit_task(
        self, user_id: int, item_id: int, task_id: int, *,
        manual_notes: Optional[str] = None, output: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        self.get(user_id, item_id)
        task = self.store.get_production_task(user_id, item_id, task_id)
        if not task:
            raise ProductionNotFound("Production task not found.")
        changes: dict[str, Any] = {}
        if manual_notes is not None:
            changes["manual_notes"] = _text(manual_notes, 5000)
        if output is not None:
            encoded = _json(output)
            if len(encoded) > 20_000:
                raise ProductionError("Production task output is too large.")
            changes["output_json"] = encoded
        self.store.update_production_task(user_id, item_id, task_id, changes)
        return self.get(user_id, item_id)

    def change_task_status(
        self, user_id: int, item_id: int, task_id: int, *,
        status: str, note: Optional[str] = None,
    ) -> dict[str, Any]:
        item = self.get(user_id, item_id)
        if item["status"] in {"completed", "cancelled"}:
            raise ProductionError("Final production items cannot change tasks.")
        task = self.store.get_production_task(user_id, item_id, task_id)
        if not task:
            raise ProductionNotFound("Production task not found.")
        current = str(task["status"])
        allowed = {
            "pending": {"blocked", "skipped"},
            "ready": {"in_progress", "blocked", "skipped"},
            "in_progress": {"completed", "blocked"},
            "blocked": {"ready", "in_progress"},
            "completed": set(), "skipped": set(),
        }
        if status not in TASK_STATUSES or status not in allowed[current]:
            raise ProductionError(f"Invalid task transition: {current} → {status}.")
        if status == "skipped" and task["required"]:
            raise ProductionError("Required production tasks cannot be skipped.")
        if status in {"in_progress", "ready"} and not self._dependencies_satisfied(task, item["tasks"]):
            raise ProductionError("Production task dependencies are not complete.")
        changes = {"status": status, "completed_at": time.time() if status in {"completed", "skipped"} else None}
        self.store.update_production_task(user_id, item_id, task_id, changes)
        event = {
            "in_progress": "task_started", "completed": "task_completed",
            "blocked": "task_blocked", "skipped": "task_skipped", "ready": "task_unblocked",
        }[status]
        self.store.add_production_event(
            user_id, item_id, event_type=event, task_id=task_id,
            from_status=current, to_status=status, note=_text(note, 1000),
        )
        if status == "in_progress" and item["status"] == "queued":
            self.store.update_production_item(
                user_id, item_id, {"status": "planning", "started_at": time.time()}
            )
            self.store.add_production_event(
                user_id, item_id, event_type="item_status_changed",
                from_status="queued", to_status="planning", note="First planning task started.",
            )
        self._recompute(user_id, item_id)
        return self.get(user_id, item_id)

    def sync(self, user_id: int, item_id: int) -> dict[str, Any]:
        item = self.get(user_id, item_id)
        opportunity = self.store.get_content_opportunity(user_id, int(item["opportunity_id"]))
        if not opportunity:
            raise ProductionError("Source Content Opportunity is no longer available.")
        brief = self.build_brief(opportunity)
        rights_status = str(opportunity.get("rights_status") or "unknown")
        changes = {
            "working_title": brief["working_title"], "selected_angle": brief.get("selected_angle"),
            "target_format": brief["target_format"],
            "target_duration_min": brief.get("target_duration_min"),
            "target_duration_max": brief.get("target_duration_max"),
            "opportunity_rank_score": float(opportunity.get("opportunity_rank_score") or 0),
            "production_brief_json": _json(brief), "rights_status": rights_status,
            "planning_ready": int(self._brief_ready(brief) and item["status"] != "blocked"),
        }
        if item["rights_gate_status"] != "cleared" and rights_status in {"owned", "licensed", "permitted"}:
            changes["rights_gate_status"] = "cleared"
            changes["rights_ready"] = 1
        self.store.update_production_item(user_id, item_id, changes)
        self.store.add_production_event(
            user_id, item_id, event_type="brief_refreshed",
            from_status=item["status"], to_status=item["status"],
        )
        self.store.add_production_event(
            user_id, item_id, event_type="editorial_synced",
            from_status=item["status"], to_status=item["status"],
        )
        return self.get(user_id, item_id)

    def _recompute(self, user_id: int, item_id: int) -> None:
        item = self.store.get_production_item(user_id, item_id)
        if not item:
            return
        tasks = self.store.list_production_tasks(user_id, item_id)
        for task in tasks:
            if task["status"] == "pending" and self._dependencies_satisfied(task, tasks):
                self.store.update_production_task(
                    user_id, item_id, int(task["id"]), {"status": "ready"}
                )
        tasks = self.store.list_production_tasks(user_id, item_id)
        blocked = any(task["required"] and task["status"] == "blocked" for task in tasks)
        progress = self._progress(tasks)
        if item["status"] in {"completed", "cancelled"}:
            return
        changes: dict[str, Any] = {
            "planning_ready": int(not blocked and self._brief_ready(item["production_brief"]) and len(tasks) == 6),
        }
        if blocked and item["status"] != "blocked":
            changes.update({"status": "blocked", "blocker_reason": "manual_review_requested"})
            self.store.add_production_event(
                user_id, item_id, event_type="item_status_changed",
                from_status=item["status"], to_status="blocked", note="A required planning task is blocked.",
            )
        elif (
            not blocked and item["status"] == "blocked"
            and item.get("blocker_reason") == "manual_review_requested"
        ):
            changes.update({"status": "planning", "blocker_reason": None})
            self.store.add_production_event(
                user_id, item_id, event_type="item_status_changed",
                from_status="blocked", to_status="planning", note="Required task blocker cleared.",
            )
        if progress["percent"] == 100:
            changes.update({"status": "completed", "completed_at": time.time(), "blocker_reason": None})
            self.store.add_production_event(
                user_id, item_id, event_type="item_status_changed",
                from_status=item["status"], to_status="completed",
                note="All required planning tasks completed. No publishing action was run.",
            )
        self.store.update_production_item(user_id, item_id, changes)

    @staticmethod
    def _dependencies_satisfied(task: dict[str, Any], tasks: list[dict[str, Any]]) -> bool:
        states = {str(row["task_type"]): str(row["status"]) for row in tasks}
        return all(states.get(dep) in {"completed", "skipped"} for dep in task.get("depends_on") or [])

    @staticmethod
    def _progress(tasks: list[dict[str, Any]]) -> dict[str, Any]:
        required = [task for task in tasks if task.get("required")]
        completed = sum(task.get("status") == "completed" for task in required)
        total = len(required)
        return {
            "completed_required": completed, "total_required": total,
            "percent": round(completed / total * 100) if total else 0,
            "blocked_required": sum(task.get("status") == "blocked" for task in required),
        }

    @staticmethod
    def _brief_ready(brief: dict[str, Any]) -> bool:
        return bool(
            brief.get("working_title") and brief.get("target_format")
            and brief.get("rights_guidance") and brief.get("script_direction")
        )

    @staticmethod
    def _initial_rights_gate(rights_status: str) -> str:
        if rights_status in {"owned", "licensed", "permitted"}:
            return "cleared"
        return "research_only" if rights_status == "idea_only" else "needs_review"

    @staticmethod
    def _opportunity_summary(opportunity: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
        if not opportunity:
            return None
        return {
            key: opportunity.get(key) for key in (
                "id", "status", "topic", "working_title", "selected_angle", "evidence_score",
                "evidence_confidence", "opportunity_rank_score", "rights_status", "brain_run_id",
            )
        }
