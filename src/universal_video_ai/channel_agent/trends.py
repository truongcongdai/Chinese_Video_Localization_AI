"""Quota-conscious, metadata-only YouTube trend discovery for CP2."""

from __future__ import annotations

import json
import logging
import math
import os
import re
import threading
import time
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Any, Optional

from universal_video_ai.analytics.youtube_research import ResearchVideo
from universal_video_ai.channel_agent.analytics import (
    engagement_rate,
    outlier_ratio,
    trend_score,
    view_velocity,
)
from universal_video_ai.channel_agent.models import RightsStatus
from universal_video_ai.channel_agent.youtube import YouTubeReadOnlyService

logger = logging.getLogger(__name__)

MAX_QUERIES_PER_SCAN = min(10, max(1, int(os.environ.get("CHANNEL_AGENT_TREND_MAX_QUERIES", "5"))))
MAX_RESULTS_PER_QUERY = min(25, max(1, int(os.environ.get("CHANNEL_AGENT_TREND_RESULTS_PER_QUERY", "10"))))
MAX_ENRICHMENT_CHANNELS = min(10, max(0, int(os.environ.get("CHANNEL_AGENT_TREND_MAX_ENRICHMENT_CHANNELS", "5"))))
RECENT_BASELINE_VIDEOS = 10
DEFAULT_MIN_RELEVANCE = 0.55

_ISO_DURATION = re.compile(
    r"^P(?:(?P<days>\d+)D)?(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?)?$"
)


class TrendScanError(RuntimeError):
    pass


class TrendScanAlreadyRunning(TrendScanError):
    pass


@dataclass(frozen=True)
class SearchResult:
    videos: tuple[ResearchVideo, ...]
    uploads_playlists: dict[str, str]


@dataclass(frozen=True)
class CandidateScore:
    score: float
    confidence: str
    available_signal_count: int
    observed_vph: Optional[float]
    approx_vph: Optional[float]
    engagement: Optional[float]
    outlier: Optional[float]
    freshness: Optional[float]
    competition_proxy: Optional[float]
    explanations: tuple[str, ...]


@dataclass(frozen=True)
class RelevanceScore:
    score: float
    status: str
    match_reasons: tuple[str, ...]


def trend_min_relevance() -> float:
    try:
        value = float(os.environ.get("CHANNEL_AGENT_TREND_MIN_RELEVANCE", str(DEFAULT_MIN_RELEVANCE)))
    except (TypeError, ValueError):
        value = DEFAULT_MIN_RELEVANCE
    return min(1.0, max(0.0, value))


def _split_profile_terms(value: Any) -> tuple[str, ...]:
    if not value:
        return ()
    values = re.split(r"[,;|\n]+", str(value))
    return tuple(dict.fromkeys(term.strip() for term in values if term.strip()))


def normalize_relevance_text(value: Any) -> str:
    """NFKC/case/accent normalization without external tokenizers."""
    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    decomposed = unicodedata.normalize("NFKD", normalized)
    without_marks = "".join(char for char in decomposed if not unicodedata.combining(char))
    cleaned = "".join(
        char if (char.isalnum() or "\u3400" <= char <= "\u9fff") else " "
        for char in without_marks
    )
    return " ".join(cleaned.split())


def _contains_term(text: str, term: str) -> bool:
    needle = normalize_relevance_text(term)
    if not needle:
        return False
    if any("\u3400" <= char <= "\u9fff" for char in needle):
        return needle.replace(" ", "") in text.replace(" ", "")
    return needle in text


def _motif_terms(*values: Any) -> tuple[str, ...]:
    motifs: list[str] = []
    for value in values:
        normalized = normalize_relevance_text(value)
        motifs.extend(token for token in re.findall(r"[a-z0-9]+", normalized) if len(token) >= 3)
        for run in re.findall(r"[\u3400-\u9fff]+", normalized):
            if len(run) == 1:
                continue
            motifs.extend(run[index:index + 2] for index in range(len(run) - 1))
    return tuple(dict.fromkeys(motifs))


