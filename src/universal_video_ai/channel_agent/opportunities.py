"""CP5 deterministic Content Opportunity decision management.

This module consumes normalized CP2/CP3/CP4 evidence.  It never calls an AI
provider, downloads media, or creates production work.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from typing import Any, Optional

from .brain import ContentEvidenceAssembler, EvidenceSelectionError, evidence_hash
from .competitors import MAX_COMPETITORS, opportunity_gaps
from .trends import trend_min_relevance


STATUSES = frozenset({"draft", "watch", "approved", "rejected", "archived"})
SOURCE_TYPES = frozenset({"candidate", "gap", "brain_run"})
CONFIDENCE_LEVELS = frozenset({"low", "medium", "high"})
COMPETITION_LEVELS = frozenset({"low", "medium", "high", "unknown"})
TARGET_FORMATS = frozenset({"long_form", "short_form", "all", "unspecified"})
RIGHTS_STATUSES = frozenset({"idea_only", "unknown", "licensed", "owned", "permitted"})
REJECTION_REASONS = frozenset({
    "low_evidence", "wrong_niche", "too_competitive", "rights_concern",
    "duplicate_idea", "weak_differentiation", "other",
})
TRANSITIONS = {
    "draft": frozenset({"watch", "approved", "rejected"}),
    "watch": frozenset({"draft", "approved", "rejected"}),
    "approved": frozenset({"archived"}),
    "rejected": frozenset({"draft"}),
    "archived": frozenset(),
}


class OpportunityError(RuntimeError):
    pass


class OpportunityNotFound(OpportunityError):
    pass


def gap_key(pattern: str) -> str:
    return "gap:" + hashlib.sha256(str(pattern).encode("utf-8")).hexdigest()[:12]


def _number(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return min(1.0, max(0.0, float(value)))
    except (TypeError, ValueError):
        return None


def _bounded_text(value: Any, maximum: int) -> Optional[str]:
    text = " ".join(str(value or "").split())
    return text[:maximum] or None


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _first(rows: Any) -> dict[str, Any]:
    return rows[0] if isinstance(rows, list) and rows and isinstance(rows[0], dict) else {}


class ContentOpportunityService:
    """Build and manage user-scoped opportunity cards without requiring Ollama."""

    SCORE_WEIGHTS = {
        "trend": 20.0,
        "niche_relevance": 20.0,
        "candidate_strength": 15.0,
        "competitor_evidence": 15.0,
        "pattern_gap_quality": 15.0,
        "evidence_confidence": 15.0,
    }
    CONFIDENCE_FACTORS = {"low": 0.72, "medium": 0.88, "high": 1.0}
    COMPETITION_FACTORS = {"low": 1.05, "medium": 1.0, "high": 0.95, "unknown": 0.95}
    FRESHNESS_FACTORS = {"fresh": 1.0, "aging": 0.92, "stale": 0.82}

    def __init__(self, store: Any) -> None:
        self.store = store
        self.assembler = ContentEvidenceAssembler(store, max_items=18, max_chars=23_000)

    def create(
        self, user_id: int, *, source_type: str, source_id: str,
        allow_low_confidence: bool = False,
    ) -> tuple[dict[str, Any], bool]:
        if source_type not in SOURCE_TYPES:
            raise OpportunityError("Unsupported opportunity source type.")
        bundle, refs = self._resolve_source(
            user_id, source_type, source_id, allow_low_confidence=allow_low_confidence
        )
        dedupe_key = self._dedupe_key(bundle, refs)
        existing = self.store.get_content_opportunity_by_dedupe(user_id, dedupe_key)
        if existing:
            if source_type == "brain_run":
                enrichment = self._find_enrichment(user_id, bundle, refs)
                derived = self._derive(bundle, refs, enrichment)
                derived["brain_run_id"] = enrichment.get("brain_run_id") or refs.get("brain_run_id")
                self.store.update_content_opportunity(user_id, int(existing["id"]), derived)
                self.store.add_content_opportunity_event(
                    user_id, int(existing["id"]), event_type="cp4_enrichment_linked",
                    from_status=existing["status"], to_status=existing["status"],
                )
                existing = self.store.get_content_opportunity(user_id, int(existing["id"])) or existing
            return self._with_events(user_id, existing), False
        enrichment = self._find_enrichment(user_id, bundle, refs)
        data = self._derive(bundle, refs, enrichment)
        data.update({
            "status": "draft",
            "source_type": source_type,
            "source_key": str(source_id),
            "dedupe_key": dedupe_key,
            "source_candidate_id": refs.get("candidate_id"),
            "source_gap_key": refs.get("gap_key"),
            "brain_run_id": enrichment.get("brain_run_id") or refs.get("brain_run_id"),
            "working_title": None,
            "selected_angle": None,
            "notes": None,
            "priority": 0,
            "target_format": "long_form",
            "target_duration_min": 60,
            "target_duration_max": 90,
            "rejection_reason": None,
            "rejection_note": None,
        })
        try:
            opportunity_id = self.store.insert_content_opportunity(user_id, data)
        except sqlite3.IntegrityError:
            existing = self.store.get_content_opportunity_by_dedupe(user_id, dedupe_key)
            if existing:
                return self._with_events(user_id, existing), False
            raise
        return self.get(user_id, opportunity_id), True

    def generate(self, user_id: int, *, limit: int = 5) -> dict[str, Any]:
        requested = min(20, max(1, int(limit)))
        candidates = self.store.list_trend_candidates(
            user_id, limit=200, min_relevance=trend_min_relevance(), include_filtered=False,
        )
        competitors = self.store.list_competitors(
            user_id, limit=MAX_COMPETITORS, include_filtered=True,
        )
        gaps = opportunity_gaps(competitors, candidates, include_filtered=False)
        candidate_sources = [("candidate", str(row["id"])) for row in candidates]
        gap_sources = [("gap", gap_key(str(row.get("pattern") or ""))) for row in gaps]
        sources: list[tuple[str, str]] = []
        for index in range(max(len(candidate_sources), len(gap_sources))):
            if index < len(candidate_sources):
                sources.append(candidate_sources[index])
            if index < len(gap_sources):
                sources.append(gap_sources[index])
        created: list[dict[str, Any]] = []
        existing: list[dict[str, Any]] = []
        skipped: list[str] = []
        for source_type, source_id in sources:
            if len(created) + len(existing) >= requested:
                break
            try:
                row, was_created = self.create(user_id, source_type=source_type, source_id=source_id)
            except (OpportunityError, EvidenceSelectionError) as exc:
                skipped.append(str(exc))
                continue
            (created if was_created else existing).append(row)
        return {"created": created, "existing": existing, "skipped": skipped[:10], "requested_limit": requested}

    def list(self, user_id: int, **filters: Any) -> list[dict[str, Any]]:
        limit = min(100, max(1, int(filters.pop("limit", 20))))
        rows = self.store.list_content_opportunities(user_id, limit=100, **filters)
        for row in rows:
            row["opportunity_rank_score"] = self._current_rank(row)
        rows.sort(key=lambda row: (-float(row["opportunity_rank_score"]), -float(row.get("updated_at") or 0), -int(row["id"])))
        return rows[:limit]

    def get(self, user_id: int, opportunity_id: int) -> dict[str, Any]:
        row = self.store.get_content_opportunity(user_id, opportunity_id)
        if not row:
            raise OpportunityNotFound("Content opportunity not found.")
        return self._with_events(user_id, row)

    def edit(self, user_id: int, opportunity_id: int, **changes: Any) -> dict[str, Any]:
        self.get(user_id, opportunity_id)
        clean: dict[str, Any] = {}
        limits = {"working_title": 300, "selected_angle": 1500, "notes": 5000}
        for field, maximum in limits.items():
            if field in changes:
                clean[field] = _bounded_text(changes[field], maximum)
        if "priority" in changes:
            clean["priority"] = min(100, max(0, int(changes["priority"])))
        if "target_format" in changes:
            if changes["target_format"] not in TARGET_FORMATS:
                raise OpportunityError("Unsupported target format.")
            clean["target_format"] = changes["target_format"]
        minimum = changes.get("target_duration_min")
        maximum = changes.get("target_duration_max")
        if minimum is not None:
            clean["target_duration_min"] = min(600, max(1, int(minimum)))
        if maximum is not None:
            clean["target_duration_max"] = min(600, max(1, int(maximum)))
        merged_min = clean.get("target_duration_min", self.get(user_id, opportunity_id).get("target_duration_min"))
        merged_max = clean.get("target_duration_max", self.get(user_id, opportunity_id).get("target_duration_max"))
        if merged_min is not None and merged_max is not None and merged_min > merged_max:
            raise OpportunityError("Minimum duration must not exceed maximum duration.")
        self.store.update_content_opportunity(user_id, opportunity_id, clean)
        return self.get(user_id, opportunity_id)

    def change_status(
        self, user_id: int, opportunity_id: int, *, status: str,
        rejection_reason: Optional[str] = None, note: Optional[str] = None,
    ) -> dict[str, Any]:
        row = self.get(user_id, opportunity_id)
        current = str(row["status"])
        if status not in STATUSES:
            raise OpportunityError("Unsupported opportunity status.")
        if status not in TRANSITIONS[current]:
            raise OpportunityError(f"Invalid opportunity transition: {current} → {status}.")
        if status == "rejected" and rejection_reason and rejection_reason not in REJECTION_REASONS:
            raise OpportunityError("Unsupported rejection reason.")
        changes = {
            "status": status,
            "rejection_reason": rejection_reason if status == "rejected" else None,
            "rejection_note": _bounded_text(note, 1000) if status == "rejected" else None,
        }
        self.store.update_content_opportunity(user_id, opportunity_id, changes)
        self.store.add_content_opportunity_event(
            user_id, opportunity_id, event_type="status_changed", from_status=current,
            to_status=status, note=_bounded_text(note, 1000),
        )
        return self.get(user_id, opportunity_id)

    def refresh(self, user_id: int, opportunity_id: int) -> dict[str, Any]:
        row = self.get(user_id, opportunity_id)
        selector_type: Optional[str] = None
        selector_id: Optional[str] = None
        if row.get("source_candidate_id"):
            selector_type, selector_id = "candidate", str(row["source_candidate_id"])
        elif row.get("source_gap_key"):
            selector_type, selector_id = "gap", str(row["source_gap_key"])
        if not selector_type:
            raise OpportunityError("This opportunity has no refreshable CP2/CP3 source.")
        bundle = self.assembler.assemble(
            user_id, selector_type=selector_type, selector_id=selector_id,
            allow_low_confidence=False,
        )
        refs = self._refs_from_bundle(bundle)
        enrichment = self._find_enrichment(user_id, bundle, refs)
        derived = self._derive(bundle, refs, enrichment)
        derived["brain_run_id"] = enrichment.get("brain_run_id") or row.get("brain_run_id")
        derived["last_refreshed_at"] = time.time()
        self.store.update_content_opportunity(user_id, opportunity_id, derived)
        self.store.add_content_opportunity_event(
            user_id, opportunity_id, event_type="refreshed", from_status=row["status"],
            to_status=row["status"],
        )
        return self.get(user_id, opportunity_id)

    def delete(self, user_id: int, opportunity_id: int) -> None:
        if not self.store.delete_content_opportunity(user_id, opportunity_id):
            raise OpportunityNotFound("Content opportunity not found.")

    def _resolve_source(
        self, user_id: int, source_type: str, source_id: str, *, allow_low_confidence: bool,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        if len(str(source_id)) > 240:
            raise OpportunityError("Opportunity source identifier is too long.")
        if source_type == "brain_run":
            try:
                run_id = int(source_id)
            except ValueError as exc:
                raise OpportunityError("Content Brain run not found.") from exc
            run = self.store.get_content_brain_run(user_id, run_id)
            if not run or run.get("status") != "completed" or not isinstance(run.get("result"), dict):
                raise OpportunityError("Completed Content Brain run not found.")
            bundle = run.get("evidence") or {}
            if not isinstance(bundle, dict) or not bundle.get("schema_version"):
                raise OpportunityError("Content Brain run has no usable evidence snapshot.")
            refs = self._refs_from_bundle(bundle)
            refs["brain_run_id"] = run_id
            refs["requested_brain_run"] = run
            return bundle, refs
        selector_type = "candidate" if source_type == "candidate" else "gap"
        try:
            bundle = self.assembler.assemble(
                user_id, selector_type=selector_type, selector_id=str(source_id),
                allow_low_confidence=allow_low_confidence,
            )
        except EvidenceSelectionError as exc:
            raise OpportunityError(str(exc)) from exc
        return bundle, self._refs_from_bundle(bundle)

    @staticmethod
    def _refs_from_bundle(bundle: dict[str, Any]) -> dict[str, Any]:
        selector = bundle.get("selector") or {}
        selector_id = str(selector.get("id") or "")
        refs: dict[str, Any] = {"selector_id": selector_id}
        if selector_id.startswith("candidate:"):
            try:
                refs["candidate_id"] = int(selector_id.split(":", 1)[1])
            except ValueError:
                pass
        if selector_id.startswith("gap:"):
            refs["gap_key"] = selector_id
        return refs

    @staticmethod
    def _dedupe_key(bundle: dict[str, Any], refs: dict[str, Any]) -> str:
        if refs.get("candidate_id"):
            return f"candidate:{refs['candidate_id']}"
        if refs.get("gap_key"):
            return str(refs["gap_key"])
        return "evidence:" + evidence_hash(bundle)

    def _find_enrichment(
        self, user_id: int, bundle: dict[str, Any], refs: dict[str, Any]
    ) -> dict[str, Any]:
        requested = refs.get("requested_brain_run")
        selector_id = str((bundle.get("selector") or {}).get("id") or "")
        digest = evidence_hash(bundle)
        runs: list[dict[str, Any]] = []
        if requested:
            runs.append(requested)
        for summary in self.store.list_content_brain_runs(user_id, 100):
            if summary.get("status") != "completed":
                continue
            full = self.store.get_content_brain_run(user_id, int(summary["id"]))
            if not full or not isinstance(full.get("result"), dict):
                continue
            run_selector = str(((full.get("evidence") or {}).get("selector") or {}).get("id") or "")
            if full.get("evidence_hash") == digest or (selector_id and run_selector == selector_id):
                if all(int(item["id"]) != int(full["id"]) for item in runs):
                    runs.append(full)
        enrichment: dict[str, Any] = {"ai_enrichment_status": "missing", "system_risks": []}
        for run in sorted(runs, key=lambda item: (float(item.get("created_at") or 0), int(item["id"]))):
            result = run["result"]
            enrichment["brain_run_id"] = int(run["id"])
            enrichment["ai_enrichment_status"] = "available"
            mode = run.get("request_type") or result.get("request_type")
            if mode == "opportunity_analysis":
                enrichment["system_differentiation"] = result.get("differentiation")
                enrichment["system_risks"] = list(result.get("risks") or [])[:10]
            elif mode == "content_angles":
                angle = _first(result.get("angles"))
                enrichment.update({
                    "system_recommended_angle": angle.get("angle_name"),
                    "system_audience_promise": angle.get("audience_promise"),
                    "system_core_conflict": angle.get("core_conflict"),
                    "system_differentiation": angle.get("differentiation"),
                })
                if angle.get("risk"):
                    enrichment["system_risks"] = [angle["risk"]]
            elif mode == "title_hooks":
                enrichment["system_suggested_title"] = _first(result.get("titles")).get("title")
                enrichment["system_suggested_hook"] = _first(result.get("hooks")).get("hook")
        return enrichment

    def _derive(
        self, bundle: dict[str, Any], refs: dict[str, Any], enrichment: dict[str, Any]
    ) -> dict[str, Any]:
        candidates = list(bundle.get("trend_candidates") or [])
        competitors = list(bundle.get("competitors") or [])
        gaps = list(bundle.get("opportunity_gaps") or [])
        patterns = list(bundle.get("patterns") or [])
        videos = list(bundle.get("breakout_videos") or [])
        selected_candidate = next(
            (row for row in candidates if row.get("candidate_id") == refs.get("candidate_id")),
            _first(candidates),
        )
        selected_gap = next(
            (row for row in gaps if row.get("evidence_id") == refs.get("gap_key")),
            _first(gaps),
        )
        confidence, waiting = self._confidence(bundle)
        competition = self._competition(competitors, selected_gap)
        competitor_strength = max(
            (_number(row.get("competitor_score")) or 0.0 for row in competitors), default=0.0
        ) if competitors else None
        breakout_count = len({str(row.get("video_id")) for row in videos if row.get("video_id")})
        breakout_count = max(breakout_count, int(selected_gap.get("supporting_breakout_count") or 0))
        pattern_quality = max(
            (_number(row.get("pattern_quality_score")) or 0.0 for row in patterns), default=0.0
        ) if patterns else None
        gap_quality = _number(selected_gap.get("gap_quality_score")) if selected_gap else None
        component_values = {
            "trend": _number(selected_candidate.get("trend_score")),
            "niche_relevance": _number(selected_candidate.get("niche_relevance_score")),
            "candidate_strength": _number(selected_candidate.get("opportunity_score")),
            "competitor_evidence": self._competitor_component(competitors, breakout_count),
            "pattern_gap_quality": max(
                [value for value in (pattern_quality, gap_quality) if value is not None],
                default=None,
            ),
            "evidence_confidence": {"low": 0.30, "medium": 0.65, "high": 1.0}[confidence],
        }
        breakdown, score = self._score(component_values)
        rank = min(100.0, max(0.0, score * self.CONFIDENCE_FACTORS[confidence]
                                      * self.COMPETITION_FACTORS[competition]))
        if refs.get("gap_key"):
            topic = selected_gap.get("pattern") or "Research opportunity"
        elif refs.get("candidate_id"):
            topic = selected_candidate.get("title") or "Research opportunity"
        else:
            topic = selected_gap.get("pattern") or selected_candidate.get("title") or "Research opportunity"
        motif = (selected_gap.get("pattern") or selected_candidate.get("matched_query")
                 or _first(patterns).get("pattern") or topic)
        rights = self._rights(bundle)
        risks = list(enrichment.get("system_risks") or [])[:10]
        risk_level = "high" if confidence == "low" and competition == "high" else (
            "medium" if confidence == "low" or risks or rights in {"idea_only", "unknown"} else "low"
        )
        snapshot = {
            "schema_version": "cp5-v1",
            "selector": bundle.get("selector") or {},
            "trend_candidates": candidates,
            "competitors": competitors,
            "opportunity_gaps": gaps,
            "patterns": patterns,
            "breakout_videos": videos,
            "own_channel": bundle.get("own_channel") or {},
            "rights_policy": bundle.get("rights_policy") or {},
            "evidence_confidence": bundle.get("evidence_confidence") or {},
        }
        return {
            "topic": _bounded_text(topic, 500) or "Research opportunity",
            "primary_motif": _bounded_text(motif, 300),
            "evidence_score": round(score, 2),
            "evidence_confidence": confidence,
            "opportunity_rank_score": round(rank, 2),
            "competition_level": competition,
            "trend_score": component_values["trend"],
            "niche_relevance_score": component_values["niche_relevance"],
            "candidate_opportunity_score": component_values["candidate_strength"],
            "competitor_strength_score": competitor_strength,
            "breakout_support_count": breakout_count,
            "pattern_quality_score": pattern_quality,
            "gap_quality_score": gap_quality,
            "ai_enrichment_status": enrichment.get("ai_enrichment_status", "missing"),
            "system_recommended_angle": _bounded_text(enrichment.get("system_recommended_angle"), 1500),
            "system_audience_promise": _bounded_text(enrichment.get("system_audience_promise"), 1500),
            "system_core_conflict": _bounded_text(enrichment.get("system_core_conflict"), 1500),
            "system_differentiation": _bounded_text(enrichment.get("system_differentiation"), 2000),
            "system_suggested_title": _bounded_text(enrichment.get("system_suggested_title"), 300),
            "system_suggested_hook": _bounded_text(enrichment.get("system_suggested_hook"), 1000),
            "system_risks_json": _json(risks),
            "rights_status": rights,
            "risk_level": risk_level,
            "evidence_hash": evidence_hash(snapshot),
            "evidence_snapshot_json": _json(snapshot),
            "score_breakdown_json": _json(breakdown),
            "waiting_for_json": _json(waiting),
        }

    @classmethod
    def _score(cls, values: dict[str, Optional[float]]) -> tuple[dict[str, Any], float]:
        available_weight = sum(cls.SCORE_WEIGHTS[key] for key, value in values.items() if value is not None)
        raw_points = sum(
            cls.SCORE_WEIGHTS[key] * float(value)
            for key, value in values.items() if value is not None
        )
        score = raw_points / available_weight * 100.0 if available_weight else 0.0
        scale = 100.0 / available_weight if available_weight else 0.0
        components = {
            key: {
                "weight": weight,
                "available": values[key] is not None,
                "signal": None if values[key] is None else round(float(values[key]), 4),
                "raw_points": round(weight * float(values[key]), 2) if values[key] is not None else None,
                "normalized_points": round(weight * float(values[key]) * scale, 2) if values[key] is not None else None,
            }
            for key, weight in cls.SCORE_WEIGHTS.items()
        }
        return {
            "formula": "available-weight normalized CP2/CP3 signals; LLM self-rating excluded",
            "available_weight": round(available_weight, 2),
            "components": components,
            "total": round(min(100.0, max(0.0, score)), 2),
        }, min(100.0, max(0.0, score))

    @staticmethod
    def _competitor_component(competitors: list[dict[str, Any]], breakout_count: int) -> Optional[float]:
        if not competitors and not breakout_count:
            return None
        strength = max((_number(row.get("competitor_score")) or 0.0 for row in competitors), default=0.0)
        return min(1.0, 0.45 * strength + 0.25 * min(1.0, len(competitors) / 3.0)
                   + 0.30 * min(1.0, breakout_count / 4.0))

    @staticmethod
    def _confidence(bundle: dict[str, Any]) -> tuple[str, list[str]]:
        candidates = list(bundle.get("trend_candidates") or [])
        competitors = list(bundle.get("competitors") or [])
        gaps = list(bundle.get("opportunity_gaps") or [])
        patterns = list(bundle.get("patterns") or [])
        videos = list(bundle.get("breakout_videos") or [])
        points = 0.0
        if any(row.get("observed_vph") is not None for row in candidates):
            points += 2.0
        elif any(row.get("approx_vph") is not None for row in candidates):
            points += 0.5
        if len(candidates) >= 2:
            points += 1.0
        points += 2.0 if len(competitors) >= 2 else (1.0 if competitors else 0.0)
        distinct_breakouts = len({row.get("video_id") for row in videos if row.get("video_id")})
        points += 2.0 if distinct_breakouts >= 2 else (1.0 if distinct_breakouts else 0.0)
        cross_channel = max(
            [int(row.get("supporting_competitor_count") or 0) for row in gaps]
            + [len(row.get("competitor_evidence_ids") or []) for row in patterns]
            + [0]
        )
        if cross_channel >= 2:
            points += 2.0
        own = bundle.get("own_channel") or {}
        if own.get("history_status") == "usable":
            points += 1.0
        cp4_score = _number((bundle.get("evidence_confidence") or {}).get("score"))
        if cp4_score is not None:
            points += 2.0 * cp4_score
        label = "high" if points >= 8.0 else ("medium" if points >= 4.0 else "low")
        waiting: list[str] = []
        if not any(row.get("observed_vph") is not None for row in candidates):
            waiting.append("observed VPH from a second snapshot")
        if len(competitors) < 2:
            waiting.append("qualified competitor evidence")
        if distinct_breakouts < 2:
            waiting.append("breakout confirmation")
        if cross_channel < 2:
            waiting.append("cross-channel pattern support")
        if own.get("history_status") != "usable":
            waiting.append("own-channel performance history")
        return label, waiting

    @staticmethod
    def _competition(competitors: list[dict[str, Any]], gap: dict[str, Any]) -> str:
        competitor_count = len(competitors)
        candidate_supply = int(gap.get("qualified_candidate_supply") or 0) if gap else 0
        if competitor_count < 2 and candidate_supply < 2:
            return "unknown"
        signal = (2 if competitor_count >= 4 else (1 if competitor_count >= 2 else 0))
        signal += 2 if candidate_supply >= 5 else (1 if candidate_supply >= 2 else 0)
        if int(gap.get("supporting_breakout_count") or 0) >= 4:
            signal += 1
        if int(gap.get("supporting_competitor_count") or 0) >= 3:
            signal += 1
        return "high" if signal >= 4 else ("medium" if signal >= 2 else "low")

    @staticmethod
    def _rights(bundle: dict[str, Any]) -> str:
        values = [
            str(row.get("rights_status") or "unknown")
            for section in ("trend_candidates", "breakout_videos")
            for row in (bundle.get(section) or [])
        ]
        if not values or "unknown" in values:
            return "unknown"
        if "idea_only" in values:
            return "idea_only"
        if all(value == "owned" for value in values):
            return "owned"
        if all(value in {"licensed", "owned", "permitted"} for value in values):
            return "permitted"
        return "unknown"

    def _with_events(self, user_id: int, row: dict[str, Any]) -> dict[str, Any]:
        result = dict(row)
        result["opportunity_rank_score"] = self._current_rank(result)
        result["events"] = self.store.list_content_opportunity_events(user_id, int(row["id"]))
        return result

    @classmethod
    def _current_rank(cls, row: dict[str, Any]) -> float:
        score = float(row.get("evidence_score") or 0.0)
        confidence = str(row.get("evidence_confidence") or "low")
        competition = str(row.get("competition_level") or "unknown")
        freshness = str(row.get("freshness_status") or "fresh")
        value = score * cls.CONFIDENCE_FACTORS.get(confidence, .72)
        value *= cls.COMPETITION_FACTORS.get(competition, .95)
        value *= cls.FRESHNESS_FACTORS.get(freshness, .82)
        return round(min(100.0, max(0.0, value)), 2)
