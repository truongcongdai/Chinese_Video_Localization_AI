"""Rank observed source metadata without inventing missing trend signals."""
from __future__ import annotations

from datetime import datetime, timezone
import math

from universal_video_ai.channel_agent.analytics import engagement_rate
from universal_video_ai.channel_agent.trends import approximate_vph, freshness_score, score_candidate

__all__ = ["rank_source", "source_rank_key"]
_LIKE_REFERENCE = 100_000


def rank_source(source: dict, *, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    published = None
    try:
        published = datetime.fromisoformat(str(source.get("published_at") or "").replace("Z", "+00:00"))
        if published.tzinfo is None or published > now:
            published = None
    except ValueError:
        pass
    views = _count(source.get("view_count"))
    likes = _count(source.get("like_count"))
    comments = _count(source.get("comment_count"))
    freshness = freshness_score(published, now)
    velocity = approximate_vph(views, published, now)
    engagement = engagement_rate(views, likes, comments) if views and (likes is not None or comments is not None) else None
    score = score_candidate(observed_vph=None, approx_vph_value=velocity, engagement=engagement,
                            outlier=None, freshness=freshness, competition_proxy=None)
    if views is not None and (velocity is not None or engagement is not None):
        value, method = score.score, "metadata_proxy"
    elif views is not None or likes is not None:
        count = views if views is not None else likes
        value = min(1.0, math.log1p(count) / math.log1p(_LIKE_REFERENCE))
        method = "popularity_only"
    else:
        value, method = None, "public_feed_order"
    return {"score": round(value, 4) if value is not None else None, "method": method,
            "confidence": score.confidence if method == "metadata_proxy" else "low",
            "approx_views_per_hour": velocity, "engagement_rate": engagement,
            "freshness": freshness, "observed_growth": None}


def source_rank_key(source: dict) -> tuple:
    ranking = source.get("ranking") or rank_source(source)
    value = ranking.get("score")
    return (value is not None, value or 0, _count(source.get("view_count")) or 0,
            _count(source.get("like_count")) or 0)


def _count(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
        return int(number) if math.isfinite(number) and number >= 0 else None
    except (ValueError, TypeError, OverflowError):
        return None