def score_niche_relevance(
    *,
    title: Any,
    description: Any,
    query: Any,
    topic_terms: Any = None,
    exclusion_terms: Any = None,
    minimum: Optional[float] = None,
) -> RelevanceScore:
    """Deterministic query/profile relevance; not semantic AI classification."""
    title_text = normalize_relevance_text(title)
    description_text = normalize_relevance_text(description)
    query_text = normalize_relevance_text(query)
    topics = _split_profile_terms(topic_terms)
    exclusions = _split_profile_terms(exclusion_terms)
    score = 0.0
    reasons: list[str] = []

    if query_text and _contains_term(title_text, query_text):
        score += 0.65
        reasons.append(str(query).strip())
    elif query_text and _contains_term(description_text, query_text):
        score += 0.20
        reasons.append(f"description: {str(query).strip()}")

    title_topics = [term for term in topics if _contains_term(title_text, term)]
    description_topics = [term for term in topics if term not in title_topics and _contains_term(description_text, term)]
    score += min(0.45, 0.18 * len(title_topics))
    score += min(0.18, 0.06 * len(description_topics))
    reasons.extend(title_topics)
    reasons.extend(f"description: {term}" for term in description_topics)

    motifs = _motif_terms(query, *topics)
    title_motifs = [term for term in motifs if _contains_term(title_text, term)]
    description_motifs = [
        term for term in motifs
        if term not in title_motifs and _contains_term(description_text, term)
    ]
    score += min(0.54, 0.18 * len(title_motifs))
    score += min(0.15, 0.05 * len(description_motifs))
    reasons.extend(title_motifs)
    reasons.extend(f"description: {term}" for term in description_motifs)

    excluded_title = [term for term in exclusions if _contains_term(title_text, term)]
    excluded_description = [
        term for term in exclusions
        if term not in excluded_title and _contains_term(description_text, term)
    ]
    penalty = min(0.80, 0.50 * len(excluded_title) + 0.15 * len(excluded_description))
    score = min(1.0, max(0.0, score - penalty))
    reasons.extend(f"excluded: {term}" for term in excluded_title + excluded_description)
    reasons = list(dict.fromkeys(reason for reason in reasons if reason))
    threshold = trend_min_relevance() if minimum is None else min(1.0, max(0.0, minimum))
    return RelevanceScore(
        score=score,
        status="relevant" if score >= threshold else "low_relevance",
        match_reasons=tuple(reasons),
    )


def opportunity_score(trend_value: Optional[float], relevance_value: Optional[float]) -> Optional[float]:
    if trend_value is None or relevance_value is None:
        return None
    value = max(0.0, float(trend_value)) * max(0.0, float(relevance_value))
    return min(1.0, value) if math.isfinite(value) else None


def parse_youtube_duration(value: Any) -> Optional[int]:
    if not isinstance(value, str):
        return None
    match = _ISO_DURATION.match(value)
    if not match:
        return None
    parts = {key: int(number or 0) for key, number in match.groupdict().items()}
    return parts["days"] * 86400 + parts["hours"] * 3600 + parts["minutes"] * 60 + parts["seconds"]


def freshness_score(published_at: Optional[datetime], now: datetime) -> Optional[float]:
    if published_at is None:
        return None
    if published_at.tzinfo is None or now.tzinfo is None:
        return None
    age_hours = max(0.0, (now - published_at).total_seconds() / 3600.0)
    value = math.exp(-age_hours / (24.0 * 7.0))
    return min(1.0, max(0.0, value)) if math.isfinite(value) else None


def approximate_vph(views: Optional[int], published_at: Optional[datetime], now: datetime) -> Optional[float]:
    if views is None or published_at is None or published_at.tzinfo is None or now.tzinfo is None:
        return None
    age_hours = max(1.0, (now - published_at).total_seconds() / 3600.0)
    return max(0.0, float(views)) / age_hours


def competition_opportunity_proxy(video_count: int, unique_channels: int) -> float:
    """Experimental inverse-supply proxy, not true keyword competition."""
    supply = max(0, video_count) + max(0, unique_channels)
    return 1.0 / (1.0 + supply / 10.0)


