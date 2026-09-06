"""CP9 owner-scoped YouTube performance feedback and bounded learning."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
import re
import statistics
import time
from typing import Any, Optional, Protocol

from .providers import AIProvider, OllamaProviderError
from .youtube import (
    GoogleOAuthTokenService, YouTubeAnalyticsUnavailableError,
    YouTubeReadOnlyError, YouTubeReadOnlyService, _values_by_header,
)
from universal_video_ai.web.store import Store

METRICS = (
    "views", "impressions", "impressions_ctr", "watch_time_minutes",
    "average_view_duration_seconds", "average_view_percentage", "likes",
    "comments", "subscribers_gained", "subscribers_lost",
)
SIGNAL_DIMENSIONS = (
    "topic_strength", "hook_strength", "title_strength", "thumbnail_strength",
    "retention_strength", "pacing_strength", "length_fit", "audience_fit", "search_fit",
)
RECOMMENDATION_CATEGORIES = {
    "topic", "hook", "title", "thumbnail", "script", "pacing", "length", "metadata",
}


class FeedbackLearningError(RuntimeError):
    pass


class FeedbackLearningNotFound(FeedbackLearningError):
    pass


class PerformanceProvider(Protocol):
    def fetch(self, user_id: int, job: dict[str, Any]) -> dict[str, Any]: ...


def _iso_timestamp(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def _duration_seconds(value: Any) -> Optional[float]:
    match = re.fullmatch(r"P(?:(\d+)D)?T(?:(\d+)H)?(?:(\d+)M)?(?:([\d.]+)S)?", str(value or ""))
    if not match:
        return None
    days, hours, minutes, seconds = (float(part or 0) for part in match.groups())
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def _safe_number(value: Any, *, integer: bool = False) -> Optional[float | int]:
    if value in (None, ""):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(result) or result < 0:
        return None
    return int(result) if integer else result


def _row_map(headers: list[str], rows: list[list[Any]]) -> dict[str, Any]:
    return _values_by_header(headers, rows[0]) if rows else {}


class YouTubePerformanceProvider:
    """Official Data/Analytics API adapter reusing CP1 OAuth and errors."""

    AGGREGATE = (
        "views,estimatedMinutesWatched,averageViewDuration,averageViewPercentage,"
        "subscribersGained,subscribersLost,likes,comments"
    )
    PACKAGING = "videoThumbnailImpressions,videoThumbnailImpressionsClickRate"

    def __init__(self, youtube: YouTubeReadOnlyService) -> None:
        self.youtube = youtube

    def _breakdown(self, user_id: int, start: datetime, end: datetime, video_id: str,
                   dimension: str, metrics: str = "views") -> Optional[list[dict[str, Any]]]:
        try:
            headers, rows = self.youtube._analytics_report(
                user_id, start.date(), end.date(), metrics=metrics, dimensions=dimension,
                filters=f"video=={video_id}", sort="-views", maxResults=25)
        except YouTubeReadOnlyError:
            return None
        return [_values_by_header(headers, row) for row in rows]

    def fetch(self, user_id: int, job: dict[str, Any]) -> dict[str, Any]:
        video_id = str(job["external_video_id"])
        metadata = self.youtube.data_request(user_id, "videos", {
            "part": "snippet,contentDetails,status", "id": video_id,
            "fields": "items(id,snippet(channelId,publishedAt),contentDetails(duration),status/privacyStatus)",
        })
        items = metadata.get("items")
        if not isinstance(items, list):
            raise FeedbackLearningError("YouTube returned an invalid video response.")
        if not items:
            raise FeedbackLearningError("The linked YouTube video is deleted or unavailable.")
        item = items[0] if isinstance(items[0], dict) else {}
        if str(item.get("id") or "") != video_id:
            raise FeedbackLearningError("YouTube returned a different video identity.")
        snippet = item.get("snippet") if isinstance(item.get("snippet"), dict) else {}
        if str(snippet.get("channelId") or "") != str(job["channel_id"]):
            raise FeedbackLearningError("The linked video is not owned by the publishing channel.")
        published = _iso_timestamp(snippet.get("publishedAt")) or _iso_timestamp(job.get("published_at"))
        start = datetime.fromtimestamp(published or time.time(), timezone.utc)
        end = datetime.now(timezone.utc)
        headers, rows = self.youtube._analytics_report(
            user_id, start.date(), end.date(), metrics=self.AGGREGATE,
            filters=f"video=={video_id}")
        raw = _row_map(headers, rows)
        impressions = ctr = None
        try:
            package_headers, package_rows = self.youtube._analytics_report(
                user_id, start.date(), end.date(), metrics=self.PACKAGING,
                filters=f"video=={video_id}")
            packaging = _row_map(package_headers, package_rows)
            impressions = packaging.get("videoThumbnailImpressions")
            ctr = packaging.get("videoThumbnailImpressionsClickRate")
        except YouTubeAnalyticsUnavailableError:
            pass
        retention = self._breakdown(
            user_id, start, end, video_id, "elapsedVideoTimeRatio", "audienceWatchRatio")
        points = None if retention is None else [
            {"elapsed_ratio": _safe_number(row.get("elapsedVideoTimeRatio")),
             "retention_ratio": _safe_number(row.get("audienceWatchRatio"))}
            for row in retention
            if _safe_number(row.get("elapsedVideoTimeRatio")) is not None
            and _safe_number(row.get("audienceWatchRatio")) is not None
        ]
        return {
            "youtube_video_id": video_id,
            "channel_id": snippet.get("channelId"),
            "video_published_at": published,
            "duration_seconds": _duration_seconds(
                (item.get("contentDetails") or {}).get("duration")),
            "privacy": (item.get("status") or {}).get("privacyStatus"),
            "views": raw.get("views"),
            "impressions": impressions,
            "impressions_ctr": ctr,
            "watch_time_minutes": raw.get("estimatedMinutesWatched"),
            "average_view_duration_seconds": raw.get("averageViewDuration"),
            "average_view_percentage": raw.get("averageViewPercentage"),
            "likes": raw.get("likes"), "comments": raw.get("comments"),
            "subscribers_gained": raw.get("subscribersGained"),
            "subscribers_lost": raw.get("subscribersLost"),
            "retention_available": bool(points),
            "retention": points or [],
            "traffic_sources": self._breakdown(
                user_id, start, end, video_id, "insightTrafficSourceType",
                "views,estimatedMinutesWatched"),
            "device_types": self._breakdown(user_id, start, end, video_id, "deviceType"),
            "countries": self._breakdown(user_id, start, end, video_id, "country"),
            "audience_segments": None,
            "playlist_sources": self._breakdown(user_id, start, end, video_id, "playlist"),
        }


class FeedbackLearningService:
    MIN_IMPRESSIONS = 100
    MIN_VIEWS = 20
    BASELINE_MIN_SAMPLES = 3
    PROFILE_HALF_LIFE_DAYS = 180.0

    def __init__(self, store: Store, provider: Optional[PerformanceProvider] = None,
                 llm: Optional[AIProvider] = None, *, now: Any = time.time) -> None:
        self.store, self.llm, self.now = store, llm, now
        self.provider = provider or YouTubePerformanceProvider(
            YouTubeReadOnlyService(GoogleOAuthTokenService(store)))

    def _job(self, user_id: int, item_id: int, job_id: int) -> dict[str, Any]:
        if not self.store.get_production_item(user_id, item_id):
            raise FeedbackLearningNotFound("Production item not found.")
        job = self.store.get_production_publishing_job(
            user_id, item_id, job_id, private=True)
        if not job or not job.get("external_video_id"):
            raise FeedbackLearningNotFound("Published YouTube video linkage not found.")
        account = self.store.get_social_account(user_id, "youtube")
        if not account or not account["account_ref"]:
            raise FeedbackLearningError("Connected YouTube channel identity is unavailable.")
        if str(account["account_ref"]) != str(job.get("channel_id")):
            raise FeedbackLearningError("Publishing channel no longer matches the owned account.")
        return job

    @staticmethod
    def _duration_bucket(seconds: Optional[float]) -> Optional[str]:
        if seconds is None:
            return None
        return "short" if seconds <= 60 else ("medium" if seconds <= 1200 else "long")

    def refresh(self, user_id: int, item_id: int, job_id: int, *,
                evaluation_window_hours: Optional[float] = None) -> dict[str, Any]:
        job = self._job(user_id, item_id, job_id)
        item = self.store.get_production_item(user_id, item_id) or {}
        payload = self.provider.fetch(user_id, job)
        if str(payload.get("youtube_video_id")) != str(job["external_video_id"]):
            raise FeedbackLearningError("Analytics video identity did not match the publishing job.")
        if str(payload.get("channel_id")) != str(job["channel_id"]):
            raise FeedbackLearningError("Analytics channel identity did not match the publishing job.")
        captured = float(self.now())
        published = _iso_timestamp(payload.get("video_published_at"))
        if published is None:
            published = _iso_timestamp(job.get("published_at"))
        age = max(0.0, (captured - published) / 3600.0) if published else 0.0
        window = age if evaluation_window_hours is None else float(evaluation_window_hours)
        if not math.isfinite(window) or window <= 0 or window > 24 * 3650:
            raise FeedbackLearningError("Evaluation window must be positive and bounded.")
        duration = _safe_number(payload.get("duration_seconds"))
        data: dict[str, Any] = {
            "channel_id": str(job["channel_id"]), "production_item_id": item_id,
            "publishing_job_id": job_id, "youtube_video_id": str(job["external_video_id"]),
            "captured_at": captured, "video_published_at": published,
            "video_age_hours": round(age, 4), "evaluation_window_hours": round(window, 4),
            "content_type": item.get("target_format"), "duration_seconds": duration,
            "duration_bucket": self._duration_bucket(duration),
            "retention_available": bool(payload.get("retention_available")),
            "retention": payload.get("retention") or [],
            "traffic_sources": payload.get("traffic_sources"),
            "device_types": payload.get("device_types"), "countries": payload.get("countries"),
            "audience_segments": payload.get("audience_segments"),
            "playlist_sources": payload.get("playlist_sources"),
            "data_quality": {
                "api_lag_possible": age < 48,
                "newly_published": age < 6,
                "privacy": payload.get("privacy"),
                "missing_metrics": [name for name in METRICS if payload.get(name) is None],
            },
        }
        for name in METRICS:
            data[name] = _safe_number(
                payload.get(name), integer=name in {
                    "views", "impressions", "likes", "comments",
                    "subscribers_gained", "subscribers_lost"})
        snapshot = self.store.insert_video_performance_snapshot(user_id, data)
        evaluation = self.evaluate(user_id, snapshot)
        signals = self.store.replace_content_learning_signals(
            user_id, snapshot, self.learning_signals(snapshot, evaluation))
        return {"snapshot": snapshot, "evaluation": evaluation, "signals": signals}

    def snapshots(self, user_id: int, item_id: int,
                  job_id: int) -> list[dict[str, Any]]:
        self._job(user_id, item_id, job_id)
        return self.store.list_video_performance_snapshots(
            user_id, publishing_job_id=job_id)

    def latest(self, user_id: int, item_id: int, job_id: int) -> dict[str, Any]:
        rows = self.snapshots(user_id, item_id, job_id)
        if not rows:
            raise FeedbackLearningNotFound("No performance snapshot has been captured.")
        return {"snapshot": rows[0], "evaluation": self.evaluate(user_id, rows[0]),
                "signals": self.store.list_content_learning_signals(user_id, rows[0]["id"])}

    @staticmethod
    def _derived(snapshot: dict[str, Any]) -> dict[str, Optional[float]]:
        age = max(float(snapshot.get("video_age_hours") or 0), 0.01)
        views = snapshot.get("views")
        interactions = None
        if views and snapshot.get("likes") is not None and snapshot.get("comments") is not None:
            interactions = (snapshot["likes"] + snapshot["comments"]) / views
        return {
            "ctr": snapshot.get("impressions_ctr"),
            "watch_pct": snapshot.get("average_view_percentage"),
            "view_velocity": None if views is None else views / age,
            "engagement": interactions,
        }

    def _baseline(self, user_id: int, snapshot: dict[str, Any]) -> dict[str, Any]:
        rows = self.store.list_video_performance_snapshots(
            user_id, channel_id=snapshot["channel_id"], limit=2000)
        candidates = [row for row in rows
            if row["youtube_video_id"] != snapshot["youtube_video_id"]
            and row["captured_at"] >= snapshot["captured_at"] - 365 * 86400]
        age = float(snapshot["video_age_hours"])
        age_tolerance = max(6.0, age * .35)
        candidates = [row for row in candidates
                      if abs(float(row["video_age_hours"]) - age) <= age_tolerance]
        strict = [row for row in candidates
                  if row.get("content_type") == snapshot.get("content_type")
                  and row.get("duration_bucket") == snapshot.get("duration_bucket")]
        selected, matching = (strict, "age_content_duration") if len(strict) >= 3 else (
            candidates, "age_window")
        latest_by_video: dict[str, dict[str, Any]] = {}
        for row in sorted(selected, key=lambda value: abs(float(value["video_age_hours"]) - age)):
            latest_by_video.setdefault(str(row["youtube_video_id"]), row)
        selected = list(latest_by_video.values())
        values: dict[str, Optional[float]] = {}
        for metric in ("ctr", "watch_pct", "view_velocity", "engagement"):
            available = [value for row in selected
                         if (value := self._derived(row).get(metric)) is not None]
            values[metric] = statistics.median(available) if available else None
        return {"sample_count": len(selected), "matching": matching,
                "age_tolerance_hours": round(age_tolerance, 2), "metrics": values,
                "sufficient": len(selected) >= self.BASELINE_MIN_SAMPLES}

    def evaluate(self, user_id: int, snapshot: dict[str, Any]) -> dict[str, Any]:
        baseline, current = self._baseline(user_id, snapshot), self._derived(snapshot)
        normalized: dict[str, Optional[float]] = {}
        for key, value in current.items():
            reference = baseline["metrics"].get(key)
            normalized[key + "_vs_baseline"] = (
                None if value is None or reference in (None, 0)
                else round(float(value) / float(reference), 4))
        impressions, views = snapshot.get("impressions"), snapshot.get("views")
        enough_click = impressions is not None and impressions >= self.MIN_IMPRESSIONS
        enough_watch = views is not None and views >= self.MIN_VIEWS
        enough_time = float(snapshot["video_age_hours"]) >= 1
        sufficient = enough_time and (enough_click or enough_watch)
        confidence = min(1.0, (
            .2 + (.25 if enough_click else 0) + (.25 if enough_watch else 0)
            + (.2 if baseline["sufficient"] else 0)
            + (.1 if snapshot.get("retention_available") else 0)))
        dimensions = {
            "reach": {"value": current["view_velocity"],
                      "vs_baseline": normalized["view_velocity_vs_baseline"],
                      "status": "available" if views is not None else "unavailable"},
            "click": {"value": snapshot.get("impressions_ctr"),
                      "vs_baseline": normalized["ctr_vs_baseline"],
                      "status": "available" if enough_click else "insufficient_data"},
            "watch": {"average_view_duration_seconds": snapshot.get(
                          "average_view_duration_seconds"),
                      "average_view_percentage": snapshot.get("average_view_percentage"),
                      "watch_time_minutes": snapshot.get("watch_time_minutes"),
                      "vs_baseline": normalized["watch_pct_vs_baseline"],
                      "status": "available" if enough_watch else "insufficient_data"},
            "engagement": {"value": current["engagement"],
                           "vs_baseline": normalized["engagement_vs_baseline"],
                           "status": "available" if enough_watch and current["engagement"] is not None
                                     else "insufficient_data"},
            "channel_impact": {"gained": snapshot.get("subscribers_gained"),
                               "lost": snapshot.get("subscribers_lost"),
                               "net": (None if snapshot.get("subscribers_gained") is None
                                       or snapshot.get("subscribers_lost") is None
                                       else snapshot["subscribers_gained"]
                                            - snapshot["subscribers_lost"]),
                               "status": "available" if snapshot.get("subscribers_gained") is not None
                                         or snapshot.get("subscribers_lost") is not None
                                         else "unavailable"},
        }
        return {"status": "EVALUATED" if sufficient else "INSUFFICIENT_DATA",
                "confidence": round(confidence if sufficient else min(confidence, .45), 3),
                "baseline": baseline, "normalized": normalized, "dimensions": dimensions}

    @staticmethod
    def _relative_status(value: Optional[float]) -> str:
        if value is None:
            return "INSUFFICIENT_DATA"
        return "above_baseline" if value >= 1.15 else (
            "below_baseline" if value <= .85 else "near_baseline")

    def _heuristics(self, snapshot: dict[str, Any],
                    evaluation: dict[str, Any]) -> list[dict[str, Any]]:
        if evaluation["status"] == "INSUFFICIENT_DATA":
            return [{"id": "insufficient_data", "likelihood": "low-confidence",
                     "finding": "The current sample is too small or too new for a reliable cause.",
                     "evidence": ["video_age_hours", "views", "impressions"]}]
        ratios = evaluation["normalized"]
        ctr, watch, velocity = (ratios.get("ctr_vs_baseline"),
                                ratios.get("watch_pct_vs_baseline"),
                                ratios.get("view_velocity_vs_baseline"))
        findings: list[dict[str, Any]] = []
        if velocity is not None and velocity >= 1.15 and ctr is not None and ctr <= .85:
            findings.append({"id": "likely_packaging_issue", "likelihood": "likely",
                "finding": "Strong reach with below-baseline CTR suggests a possible packaging issue.",
                "evidence": ["view_velocity_vs_baseline", "ctr_vs_baseline"]})
        if ctr is not None and ctr >= 1.15 and watch is not None and watch <= .85:
            findings.append({"id": "possible_content_mismatch", "likelihood": "possible",
                "finding": "Good CTR with weaker viewing suggests a hook, pacing, or promise mismatch.",
                "evidence": ["ctr_vs_baseline", "watch_pct_vs_baseline"]})
        distribution_limited = (
            velocity is not None and velocity <= .85
            and ctr is not None and ctr >= 1.15
            and watch is not None and watch >= 1.15
        )
        if distribution_limited:
            findings.append({"id": "possible_distribution_limit", "likelihood": "possible",
                "finding": "Good click and watch response with low reach may indicate topic/distribution limits.",
                "evidence": ["view_velocity_vs_baseline", "ctr_vs_baseline",
                             "watch_pct_vs_baseline"]})
        points = snapshot.get("retention") or []
        if len(points) >= 3 and points[0]["retention_ratio"] >= .65:
            drops = [points[index - 1]["retention_ratio"] - points[index]["retention_ratio"]
                     for index in range(1, len(points))]
            if max(drops, default=0) >= .2:
                findings.append({"id": "possible_late_pacing_drop", "likelihood": "possible",
                    "finding": "Relatively strong early retention followed by a sharp drop may mark a pacing issue.",
                    "evidence": ["retention_points"]})
        return findings or [{"id": "no_clear_root_cause", "likelihood": "low-confidence",
            "finding": "Available indicators do not isolate a clear root cause.",
            "evidence": [key for key, value in ratios.items() if value is not None]}]

    def learning_signals(self, snapshot: dict[str, Any],
                         evaluation: dict[str, Any]) -> list[dict[str, Any]]:
        ratios = evaluation["normalized"]
        mapping = {
            "topic_strength": ("view_velocity_vs_baseline",),
            "hook_strength": ("watch_pct_vs_baseline", "ctr_vs_baseline"),
            "title_strength": ("ctr_vs_baseline", "view_velocity_vs_baseline"),
            "thumbnail_strength": ("ctr_vs_baseline", "impressions"),
            "retention_strength": ("watch_pct_vs_baseline",),
            "pacing_strength": ("watch_pct_vs_baseline", "retention_points"),
            "length_fit": ("watch_pct_vs_baseline", "average_view_duration_seconds"),
            "audience_fit": ("engagement_vs_baseline", "watch_pct_vs_baseline"),
            "search_fit": ("view_velocity_vs_baseline", "traffic_sources"),
        }
        results = []
        for dimension, sources in mapping.items():
            ratio_keys = [key for key in sources if key in ratios and ratios[key] is not None]
            score = (statistics.mean(ratios[key] for key in ratio_keys)
                     if ratio_keys else None)
            confidence = evaluation["confidence"] * (
                .8 if len(ratio_keys) > 1 else .65 if ratio_keys else .25)
            results.append({"dimension": dimension,
                "status": self._relative_status(score),
                "score": None if score is None else round(max(0, min(2, score)), 4),
                "confidence": round(confidence, 3),
                "evidence": self._heuristics(snapshot, evaluation),
                "source_metrics": list(sources)})
        return results

    @staticmethod
    def _recommendations(findings: list[dict[str, Any]],
                         confidence: float) -> list[dict[str, Any]]:
        categories: list[tuple[str, str]]
        ids = {row["id"] for row in findings}
        if "likely_packaging_issue" in ids:
            categories = [
                ("title", "Test a truthful alternative title structure on a future video."),
                ("thumbnail", "Test a clearer future thumbnail hierarchy without changing the promise."),
            ]
        elif "possible_content_mismatch" in ids:
            categories = [
                ("hook", "Align the opening more directly with the approved title promise."),
                ("pacing", "Move proof or payoff earlier in a future script."),
            ]
        elif "possible_distribution_limit" in ids:
            categories = [
                ("topic", "Retain the effective treatment but test a broader adjacent topic."),
                ("metadata", "Clarify future search intent without overstating the content."),
            ]
        elif "possible_late_pacing_drop" in ids:
            categories = [("pacing", "Review the drop section and tighten similar future sections.")]
        else:
            categories = [("script", "Collect another age-matched sample before making a structural change.")]
        evidence = sorted({evidence for row in findings for evidence in row["evidence"]})
        return [{
            "id": hashlib.sha256(f"{category}:{reason}".encode()).hexdigest()[:12],
            "category": category, "reason": reason, "evidence": evidence,
            "confidence": round(confidence * (.85 if len(findings) == 1 else .75), 3),
            "applicable_future_context": "New, similar owner-channel productions only.",
            "review_status": "pending",
        } for category, reason in categories]

    @staticmethod
    def _llm_validator(value: Any, evidence_ids: set[str]) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise ValueError("learning analysis must be an object")
        result: dict[str, Any] = {}
        for field in ("strengths", "weaknesses", "likely_causes"):
            rows = value.get(field, [])
            if not isinstance(rows, list) or any(not isinstance(row, str) for row in rows):
                raise ValueError(f"{field} must be a string array")
            if any(re.search(r"\d|%", row) for row in rows):
                raise ValueError("LLM narrative must not introduce numeric metric claims")
            result[field] = [row[:1000] for row in rows[:8]]
        recommendations = value.get("recommendations", [])
        if not isinstance(recommendations, list):
            raise ValueError("recommendations must be an array")
        clean = []
        for row in recommendations[:12]:
            if not isinstance(row, dict) or row.get("category") not in RECOMMENDATION_CATEGORIES:
                raise ValueError("unsupported recommendation")
            evidence = row.get("evidence")
            if not isinstance(evidence, list) or any(str(item) not in evidence_ids for item in evidence):
                raise ValueError("recommendation contains ungrounded evidence")
            if re.search(r"\d|%", str(row.get("reason") or "")):
                raise ValueError("LLM recommendation must not introduce numeric metric claims")
            clean.append({"category": row["category"],
                "reason": str(row.get("reason") or "")[:1000],
                "evidence": [str(item) for item in evidence],
                "applicable_future_context": str(
                    row.get("applicable_future_context") or "")[:1000]})
        result["recommendations"] = clean
        return result

    def _llm_analysis(self, user_id: int, snapshot: dict[str, Any],
                      evaluation: dict[str, Any],
                      findings: list[dict[str, Any]]) -> tuple[str, Optional[dict[str, Any]]]:
        if self.llm is None:
            return "offline", None
        item = self.store.get_production_item(
            user_id, int(snapshot["production_item_id"])) or {}
        assets = self.store.list_production_assets(
            user_id, int(snapshot["production_item_id"]))
        approved = {row["asset_type"]: row["payload"] for row in assets
                    if row["status"] == "approved"
                    and row["asset_type"] in {
                        "script_blueprint", "script_draft", "metadata_package",
                        "thumbnail_brief"}}
        evidence = {
            "production_brief": item.get("production_brief"),
            "approved_assets": approved,
            "metrics": {key: snapshot.get(key) for key in METRICS},
            "retention": snapshot.get("retention") if snapshot.get("retention_available") else None,
            "baseline": evaluation["baseline"], "normalized": evaluation["normalized"],
            "heuristics": findings,
        }
        evidence_ids = set(METRICS) | set(evaluation["normalized"]) | {
            "retention_points", "video_age_hours"} | {row["id"] for row in findings}
        prompt = (
            "Analyze only the supplied evidence. Do not invent or restate numeric metrics. "
            "Return JSON {strengths:[string],weaknesses:[string],likely_causes:[string],"
            "recommendations:[{category,reason,evidence:[evidence_id],applicable_future_context}]}. "
            "Evidence IDs must come from allowed_evidence_ids. Distinguish likely from proven.\n"
            + json.dumps({"allowed_evidence_ids": sorted(evidence_ids),
                          "evidence": evidence}, ensure_ascii=False, sort_keys=True))
        try:
            raw = self.llm.generate_structured(
                system_prompt="Produce cautious, grounded channel learning. JSON only.",
                user_prompt=prompt, temperature=.1, top_p=.8, num_predict=1800)
            if isinstance(raw, str):
                raw = json.loads(raw)
            return "completed", self._llm_validator(raw, evidence_ids)
        except (OllamaProviderError, ValueError, TypeError, json.JSONDecodeError):
            return "offline", None

    def generate_report(self, user_id: int, item_id: int,
                        job_id: int) -> dict[str, Any]:
        latest = self.latest(user_id, item_id, job_id)
        snapshot, evaluation = latest["snapshot"], latest["evaluation"]
        findings = self._heuristics(snapshot, evaluation)
        deterministic = self._recommendations(findings, evaluation["confidence"])
        llm_status, llm = self._llm_analysis(user_id, snapshot, evaluation, findings)
        recommendations = list(deterministic)
        if llm:
            for row in llm["recommendations"]:
                key = json.dumps(row, sort_keys=True, ensure_ascii=False)
                row = {**row, "id": hashlib.sha256(key.encode()).hexdigest()[:12],
                       "confidence": evaluation["confidence"], "review_status": "pending"}
                recommendations.append(row)
        report = self.store.insert_video_learning_report(user_id, {
            "channel_id": snapshot["channel_id"], "production_item_id": item_id,
            "publishing_job_id": job_id, "youtube_video_id": snapshot["youtube_video_id"],
            "snapshot_id": snapshot["id"],
            "evaluation_window_hours": snapshot["evaluation_window_hours"],
            "status": evaluation["status"],
            "performance_summary": {
                "metrics": {key: snapshot.get(key) for key in METRICS},
                "video_age_hours": snapshot["video_age_hours"],
                "baseline": evaluation["baseline"], "normalized": evaluation["normalized"],
                "dimensions": evaluation["dimensions"],
            },
            "strengths": (llm or {}).get("strengths", [
                key for key, value in evaluation["normalized"].items()
                if value is not None and value >= 1.15]),
            "weaknesses": (llm or {}).get("weaknesses", [
                key for key, value in evaluation["normalized"].items()
                if value is not None and value <= .85]),
            "likely_causes": findings, "confidence": evaluation["confidence"],
            "recommendations": recommendations, "llm_status": llm_status,
            "llm_analysis": llm, "created_at": float(self.now()),
        })
        self.rebuild_profile(user_id, snapshot["channel_id"])
        return report

    def reports(self, user_id: int, item_id: int,
                job_id: int) -> list[dict[str, Any]]:
        self._job(user_id, item_id, job_id)
        return self.store.list_video_learning_reports(
            user_id, publishing_job_id=job_id)

    @staticmethod
    def recency_weight(created_at: float, now: float,
                       half_life_days: float = 180.0) -> float:
        age_days = max(0.0, (now - created_at) / 86400.0)
        return 2 ** (-age_days / max(1.0, half_life_days))

    @staticmethod
    def _title_style(title: str) -> str:
        value = title.strip().casefold()
        if "?" in value:
            return "question"
        if re.search(r"\d", value):
            return "number-led"
        if value.startswith(("how to ", "cach ", "lam sao ")):
            return "how-to"
        return "statement"

    def rebuild_profile(self, user_id: int, channel_id: str) -> dict[str, Any]:
        reports = self.store.list_video_learning_reports(
            user_id, channel_id=channel_id, limit=1000)
        current = float(self.now())
        topics: dict[str, list[dict[str, Any]]] = defaultdict(list)
        formats: dict[str, list[dict[str, Any]]] = defaultdict(list)
        durations: list[tuple[float, float, int]] = []
        hooks, retention, packaging = [], [], []
        applied: list[dict[str, Any]] = []
        evidence_refs = []
        for report in reports:
            item = self.store.get_production_item(
                user_id, int(report["production_item_id"])) or {}
            topic = str((item.get("production_brief") or {}).get("topic")
                        or item.get("working_title") or "Untitled")
            assets = self.store.list_production_assets(
                user_id, int(report["production_item_id"]))
            metadata = next((row["payload"] for row in assets
                             if row["asset_type"] == "metadata_package"
                             and row["status"] == "approved"), {})
            blueprint = next((row["payload"] for row in assets
                              if row["asset_type"] == "script_blueprint"
                              and row["status"] not in {"rejected", "superseded"}), {})
            first_section = (blueprint.get("sections") or [{}])[0]
            hook_pattern = str(first_section.get("purpose")
                               or first_section.get("title") or topic)[:240]
            title_style = self._title_style(str(
                metadata.get("recommended_title") or item.get("working_title") or ""))
            weight = self.recency_weight(
                float(report["created_at"]), current, self.PROFILE_HALF_LIFE_DAYS)
            ratios = report["performance_summary"].get("normalized") or {}
            available = [float(value) for value in ratios.values() if value is not None]
            score = statistics.mean(available) if available else None
            topics[topic].append({"score": score, "weight": weight, "report_id": report["id"]})
            formats[str(item.get("target_format") or "unspecified")].append(
                {"score": score, "weight": weight, "report_id": report["id"]})
            snapshot = self.store.get_video_performance_snapshot(
                user_id, int(report["snapshot_id"]))
            if snapshot and snapshot.get("duration_seconds") and score is not None:
                durations.append((float(snapshot["duration_seconds"]), weight, report["id"]))
            if ratios.get("watch_pct_vs_baseline") is not None:
                target = hooks if ratios["watch_pct_vs_baseline"] >= 1.15 else retention
                target.append({"pattern": hook_pattern, "score": ratios["watch_pct_vs_baseline"],
                               "confidence": report["confidence"], "recency_weight": round(weight, 4),
                               "evidence_refs": [f"report:{report['id']}"]})
            if ratios.get("ctr_vs_baseline") is not None:
                packaging.append({"pattern": title_style, "score": ratios["ctr_vs_baseline"],
                                  "confidence": report["confidence"],
                                  "recency_weight": round(weight, 4),
                                  "evidence_refs": [f"report:{report['id']}"]})
            for recommendation in report["recommendations"]:
                if recommendation.get("review_status") == "applied":
                    applied.append({key: recommendation.get(key) for key in (
                        "id", "category", "reason", "confidence",
                        "applicable_future_context")})
            evidence_refs.append(f"report:{report['id']}")

        summarized = []
        for topic, rows in topics.items():
            scored = [row for row in rows if row["score"] is not None]
            total_weight = sum(row["weight"] for row in scored)
            weighted = (sum(row["score"] * row["weight"] for row in scored) / total_weight
                        if total_weight else None)
            summarized.append({"topic": topic,
                "weighted_score": None if weighted is None else round(weighted, 4),
                "sample_count": len(rows),
                "confidence": round(min(1.0, .25 + .15 * len(rows)), 3),
                "recency_weight": round(sum(row["weight"] for row in rows), 4),
                "evidence_refs": [f"report:{row['report_id']}" for row in rows]})
        winning = sorted([row for row in summarized
                          if row["weighted_score"] is not None and row["weighted_score"] >= 1.05],
                         key=lambda row: -row["weighted_score"])[:10]
        under = sorted([row for row in summarized
                        if row["weighted_score"] is not None and row["weighted_score"] < .95],
                       key=lambda row: row["weighted_score"])[:10]
        preferred = None
        if durations:
            expanded = sorted(durations)
            preferred = {"min_seconds": round(expanded[0][0]),
                         "max_seconds": round(expanded[-1][0]),
                         "sample_count": len(expanded),
                         "evidence_refs": [f"report:{row[2]}" for row in expanded]}
        format_performance = []
        for name, rows in formats.items():
            scored = [row for row in rows if row["score"] is not None]
            total = sum(row["weight"] for row in scored)
            value = (sum(row["score"] * row["weight"] for row in scored) / total
                     if total else None)
            format_performance.append({
                "format": name,
                "weighted_score": None if value is None else round(value, 4),
                "sample_count": len(rows),
                "evidence_refs": [f"report:{row['report_id']}" for row in rows],
            })
        patterns = {
            "winning_topics": winning, "underperforming_topics": under,
            "strong_hook_patterns": sorted(hooks, key=lambda row: -row["score"])[:10],
            "weak_retention_patterns": sorted(retention, key=lambda row: row["score"])[:10],
            "title_packaging_trends": sorted(packaging, key=lambda row: -row["score"])[:10],
            "preferred_duration_range": preferred,
            "format_performance": format_performance,
            "audience_response_patterns": [],
            "applied_recommendations": applied[:30],
        }
        return self.store.upsert_channel_learning_profile(user_id, channel_id, {
            "patterns": patterns, "sample_count": len(reports),
            "evidence_refs": evidence_refs,
            "decay_half_life_days": self.PROFILE_HALF_LIFE_DAYS,
            "generated_at": current})

    def profile(self, user_id: int) -> dict[str, Any]:
        account = self.store.get_social_account(user_id, "youtube")
        channel_id = str(account["account_ref"]) if account and account["account_ref"] else None
        if not channel_id:
            raise FeedbackLearningError("Connected YouTube channel identity is unavailable.")
        profile = self.store.get_channel_learning_profile(user_id, channel_id)
        if profile:
            return profile
        return self.rebuild_profile(user_id, channel_id)

    def review_recommendation(
        self, user_id: int, item_id: int, job_id: int, report_id: int,
        recommendation_id: str, action: str, note: Optional[str] = None
    ) -> dict[str, Any]:
        job = self._job(user_id, item_id, job_id)
        report = self.store.get_video_learning_report(user_id, report_id)
        if not report or report["publishing_job_id"] != job_id:
            raise FeedbackLearningNotFound("Learning report not found.")
        try:
            updated = self.store.set_learning_recommendation_action(
                user_id, report_id, recommendation_id, action, note)
        except ValueError as exc:
            raise FeedbackLearningError(str(exc)) from exc
        if not updated:
            raise FeedbackLearningNotFound("Learning report not found.")
        self.rebuild_profile(user_id, str(job["channel_id"]))
        return updated

    def item_summary(self, user_id: int, item_id: int) -> dict[str, Any]:
        if not self.store.get_production_item(user_id, item_id):
            raise FeedbackLearningNotFound("Production item not found.")
        jobs = self.store.list_production_publishing_jobs(user_id, item_id)
        results = []
        for job in jobs:
            if not job.get("external_video_id"):
                continue
            snapshots = self.store.list_video_performance_snapshots(
                user_id, publishing_job_id=job["id"], limit=1)
            reports = self.store.list_video_learning_reports(
                user_id, publishing_job_id=job["id"], limit=1)
            results.append({"publishing_job_id": job["id"],
                            "youtube_video_id": job["external_video_id"],
                            "snapshot": snapshots[0] if snapshots else None,
                            "evaluation": self.evaluate(user_id, snapshots[0]) if snapshots else None,
                            "report": reports[0] if reports else None})
        return {"items": results}
