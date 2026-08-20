"""Metadata-only competitor intelligence built on qualified CP2 candidates."""

from __future__ import annotations

import itertools
import logging
import math
import os
import re
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from statistics import mean, median
from typing import Any, Optional
from urllib.parse import urlparse

from universal_video_ai.channel_agent.analytics import engagement_rate, outlier_ratio
from universal_video_ai.channel_agent.trends import (
    normalize_relevance_text,
    parse_youtube_duration,
    trend_min_relevance,
)
from universal_video_ai.channel_agent.youtube import YouTubeReadOnlyService


logger = logging.getLogger(__name__)

MAX_COMPETITORS = min(20, max(1, int(os.environ.get("CHANNEL_AGENT_COMPETITOR_MAX_CHANNELS", "10"))))
RECENT_VIDEOS = min(50, max(1, int(os.environ.get("CHANNEL_AGENT_COMPETITOR_RECENT_VIDEOS", "20"))))
BREAKOUT_ABOVE = 2.0
BREAKOUT_STRONG = 5.0
BREAKOUT_EXCEPTIONAL = 10.0
LONG_FORM_SECONDS = 20 * 60
SHORT_FORM_SECONDS = 3 * 60
DEFAULT_GENERIC_TERMS = "家族,前世,穿越,一口气看完,合集,完结,完整版,一个,时候"
DEFAULT_EXCLUSION_TERMS = "短剧,短劇,电视剧,電視劇,甜宠,甜寵,霸总,霸總,都市剧,都市劇"