def _bounded_log(value: Optional[float], reference: float) -> Optional[float]:
    if value is None:
        return None
    number = max(0.0, float(value))
    return min(1.0, math.log1p(number) / math.log1p(reference))


def score_candidate(
    *,
    observed_vph: Optional[float],
    approx_vph_value: Optional[float],
    engagement: Optional[float],
    outlier: Optional[float],
    freshness: Optional[float],
    competition_proxy: Optional[float],
) -> CandidateScore:
    velocity = observed_vph if observed_vph is not None else approx_vph_value
    normalized = {
        "velocity_score": _bounded_log(velocity, 10_000.0),
        "outlier_score": _bounded_log(outlier, 10.0),
        "engagement_score": None if engagement is None else min(1.0, max(0.0, engagement) / 0.10),
        "freshness_score": freshness,
        "competition_score": competition_proxy,
    }
    weights = {
        "velocity_score": 0.30, "outlier_score": 0.25, "engagement_score": 0.20,
        "freshness_score": 0.15, "competition_score": 0.10,
    }
    available = {key: value for key, value in normalized.items() if value is not None}
    available_weight = sum(weights[key] for key in available)
    base = trend_score(**normalized)
    final = min(1.0, base / available_weight) if available_weight else 0.0
    count = len(available)
    confidence = "high" if count == 5 and observed_vph is not None else ("medium" if count >= 3 else "low")
    explanations: list[str] = []
    if observed_vph is not None:
        explanations.append(f"{observed_vph:,.0f} observed views/hour from repeated snapshots")
    elif approx_vph_value is not None:
        explanations.append(f"{approx_vph_value:,.0f} approximate views/hour from video age (low confidence)")
    if outlier is not None:
        explanations.append(f"{outlier:.1f}x the channel's recent median views")
    if engagement is not None:
        explanations.append(f"{engagement * 100:.1f}% metadata engagement")
    if freshness is not None:
        explanations.append(f"freshness signal {freshness * 100:.0f}/100")
    if competition_proxy is not None:
        explanations.append(f"competition proxy opportunity {competition_proxy * 100:.0f}/100")
    return CandidateScore(final, confidence, count, observed_vph, approx_vph_value,
                          engagement, outlier, freshness, competition_proxy, tuple(explanations))


class YouTubeTrendSearchProvider:
    def __init__(self, youtube: YouTubeReadOnlyService) -> None:
        self.youtube = youtube

    def search(self, user_id: int, query: dict[str, Any], *, max_results: int) -> SearchResult:
        now = datetime.now(timezone.utc)
        within = min(365, max(1, int(query.get("published_within_days") or 30)))
        params: dict[str, Any] = {
            "part": "id", "type": "video", "q": query["query"],
            "maxResults": min(25, max(1, max_results)),
            "order": query.get("search_order") or "date",
            "publishedAfter": (now - timedelta(days=within)).isoformat().replace("+00:00", "Z"),
            "fields": "items/id/videoId",
        }
        if query.get("region_code"):
            params["regionCode"] = query["region_code"]
        if query.get("relevance_language"):
            params["relevanceLanguage"] = query["relevance_language"]
        duration = query.get("duration_filter") or "any"
        if duration in {"any", "short", "medium", "long"}:
            params["videoDuration"] = duration
        search = self.youtube.data_request(user_id, "search", params)
        items = search.get("items") if isinstance(search.get("items"), list) else []
        ids = [str(item.get("id", {}).get("videoId")) for item in items
               if isinstance(item, dict) and isinstance(item.get("id"), dict) and item["id"].get("videoId")]
        if not ids:
            return SearchResult((), {})
        details = self.youtube.data_request(user_id, "videos", {
            "part": "snippet,contentDetails,statistics", "id": ",".join(ids),
            "fields": "items(id,snippet(title,description,channelId,channelTitle,publishedAt,thumbnails),contentDetails/duration,statistics(viewCount,likeCount,commentCount))",
        })
        raw_videos = details.get("items") if isinstance(details.get("items"), list) else []
        channel_ids = list(dict.fromkeys(
            str(item.get("snippet", {}).get("channelId")) for item in raw_videos
            if isinstance(item, dict) and item.get("snippet", {}).get("channelId")
        ))
        channel_data: dict[str, dict[str, Any]] = {}
        uploads: dict[str, str] = {}
        if channel_ids:
            channels = self.youtube.data_request(user_id, "channels", {
                "part": "statistics,contentDetails", "id": ",".join(channel_ids),
                "fields": "items(id,statistics/subscriberCount,contentDetails/relatedPlaylists/uploads)",
            })
            for item in channels.get("items", []) if isinstance(channels.get("items"), list) else []:
                if not isinstance(item, dict) or not item.get("id"):
                    continue
                channel_data[str(item["id"])] = item
                playlist = item.get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads")
                if playlist:
                    uploads[str(item["id"])] = str(playlist)
        videos: list[ResearchVideo] = []
        for item in raw_videos:
            if not isinstance(item, dict) or not item.get("id"):
                continue
            snippet = item.get("snippet") if isinstance(item.get("snippet"), dict) else {}
            stats = item.get("statistics") if isinstance(item.get("statistics"), dict) else {}
            channel_id = str(snippet.get("channelId") or "")
            published = _parse_datetime(snippet.get("publishedAt"))
            videos.append(ResearchVideo(
                video_id=str(item["id"]), title=str(snippet.get("title") or ""),
                channel_id=channel_id, channel_title=str(snippet.get("channelTitle") or ""),
                published_at=published, view_count=_optional_int(stats.get("viewCount")),
                like_count=_optional_int(stats.get("likeCount")), comment_count=_optional_int(stats.get("commentCount")),
                subscriber_count=_optional_int(channel_data.get(channel_id, {}).get("statistics", {}).get("subscriberCount")),
                duration_seconds=parse_youtube_duration(item.get("contentDetails", {}).get("duration")),
                thumbnail_url=_thumbnail(snippet.get("thumbnails")), search_query=str(query["query"]),
                description=str(snippet.get("description") or ""),
                collected_at=now,
            ))
        order = {video_id: index for index, video_id in enumerate(ids)}
        videos.sort(key=lambda video: order.get(video.video_id, len(order)))
        return SearchResult(tuple(videos), uploads)

    def channel_baselines(self, user_id: int, uploads: dict[str, str],
                          channel_ids: list[str], *, long_form: bool) -> dict[str, float]:
        selected = list(dict.fromkeys(channel_ids))[:MAX_ENRICHMENT_CHANNELS]
        video_ids_by_channel: dict[str, list[str]] = {}
        all_ids: list[str] = []
        for channel_id in selected:
            playlist = uploads.get(channel_id)
            if not playlist:
                continue
            payload = self.youtube.data_request(user_id, "playlistItems", {
                "part": "contentDetails", "playlistId": playlist,
                "maxResults": RECENT_BASELINE_VIDEOS,
                "fields": "items/contentDetails/videoId",
            })
            ids = [str(item.get("contentDetails", {}).get("videoId")) for item in payload.get("items", [])
                   if isinstance(item, dict) and item.get("contentDetails", {}).get("videoId")]
            video_ids_by_channel[channel_id] = ids
            all_ids.extend(ids)
        all_ids = list(dict.fromkeys(all_ids))[:50]
        if not all_ids:
            return {}
        payload = self.youtube.data_request(user_id, "videos", {
            "part": "snippet,contentDetails,statistics", "id": ",".join(all_ids),
            "fields": "items(id,snippet/channelId,contentDetails/duration,statistics/viewCount)",
        })
        views_by_channel: dict[str, list[int]] = {}
        for item in payload.get("items", []) if isinstance(payload.get("items"), list) else []:
            duration = parse_youtube_duration(item.get("contentDetails", {}).get("duration"))
            if duration is None or (long_form and duration < 1200):
                continue
            views = _optional_int(item.get("statistics", {}).get("viewCount"))
            channel_id = str(item.get("snippet", {}).get("channelId") or "")
            if views is not None and channel_id:
                views_by_channel.setdefault(channel_id, []).append(views)
        return {channel_id: float(median(values)) for channel_id, values in views_by_channel.items() if values}