def _bounded_env(name: str, default: float) -> float:
    try:
        value = float(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return min(1.0, max(0.0, value))


def competitor_min_relevance() -> float:
    return _bounded_env("CHANNEL_AGENT_COMPETITOR_MIN_RELEVANCE", 0.55)


def competitor_watch_relevance() -> float:
    return min(competitor_min_relevance(), _bounded_env("CHANNEL_AGENT_COMPETITOR_WATCH_RELEVANCE", 0.35))


def pattern_min_quality() -> float:
    return _bounded_env("CHANNEL_AGENT_PATTERN_MIN_QUALITY", 0.55)


def pattern_min_support() -> int:
    try:
        return min(10, max(2, int(os.environ.get("CHANNEL_AGENT_PATTERN_MIN_SUPPORT", "2"))))
    except (TypeError, ValueError):
        return 2


def gap_min_competitors() -> int:
    try:
        return min(5, max(1, int(os.environ.get("CHANNEL_AGENT_GAP_MIN_COMPETITORS", "2"))))
    except (TypeError, ValueError):
        return 2


def _split_terms(value: Any) -> tuple[str, ...]:
    if not value:
        return ()
    return tuple(dict.fromkeys(term.strip() for term in re.split(r"[,;|\n]+", str(value)) if term.strip()))


def _contains(text: Any, term: Any) -> bool:
    haystack = normalize_relevance_text(text)
    needle = normalize_relevance_text(term)
    if not needle:
        return False
    if any("\u3400" <= char <= "\u9fff" for char in needle):
        return needle.replace(" ", "") in haystack.replace(" ", "")
    return f" {needle} " in f" {haystack} "


@dataclass(frozen=True)
class TopicProfile:
    strong_terms: tuple[str, ...]
    generic_terms: tuple[str, ...]
    exclusion_terms: tuple[str, ...]


def build_topic_profile(topic_terms: Any = None, exclusion_terms: Any = None,
                        generic_terms: Any = None) -> TopicProfile:
    """Build a configurable local profile from saved queries and environment additions."""
    generic_source = generic_terms if generic_terms is not None else os.environ.get(
        "CHANNEL_AGENT_COMPETITOR_GENERIC_TERMS", DEFAULT_GENERIC_TERMS,
    )
    generic = tuple(normalize_relevance_text(term) for term in _split_terms(generic_source))
    raw_strong = list(_split_terms(topic_terms))
    raw_strong.extend(_split_terms(os.environ.get("CHANNEL_AGENT_COMPETITOR_STRONG_TERMS", "")))
    expanded: list[str] = []
    for raw in raw_strong:
        normalized = normalize_relevance_text(raw)
        if not normalized:
            continue
        expanded.append(normalized)
        for token in re.findall(r"[a-z0-9]+", normalized):
            if len(token) >= 3:
                expanded.append(token)
        for run in re.findall(r"[\u3400-\u9fff]+", normalized):
            if len(run) >= 2:
                expanded.append(run)
    strong = tuple(dict.fromkeys(term for term in expanded if term and term not in generic))
    exclusions = list(_split_terms(exclusion_terms))
    exclusions.extend(_split_terms(os.environ.get(
        "CHANNEL_AGENT_COMPETITOR_EXCLUSION_TERMS", DEFAULT_EXCLUSION_TERMS,
    )))
    return TopicProfile(
        strong_terms=strong,
        generic_terms=tuple(dict.fromkeys(generic)),
        exclusion_terms=tuple(dict.fromkeys(normalize_relevance_text(term) for term in exclusions if term)),
    )


class CompetitorError(RuntimeError):
    pass


class CompetitorRefreshRunning(CompetitorError):
    pass


@dataclass(frozen=True)
class CompetitorMetadata:
    channel_id: str
    channel_title: str
    channel_url: str
    custom_url: Optional[str]
    thumbnail_url: Optional[str]
    subscriber_count: Optional[int]
    hidden_subscriber_count: bool
    lifetime_view_count: Optional[int]
    video_count: Optional[int]
    uploads_playlist_id: Optional[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CompetitorVideo:
    video_id: str
    title: str
    description: str
    video_url: str
    thumbnail_url: Optional[str]
    published_at: Optional[datetime]
    duration_seconds: Optional[int]
    view_count: Optional[int]
    like_count: Optional[int]
    comment_count: Optional[int]


def _optional_int(value: Any) -> Optional[int]:
    try:
        return None if value is None or value == "" else max(0, int(value))
    except (TypeError, ValueError):
        return None


def _parse_datetime(value: Any) -> Optional[datetime]:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _thumbnail(value: Any) -> Optional[str]:
    if not isinstance(value, dict):
        return None
    for size in ("high", "medium", "default"):
        item = value.get(size)
        if isinstance(item, dict) and item.get("url"):
            return str(item["url"])
    return None


class YouTubeCompetitorProvider:
    def __init__(self, youtube: YouTubeReadOnlyService) -> None:
        self.youtube = youtube

    def resolve_channel(self, user_id: int, reference: str) -> CompetitorMetadata:
        value = str(reference or "").strip()
        if not value:
            raise CompetitorError("Enter a YouTube channel URL, handle, or channel ID.")
        params: dict[str, Any] = {}
        parsed = urlparse(value if "://" in value else "")
        path = parsed.path.strip("/") if parsed.netloc else ""
        if value.startswith("UC") and len(value) >= 20:
            params["id"] = value
        elif value.startswith("@"):
            params["forHandle"] = value[1:]
        elif path.startswith("channel/"):
            params["id"] = path.split("/", 1)[1].split("/", 1)[0]
        elif path.startswith("@"):
            params["forHandle"] = path.split("/", 1)[0][1:]
        elif path.startswith("user/"):
            params["forUsername"] = path.split("/", 1)[1].split("/", 1)[0]
        if not params:
            search = self.youtube.data_request(user_id, "search", {
                "part": "id", "type": "channel", "q": value, "maxResults": 1,
                "fields": "items/id/channelId",
            })
            items = search.get("items") if isinstance(search.get("items"), list) else []
            channel_id = items[0].get("id", {}).get("channelId") if items else None
            if not channel_id:
                raise CompetitorError("YouTube competitor channel was not found.")
            params["id"] = str(channel_id)
        channels = self.fetch_channels(user_id, [str(next(iter(params.values())))], selector=params)
        if not channels:
            raise CompetitorError("YouTube competitor channel was not found.")
        return next(iter(channels.values()))

    def fetch_channels(
        self, user_id: int, channel_ids: list[str], *, selector: Optional[dict[str, Any]] = None,
    ) -> dict[str, CompetitorMetadata]:
        params: dict[str, Any] = {
            "part": "snippet,statistics,contentDetails",
            "fields": (
                "items(id,snippet(title,customUrl,thumbnails),"
                "statistics(subscriberCount,hiddenSubscriberCount,viewCount,videoCount),"
                "contentDetails/relatedPlaylists/uploads)"
            ),
        }
        if selector:
            params.update(selector)
        else:
            unique = list(dict.fromkeys(channel_ids))[:50]
            if not unique:
                return {}
            params["id"] = ",".join(unique)
        payload = self.youtube.data_request(user_id, "channels", params)
        result: dict[str, CompetitorMetadata] = {}
        for item in payload.get("items", []) if isinstance(payload.get("items"), list) else []:
            if not isinstance(item, dict) or not item.get("id"):
                continue
            channel_id = str(item["id"])
            snippet = item.get("snippet") if isinstance(item.get("snippet"), dict) else {}
            stats = item.get("statistics") if isinstance(item.get("statistics"), dict) else {}
            details = item.get("contentDetails") if isinstance(item.get("contentDetails"), dict) else {}
            related = details.get("relatedPlaylists") if isinstance(details.get("relatedPlaylists"), dict) else {}
            hidden = bool(stats.get("hiddenSubscriberCount", False))
            result[channel_id] = CompetitorMetadata(
                channel_id=channel_id,
                channel_title=str(snippet.get("title") or "YouTube channel"),
                channel_url=f"https://www.youtube.com/channel/{channel_id}",
                custom_url=str(snippet["customUrl"]) if snippet.get("customUrl") else None,
                thumbnail_url=_thumbnail(snippet.get("thumbnails")),
                subscriber_count=None if hidden else _optional_int(stats.get("subscriberCount")),
                hidden_subscriber_count=hidden,
                lifetime_view_count=_optional_int(stats.get("viewCount")),
                video_count=_optional_int(stats.get("videoCount")),
                uploads_playlist_id=str(related["uploads"]) if related.get("uploads") else None,
            )
        return result

    def recent_videos(
        self, user_id: int, competitors: list[dict[str, Any]], *, sample_size: int = RECENT_VIDEOS,
    ) -> dict[str, list[CompetitorVideo]]:
        sample_size = min(50, max(1, int(sample_size)))
        owners: dict[str, list[str]] = {}
        ordered_ids: list[str] = []
        for competitor in competitors[:MAX_COMPETITORS]:
            playlist = competitor.get("uploads_playlist_id")
            if not playlist:
                continue
            payload = self.youtube.data_request(user_id, "playlistItems", {
                "part": "contentDetails", "playlistId": playlist, "maxResults": sample_size,
                "fields": "items/contentDetails/videoId",
            })
            ids = [
                str(item.get("contentDetails", {}).get("videoId"))
                for item in payload.get("items", []) if isinstance(payload.get("items"), list)
                if isinstance(item, dict) and item.get("contentDetails", {}).get("videoId")
            ]
            owners[str(competitor["channel_id"])] = ids
            ordered_ids.extend(ids)
        metadata: dict[str, CompetitorVideo] = {}
        unique_ids = list(dict.fromkeys(ordered_ids))
        for offset in range(0, len(unique_ids), 50):
            batch = unique_ids[offset:offset + 50]
            payload = self.youtube.data_request(user_id, "videos", {
                "part": "snippet,contentDetails,statistics", "id": ",".join(batch),
                "fields": "items(id,snippet(title,description,publishedAt,thumbnails),contentDetails/duration,statistics(viewCount,likeCount,commentCount))",
            })
            for item in payload.get("items", []) if isinstance(payload.get("items"), list) else []:
                if not isinstance(item, dict) or not item.get("id"):
                    continue
                snippet = item.get("snippet") if isinstance(item.get("snippet"), dict) else {}
                stats = item.get("statistics") if isinstance(item.get("statistics"), dict) else {}
                video_id = str(item["id"])
                metadata[video_id] = CompetitorVideo(
                    video_id=video_id, title=str(snippet.get("title") or ""),
                    description=str(snippet.get("description") or ""),
                    video_url=f"https://www.youtube.com/watch?v={video_id}",
                    thumbnail_url=_thumbnail(snippet.get("thumbnails")),
                    published_at=_parse_datetime(snippet.get("publishedAt")),
                    duration_seconds=parse_youtube_duration(item.get("contentDetails", {}).get("duration")),
                    view_count=_optional_int(stats.get("viewCount")),
                    like_count=_optional_int(stats.get("likeCount")),
                    comment_count=_optional_int(stats.get("commentCount")),
                )
        return {
            channel_id: [metadata[video_id] for video_id in ids if video_id in metadata]
            for channel_id, ids in owners.items()
        }


def comparable_videos(videos: list[CompetitorVideo], mode: str) -> list[CompetitorVideo]:
    if mode == "all":
        return list(videos)
    if mode == "short":
        return [video for video in videos if video.duration_seconds is not None and video.duration_seconds <= SHORT_FORM_SECONDS]
    return [video for video in videos if video.duration_seconds is not None and video.duration_seconds >= LONG_FORM_SECONDS]


def breakout_strength(ratio: Optional[float]) -> str:
    if ratio is None:
        return "unavailable"
    if ratio >= BREAKOUT_EXCEPTIONAL:
        return "exceptional"
    if ratio >= BREAKOUT_STRONG:
        return "strong"
    if ratio >= BREAKOUT_ABOVE:
        return "above_baseline"
    return "normal"


def duration_bucket(seconds: Optional[int]) -> Optional[str]:
    if seconds is None:
        return None
    minutes = seconds / 60.0
    if minutes < 20:
        return "under 20 min"
    if minutes < 40:
        return "20–40 min"
    if minutes < 60:
        return "40–60 min"
    if minutes < 90:
        return "60–90 min"
    if minutes < 120:
        return "90–120 min"
    return "120+ min"


def _title_terms(title: str, profile: TopicProfile) -> set[str]:
    normalized = normalize_relevance_text(title)
    result = {token for token in re.findall(r"[a-z0-9]+", normalized) if len(token) >= 3}
    for run in re.findall(r"[\u3400-\u9fff]+", normalized):
        result.update(run[index:index + 2] for index in range(max(0, len(run) - 1)))
    result.update(term for term in profile.strong_terms + profile.generic_terms if _contains(normalized, term))
    return {term for term in result if term}


def _text_profile_score(title: Any, description: Any, profile: TopicProfile) -> dict[str, Any]:
    title_strong = tuple(term for term in profile.strong_terms if _contains(title, term))
    description_strong = tuple(
        term for term in profile.strong_terms if term not in title_strong and _contains(description, term)
    )
    title_support = tuple(term for term in profile.generic_terms if _contains(title, term))
    description_support = tuple(
        term for term in profile.generic_terms if term not in title_support and _contains(description, term)
    )
    title_excluded = tuple(term for term in profile.exclusion_terms if _contains(title, term))
    description_excluded = tuple(
        term for term in profile.exclusion_terms if term not in title_excluded and _contains(description, term)
    )
    score = min(0.90, 0.72 * len(title_strong))
    score += min(0.30, 0.20 * len(description_strong))
    if len(title_strong) >= 2:
        score += 0.15
    score += min(0.10, 0.04 * len(title_support) + 0.02 * len(description_support))
    score -= min(0.90, 0.65 * len(title_excluded) + 0.20 * len(description_excluded))
    score = min(1.0, max(0.0, score))
    return {
        "score": score,
        "strong_match": bool(title_strong or description_strong) and score >= 0.50,
        "strong_terms": title_strong + description_strong,
        "support_terms": title_support + description_support,
        "excluded_terms": title_excluded + description_excluded,
    }


def _pattern_metrics(components: tuple[str, ...], evidence: list[dict[str, Any]],
                     profile: TopicProfile) -> dict[str, Any]:
    unique_evidence = list({str(item.get("video_id")): item for item in evidence if item.get("video_id")}.values())
    support = len(unique_evidence)
    core = {
        component for component in components
        if component not in profile.generic_terms
        if any(
            _contains(component, term) or term.startswith(component) or term.endswith(component)
            for term in profile.strong_terms
        )
    }
    generic = {component for component in components if component in profile.generic_terms}
    excluded = any(component in profile.exclusion_terms for component in components)
    compound = len(components) > 1
    if len(core) >= 2:
        relevance = 1.0
    elif core:
        relevance = 0.88 if compound else 0.80
    elif compound and len(generic) < len(components):
        relevance = 0.25
    else:
        relevance = 0.0
    if compound:
        specificity = 0.95 if core else 0.45
    elif core:
        specificity = 0.75
    elif generic:
        specificity = 0.05
    else:
        specificity = 0.25
    if excluded:
        relevance = 0.0
        specificity = 0.0
    breakout_evidence = [item for item in unique_evidence if (item.get("outlier_ratio") or 0) >= BREAKOUT_ABOVE]
    support_score = min(1.0, support / 4.0)
    breakout_score = min(1.0, len(breakout_evidence) / 2.0)
    quality = 0.45 * relevance + 0.25 * specificity + 0.15 * support_score + 0.15 * breakout_score
    if support < pattern_min_support():
        status = "filtered"
    elif quality >= pattern_min_quality():
        status = "qualified"
    elif quality >= max(0.30, pattern_min_quality() * 0.70):
        status = "watch"
    else:
        status = "filtered"
    return {
        "pattern_relevance_score": min(1.0, max(0.0, relevance)),
        "pattern_support": support,
        "pattern_specificity": min(1.0, max(0.0, specificity)),
        "pattern_quality_score": min(1.0, max(0.0, quality)),
        "pattern_quality_status": status,
        "evidence": unique_evidence[:5],
    }


def extract_patterns(videos: list[dict[str, Any]], topic_terms: str = "", *,
                     exclusion_terms: str = "", generic_terms: Any = None) -> list[dict[str, Any]]:
    profile = build_topic_profile(topic_terms, exclusion_terms, generic_terms)
    unique_videos = list({str(video.get("video_id")): video for video in videos if video.get("video_id")}.values())
    terms_by_video = {
        str(video["video_id"]): _title_terms(video.get("title") or "", profile) for video in unique_videos
    }
    counts: dict[str, int] = {}
    for terms in terms_by_video.values():
        for term in terms:
            counts[term] = counts.get(term, 0) + 1
    frequent = [
        term for term, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
        if count >= pattern_min_support()
    ][:16]
    patterns: list[dict[str, Any]] = []
    for term in frequent:
        evidence = [video for video in unique_videos if term in terms_by_video[str(video["video_id"])]]
        breakout = [video for video in evidence if (video.get("outlier_ratio") or 0) >= BREAKOUT_ABOVE]
        ratios = [float(video["outlier_ratio"]) for video in breakout if video.get("outlier_ratio") is not None]
        evidence_rows = [
            {key: video.get(key) for key in ("video_id", "title", "video_url", "outlier_ratio")}
            for video in evidence
        ]
        patterns.append({
            "pattern": term, "video_count": len(evidence), "breakout_count": len(breakout),
            "median_outlier": median(ratios) if ratios else None,
            **_pattern_metrics((term,), evidence_rows, profile),
        })
    for left, right in itertools.combinations(frequent[:10], 2):
        evidence = [
            video for video in unique_videos
            if {left, right}.issubset(terms_by_video[str(video["video_id"])])
        ]
        if len(evidence) < pattern_min_support():
            continue
        evidence_rows = [
            {key: video.get(key) for key in ("video_id", "title", "video_url", "outlier_ratio")}
            for video in evidence
        ]
        patterns.append({
            "pattern": f"{left} + {right}", "video_count": len(evidence),
            "breakout_count": sum((video.get("outlier_ratio") or 0) >= BREAKOUT_ABOVE for video in evidence),
            "median_outlier": median([float(video["outlier_ratio"]) for video in evidence if video.get("outlier_ratio") is not None]) if any(video.get("outlier_ratio") is not None for video in evidence) else None,
            **_pattern_metrics((left, right), evidence_rows, profile),
        })
    return sorted(
        patterns,
        key=lambda item: (-item["pattern_quality_score"], -item["breakout_count"], -item["video_count"], item["pattern"]),
    )[:20]


def score_competitor_relevance(
    analyzed: list[dict[str, Any]], *, candidate_summary: Optional[dict[str, Any]],
    profile: TopicProfile, channel_title: str = "",
) -> dict[str, Any]:
    """Score channel-wide niche fit independently from performance."""
    if not analyzed or not profile.strong_terms:
        return {
            "competitor_relevance_score": None,
            "competitor_relevance_status": "unscored",
            "competitor_match_reasons": [],
            "niche_hit_rate": None,
            "niche_matching_video_count": 0,
            "niche_analyzed_video_count": len(analyzed),
        }
    term_counts: dict[str, int] = {}
    excluded_counts: dict[str, int] = {}
    video_scores: list[float] = []
    hits = 0
    excluded_videos = 0
    for video in analyzed:
        signal = _text_profile_score(video.get("title"), video.get("description"), profile)
        video["niche_relevance_score"] = signal["score"]
        video["niche_match"] = signal["strong_match"]
        video_scores.append(float(signal["score"]))
        hits += int(signal["strong_match"])
        excluded_videos += int(bool(signal["excluded_terms"]))
        for term in set(signal["strong_terms"]):
            term_counts[term] = term_counts.get(term, 0) + 1
        for term in set(signal["excluded_terms"]):
            excluded_counts[term] = excluded_counts.get(term, 0) + 1
    hit_rate = hits / len(analyzed)
    median_video_score = float(median(video_scores)) if video_scores else None
    channel_signal = _text_profile_score(channel_title, "", profile)
    candidate_relevance = (candidate_summary or {}).get("median_relevance_score")
    signals = {
        "hit_rate": hit_rate,
        "recent_video_relevance": median_video_score,
        "candidate_relevance": candidate_relevance,
        "channel_identity": channel_signal["score"],
    }
    weights = {
        "hit_rate": 0.45,
        "recent_video_relevance": 0.25,
        "candidate_relevance": 0.20,
        "channel_identity": 0.10,
    }
    available = {name: value for name, value in signals.items() if value is not None}
    total_weight = sum(weights[name] for name in available)
    score = sum(weights[name] * min(1.0, max(0.0, float(value))) for name, value in available.items())
    score = score / total_weight if total_weight else 0.0
    penalty = 0.35 * int(bool(channel_signal["excluded_terms"]))
    penalty += 0.25 * (excluded_videos / len(analyzed))
    score = min(1.0, max(0.0, score - penalty))
    if score >= competitor_min_relevance() and hit_rate >= 0.30:
        status = "qualified"
    elif score >= competitor_watch_relevance() and hit_rate >= 0.15:
        status = "watch"
    else:
        status = "low_relevance"
    reasons = [f"{hits}/{len(analyzed)} recent videos matched niche"]
    if candidate_relevance is not None:
        reasons.append(f"CP2 median relevance {float(candidate_relevance):.0%}")
    for term, count in sorted(term_counts.items(), key=lambda item: (-item[1], item[0]))[:5]:
        reasons.append(f"{term}: {count} recent titles/descriptions")
    for term in channel_signal["excluded_terms"]:
        reasons.append(f"excluded channel term: {term}")
    for term, count in sorted(excluded_counts.items(), key=lambda item: (-item[1], item[0]))[:3]:
        reasons.append(f"excluded recent term {term}: {count} videos")
    return {
        "competitor_relevance_score": score,
        "competitor_relevance_status": status,
        "competitor_match_reasons": list(dict.fromkeys(reasons)),
        "niche_hit_rate": hit_rate,
        "niche_matching_video_count": hits,
        "niche_analyzed_video_count": len(analyzed),
    }


def analyze_competitor(
    videos: list[CompetitorVideo], *, mode: str = "long", candidate_summary: Optional[dict[str, Any]] = None,
    topic_terms: str = "", exclusion_terms: str = "", generic_terms: Any = None,
    channel_title: str = "", now: Optional[datetime] = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    sample = comparable_videos(videos, mode)
    valid_views = [video.view_count for video in sample if video.view_count is not None]
    baseline = float(median(valid_views)) if valid_views else None
    analyzed: list[dict[str, Any]] = []
    for video in sample:
        ratio = outlier_ratio(video.view_count, baseline) if baseline and baseline > 0 else None
        analyzed.append({
            "video_id": video.video_id, "title": video.title, "description": video.description,
            "video_url": video.video_url, "thumbnail_url": video.thumbnail_url,
            "published_at": video.published_at.isoformat() if video.published_at else None,
            "duration_seconds": video.duration_seconds, "view_count": video.view_count,
            "like_count": video.like_count, "comment_count": video.comment_count,
            "engagement_rate": None if video.view_count is None else engagement_rate(video.view_count, video.like_count, video.comment_count),
            "outlier_ratio": ratio, "breakout_strength": breakout_strength(ratio),
        })
    breakout_count = sum((video.get("outlier_ratio") or 0) >= BREAKOUT_ABOVE for video in analyzed)
    frequency = breakout_count / len(analyzed) if analyzed else None
    consistency = (
        sum((video.view_count or 0) >= baseline * 0.5 for video in sample if video.view_count is not None) / len(valid_views)
        if baseline is not None and valid_views else None
    )
    durations = [video.duration_seconds for video in sample if video.duration_seconds is not None]
    engagements = [video["engagement_rate"] for video in analyzed if video["engagement_rate"] is not None]
    published = sorted((video.published_at for video in sample if video.published_at), reverse=True)
    intervals = [(published[index] - published[index + 1]).total_seconds() / 3600 for index in range(len(published) - 1)]
    uploads_per_week = None
    if len(published) >= 2:
        span_days = max(1 / 24, (published[0] - published[-1]).total_seconds() / 86400)
        uploads_per_week = (len(published) - 1) / span_days * 7
    buckets: list[dict[str, Any]] = []
    for name in ("under 20 min", "20–40 min", "40–60 min", "60–90 min", "90–120 min", "120+ min"):
        rows = [video for video in analyzed if duration_bucket(video.get("duration_seconds")) == name]
        if not rows:
            continue
        bucket_views = [row["view_count"] for row in rows if row["view_count"] is not None]
        ratios = [row["outlier_ratio"] for row in rows if row["outlier_ratio"] is not None]
        buckets.append({"bucket": name, "video_count": len(rows),
                        "median_views": median(bucket_views) if bucket_views else None,
                        "median_outlier": median(ratios) if ratios else None,
                        "breakout_count": sum((row.get("outlier_ratio") or 0) >= BREAKOUT_ABOVE for row in rows)})
    summary = candidate_summary or {}
    signals = {
        "breakout": frequency,
        "candidate_opportunity": summary.get("median_opportunity_score"),
        "niche_relevance": summary.get("median_relevance_score"),
        "momentum": None if baseline is None else min(1.0, math.log1p(max(0.0, baseline)) / math.log1p(1_000_000)),
        "consistency": consistency,
    }
    weights = {"breakout": .30, "candidate_opportunity": .25, "niche_relevance": .20, "momentum": .15, "consistency": .10}
    available = {key: value for key, value in signals.items() if value is not None}
    weight = sum(weights[key] for key in available)
    score = sum(weights[key] * min(1.0, max(0.0, float(value))) for key, value in available.items()) / weight if weight else 0.0
    confidence = "high" if len(analyzed) >= 15 else ("medium" if len(analyzed) >= 5 else "low")
    profile = build_topic_profile(topic_terms, exclusion_terms, generic_terms)
    relevance = score_competitor_relevance(
        analyzed, candidate_summary=summary, profile=profile, channel_title=channel_title,
    )
    return ({
        "sample_mode": mode, "recent_upload_count": len(analyzed), "median_views": baseline,
        "mean_views": mean(valid_views) if valid_views else None,
        "median_duration_seconds": median(durations) if durations else None,
        "median_engagement_rate": median(engagements) if engagements else None,
        "breakout_frequency": frequency, "breakout_count": breakout_count,
        "consistency_score": consistency, "uploads_per_week": uploads_per_week,
        "median_upload_interval_hours": median(intervals) if intervals else None,
        "competitor_score": min(1.0, max(0.0, score)), "score_confidence": confidence,
        **relevance,
        "patterns": extract_patterns(
            analyzed, topic_terms, exclusion_terms=exclusion_terms, generic_terms=generic_terms,
        ), "duration_buckets": buckets,
        "analyzed_at": (now or datetime.now(timezone.utc)).timestamp(),
    }, analyzed)


def opportunity_gaps(competitors: list[dict[str, Any]], candidates: list[dict[str, Any]], *,
                     include_filtered: bool = False) -> list[dict[str, Any]]:
    aggregate: dict[str, dict[str, Any]] = {}
    for competitor in competitors:
        competitor_status = competitor.get("competitor_relevance_status") or "unscored"
        if not include_filtered and competitor_status != "qualified":
            continue
        for pattern in competitor.get("patterns", []):
            if not pattern.get("breakout_count"):
                continue
            pattern_status = pattern.get("pattern_quality_status") or "unscored"
            if not include_filtered and pattern_status != "qualified":
                continue
            components = tuple(sorted(part.strip() for part in pattern["pattern"].split("+") if part.strip()))
            canonical = " + ".join(components)
            entry = aggregate.setdefault(canonical, {
                "competitors": set(), "qualities": [], "evidence": {}, "statuses": set(),
            })
            entry["competitors"].add(competitor["id"])
            entry["statuses"].add(pattern_status)
            if pattern.get("pattern_quality_score") is not None:
                entry["qualities"].append(float(pattern["pattern_quality_score"]))
            for evidence in pattern.get("evidence", []):
                video_id = evidence.get("video_id")
                if video_id:
                    entry["evidence"][str(video_id)] = evidence
    results: list[dict[str, Any]] = []
    for pattern, data in aggregate.items():
        terms = [part.strip() for part in pattern.split("+")]
        candidate_count = sum(all(normalize_relevance_text(term) in normalize_relevance_text(candidate.get("title")) for term in terms) for candidate in candidates)
        competitor_count = len(data["competitors"])
        evidence = list(data["evidence"].values())
        breakout_evidence = [item for item in evidence if (item.get("outlier_ratio") or 0) >= BREAKOUT_ABOVE]
        ratios = [float(item["outlier_ratio"]) for item in breakout_evidence if item.get("outlier_ratio") is not None]
        pattern_quality = median(data["qualities"]) if data["qualities"] else 0.0
        cross_channel_score = min(1.0, competitor_count / max(1, gap_min_competitors()))
        breakout_support_score = min(1.0, len(breakout_evidence) / 3.0)
        gap_quality = min(1.0, 0.55 * pattern_quality + 0.25 * cross_channel_score + 0.20 * breakout_support_score)
        if (gap_quality >= pattern_min_quality() and competitor_count >= gap_min_competitors()
                and breakout_evidence and "qualified" in data["statuses"]):
            quality_status = "qualified"
        elif pattern_quality >= pattern_min_quality() and breakout_evidence:
            quality_status = "watch"
        else:
            quality_status = "filtered"
        results.append({
            "pattern": pattern, "supporting_competitor_count": competitor_count,
            "supporting_breakout_count": len(breakout_evidence),
            "median_outlier": median(ratios) if ratios else None,
            "qualified_candidate_count": candidate_count,
            "competition_proxy": 1.0 / (1.0 + candidate_count),
            "confidence": "high" if competitor_count >= 3 else ("medium" if competitor_count >= 2 else "low"),
            "gap_quality_score": gap_quality,
            "gap_quality_status": quality_status,
            "evidence": breakout_evidence[:10],
        })
    if not include_filtered:
        results = [item for item in results if item["gap_quality_status"] == "qualified"]
    return sorted(
        results,
        key=lambda item: (-item["gap_quality_score"], -item["competition_proxy"],
                          -item["supporting_breakout_count"], item["pattern"]),
    )[:20]


class CompetitorIntelligenceService:
    _running_users: set[int] = set()
    _lock = threading.Lock()

    def __init__(self, store: Any, provider: YouTubeCompetitorProvider) -> None:
        self.store = store
        self.provider = provider

    def discover(self, user_id: int) -> dict[str, Any]:
        summaries = self.store.list_qualified_trend_channels(user_id, trend_min_relevance(), MAX_COMPETITORS)
        metadata = self.provider.fetch_channels(user_id, [item["channel_id"] for item in summaries])
        for summary in summaries:
            channel = metadata.get(summary["channel_id"])
            if channel:
                self.store.upsert_competitor(user_id, channel.to_dict(), source_candidate_count=summary["source_candidate_count"])
        logger.info("Competitor discovery user_id=%s qualified_channels=%s discovered=%s",
                    user_id, len(summaries), len(metadata))
        return {"qualified_channels": len(summaries), "competitors_discovered": len(metadata)}

    def add(self, user_id: int, reference: str, notes: Optional[str] = None) -> dict[str, Any]:
        channel = self.provider.resolve_channel(user_id, reference)
        competitor_id = self.store.upsert_competitor(user_id, channel.to_dict(), tracked=True, notes=notes)
        return self.store.get_competitor(user_id, competitor_id)

    def refresh(self, user_id: int, competitor_id: Optional[int] = None, mode: str = "long") -> dict[str, Any]:
        if mode not in {"long", "short", "all"}:
            raise CompetitorError("Competitor sample mode must be long, short, or all.")
        with self._lock:
            if user_id in self._running_users:
                raise CompetitorRefreshRunning("A competitor refresh is already running for this user.")
            self._running_users.add(user_id)
        started = time.monotonic()
        try:
            competitors = self.store.list_competitors(
                user_id, competitor_id=competitor_id, limit=MAX_COMPETITORS, include_filtered=True,
            )
            if not competitors:
                raise CompetitorError("Discover or add at least one competitor first.")
            metadata = self.provider.fetch_channels(user_id, [row["channel_id"] for row in competitors])
            refreshed: list[dict[str, Any]] = []
            for row in competitors:
                channel = metadata.get(row["channel_id"])
                if channel:
                    self.store.upsert_competitor(user_id, channel.to_dict(), source_candidate_count=row.get("source_candidate_count", 0))
            competitors = self.store.list_competitors(
                user_id, competitor_id=competitor_id, limit=MAX_COMPETITORS, include_filtered=True,
            )
            videos_by_channel = self.provider.recent_videos(user_id, competitors, sample_size=RECENT_VIDEOS)
            queries = self.store.list_trend_queries(user_id, enabled_only=True)
            topic_terms = ",".join(
                str(value) for query in queries for value in (query.get("query"), query.get("topic_terms")) if value
            )
            exclusion_terms = ",".join(
                str(query.get("exclusion_terms")) for query in queries if query.get("exclusion_terms")
            )
            summaries = {row["channel_id"]: row for row in self.store.list_qualified_trend_channels(user_id, trend_min_relevance(), 200)}
            for competitor in competitors:
                analysis, videos = analyze_competitor(
                    videos_by_channel.get(competitor["channel_id"], []), mode=mode,
                    candidate_summary=summaries.get(competitor["channel_id"]), topic_terms=topic_terms,
                    exclusion_terms=exclusion_terms, channel_title=competitor.get("channel_title") or "",
                )
                for video in videos:
                    self.store.upsert_competitor_video(competitor["id"], video)
                self.store.update_competitor_analysis(user_id, competitor["id"], analysis)
                self.store.add_competitor_snapshot(user_id, competitor["id"])
                refreshed.append({"id": competitor["id"], "channel_id": competitor["channel_id"], "videos": len(videos)})
            duration_ms = int((time.monotonic() - started) * 1000)
            logger.info("Competitor refresh user_id=%s competitors=%s videos=%s duration_ms=%s",
                        user_id, len(refreshed), sum(item["videos"] for item in refreshed), duration_ms)
            return {"competitors_refreshed": len(refreshed), "items": refreshed,
                    "duration_ms": duration_ms}
        finally:
            with self._lock:
                self._running_users.discard(user_id)