class YouTubeTrendScanner:
    _running_users: set[int] = set()
    _lock = threading.Lock()

    def __init__(self, store: Any, provider: YouTubeTrendSearchProvider) -> None:
        self.store = store
        self.provider = provider

    def scan(self, user_id: int) -> dict[str, Any]:
        with self._lock:
            if user_id in self._running_users:
                raise TrendScanAlreadyRunning("A trend scan is already running for this user.")
            self._running_users.add(user_id)
        started = time.monotonic()
        scan_id = str(uuid.uuid4())
        try:
            queries = self.store.list_trend_queries(user_id, enabled_only=True)[:MAX_QUERIES_PER_SCAN]
            if not queries:
                raise TrendScanError("Add and enable at least one research query before scanning.")
            self.store.create_trend_scan(scan_id, user_id, " | ".join(q["query"] for q in queries),
                                         MAX_RESULTS_PER_QUERY)
            own_channel_id = None
            try:
                own_channel_id = self.provider.youtube.get_own_channel(user_id).channel_id
            except Exception:
                logger.info("Own-channel rights classification unavailable for user_id=%s", user_id)
            found: dict[str, dict[str, Any]] = {}
            uploads: dict[str, str] = {}
            competition: dict[str, float] = {}
            global_topics = os.environ.get("CHANNEL_AGENT_TREND_TOPIC_TERMS", "")
            global_exclusions = os.environ.get("CHANNEL_AGENT_TREND_EXCLUSION_TERMS", "")
            for query in queries:
                result = self.provider.search(user_id, query, max_results=MAX_RESULTS_PER_QUERY)
                uploads.update(result.uploads_playlists)
                proxy = competition_opportunity_proxy(len(result.videos), len({v.channel_id for v in result.videos if v.channel_id}))
                for video in result.videos:
                    item = found.setdefault(video.video_id, {"video": video, "queries": [], "competition": proxy})
                    item["queries"].append(query)
                    item["competition"] = max(item["competition"], proxy)
            now = datetime.now(timezone.utc)
            persisted: list[dict[str, Any]] = []
            snapshot_count = 0
            for data in found.values():
                video: ResearchVideo = data["video"]
                relevance_options = [score_niche_relevance(
                    title=video.title,
                    description=video.description,
                    query=query["query"],
                    topic_terms=",".join(filter(None, [query.get("topic_terms"), global_topics])),
                    exclusion_terms=",".join(filter(None, [query.get("exclusion_terms"), global_exclusions])),
                ) for query in data["queries"]]
                relevance = max(relevance_options, key=lambda value: value.score)
                record = {
                    "scan_id": scan_id, "source_id": video.video_id,
                    "source_url": f"https://www.youtube.com/watch?v={video.video_id}",
                    "title": video.title, "channel_id": video.channel_id,
                    "channel_title": video.channel_title,
                    "description": video.description[:5000],
                    "published_at": video.published_at.isoformat() if video.published_at else None,
                    "duration_seconds": video.duration_seconds, "thumbnail_url": video.thumbnail_url,
                    "view_count": video.view_count, "like_count": video.like_count,
                    "comment_count": video.comment_count, "captured_at": video.collected_at.timestamp(),
                }
                rights = RightsStatus.OWNED.value if own_channel_id and video.channel_id == own_channel_id else RightsStatus.IDEA_ONLY.value
                item_id = self.store.upsert_trend_candidate(user_id, record, rights)
                for query in data["queries"]:
                    self.store.match_trend_candidate_query(item_id, int(query["id"]), record["captured_at"])
                previous = self.store.add_trend_snapshot(item_id, record["captured_at"], video.view_count,
                                                         video.like_count, video.comment_count)
                snapshot_count += 1
                observed = None
                if previous:
                    observed = view_velocity(previous.get("view_count"), video.view_count,
                                             datetime.fromtimestamp(previous["captured_at"], timezone.utc),
                                             video.collected_at)
                approx = approximate_vph(video.view_count, video.published_at, now)
                engagement = None if video.view_count is None else engagement_rate(video.view_count, video.like_count, video.comment_count)
                fresh = freshness_score(video.published_at, now)
                cheap = score_candidate(observed_vph=observed, approx_vph_value=approx,
                                        engagement=engagement, outlier=None, freshness=fresh,
                                        competition_proxy=data["competition"])
                persisted.append({"id": item_id, "video": video, "cheap": cheap, "relevance": relevance,
                                  "competition": data["competition"]})
            channel_priority = [item["video"].channel_id for item in sorted(
                persisted, key=lambda item: item["cheap"].score, reverse=True
            ) if item["video"].channel_id]
            baselines = self.provider.channel_baselines(
                user_id, uploads, channel_priority,
                long_form=any(q.get("duration_filter") == "long" for q in queries),
            ) if MAX_ENRICHMENT_CHANNELS else {}
            for item in persisted:
                video = item["video"]
                typical = baselines.get(video.channel_id)
                outlier = outlier_ratio(video.view_count, typical) if typical and typical > 0 else None
                cheap: CandidateScore = item["cheap"]
                score = score_candidate(observed_vph=cheap.observed_vph, approx_vph_value=cheap.approx_vph,
                                        engagement=cheap.engagement, outlier=outlier,
                                        freshness=cheap.freshness, competition_proxy=cheap.competition_proxy)
                relevance: RelevanceScore = item["relevance"]
                self.store.update_trend_candidate_score(
                    user_id, item["id"], observed_vph=score.observed_vph, approx_vph=score.approx_vph,
                    engagement_rate=score.engagement, outlier_ratio=score.outlier,
                    channel_typical_views=typical, freshness_score=score.freshness,
                    competition_proxy=score.competition_proxy, trend_score=score.score,
                    niche_relevance_score=relevance.score,
                    opportunity_score=opportunity_score(score.score, relevance.score),
                    relevance_status=relevance.status,
                    match_reason_json=json.dumps(relevance.match_reasons, ensure_ascii=False),
                    score_confidence=score.confidence, available_signal_count=score.available_signal_count,
                    score_explanation_json=json.dumps(score.explanations, ensure_ascii=False),
                )
            filtered_count = sum(
                1 for item in persisted if item["relevance"].status == "low_relevance"
            )
            note = (
                f"{len(queries)} queries, {len(found)} candidates, {snapshot_count} snapshots, "
                f"{filtered_count} low relevance"
            )
            self.store.finish_trend_scan(scan_id, status="done", note=note)
            logger.info("YouTube trend scan complete user_id=%s queries=%s candidates=%s snapshots=%s duration_ms=%s",
                        user_id, len(queries), len(found), snapshot_count, int((time.monotonic()-started)*1000))
            return {"scan_id": scan_id, "status": "done", "queries_scanned": len(queries),
                    "candidates_found": len(found), "snapshots_created": snapshot_count,
                    "filtered_low_relevance": filtered_count}
        except Exception as exc:
            latest_scan = self.store.latest_trend_scan(user_id)
            if latest_scan and latest_scan.get("scan_id") == scan_id:
                self.store.finish_trend_scan(
                    scan_id,
                    status="error",
                    note="Scan failed",
                    error="Trend scan failed. Please try again or reduce the query count.",
                )
            logger.warning(
                "YouTube trend scan failed user_id=%s scan_id=%s error_type=%s",
                user_id,
                scan_id,
                type(exc).__name__,
            )
            raise
        finally:
            with self._lock:
                self._running_users.discard(user_id)


def _parse_datetime(value: Any) -> Optional[datetime]:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _optional_int(value: Any) -> Optional[int]:
    try:
        return None if value is None or value == "" else max(0, int(value))
    except (TypeError, ValueError):
        return None


def _thumbnail(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    for key in ("high", "medium", "default"):
        if isinstance(value.get(key), dict) and value[key].get("url"):
            return str(value[key]["url"])
    return ""
