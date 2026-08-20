"""Deterministic, dependency-free trend metrics for Channel Agent experiments.

These metrics rank observed metadata. They are heuristics, not predictions of
virality or guarantees of future performance.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Optional


def _non_negative(value: Optional[float]) -> float:
    """Convert a finite numeric value to a non-negative float, else zero."""

    if value is None:
        return 0.0
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(number):
        return 0.0
    return max(0.0, number)


def _normalized(value: Optional[float]) -> float:
    """Return a finite score bounded to the shared 0.0-1.0 range."""

    return min(1.0, _non_negative(value))


def _require_aware(timestamp: datetime) -> None:
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("snapshot timestamps must be timezone-aware")


def view_velocity(
    previous_views: Optional[int],
    current_views: Optional[int],
    previous_at: datetime,
    current_at: datetime,
) -> float:
    """Calculate non-negative views gained per elapsed hour.

    Both timestamps must be timezone-aware. Reversed/equal timestamps,
    missing counters, counter resets, and negative counters safely return 0.
    """

    _require_aware(previous_at)
    _require_aware(current_at)
    elapsed_hours = (current_at - previous_at).total_seconds() / 3600.0
    if elapsed_hours <= 0:
        return 0.0
    previous = _non_negative(previous_views)
    current = _non_negative(current_views)
    delta = current - previous
    if delta <= 0:
        return 0.0
    result = delta / elapsed_hours
    return result if math.isfinite(result) else 0.0


def engagement_rate(
    views: Optional[int],
    likes: Optional[int] = None,
    comments: Optional[int] = None,
) -> float:
    """Return ``(likes + comments) / views`` with missing values treated as 0.

    Zero/missing/negative views return 0. Negative interactions are clamped to
    zero so malformed metadata cannot produce negative or infinite results.
    """

    safe_views = _non_negative(views)
    if safe_views == 0:
        return 0.0
    result = (_non_negative(likes) + _non_negative(comments)) / safe_views
    return result if math.isfinite(result) else 0.0


def outlier_ratio(
    video_views: Optional[int],
    channel_typical_views: Optional[float],
    *,
    cap: Optional[float] = None,
) -> float:
    """Compare video views with a channel's typical views.

    A zero/missing typical value returns 0 because no defensible comparison is
    possible. ``cap`` can limit extreme ratios before later normalization; it
    must be positive when supplied. A high ratio is only a ranking signal.
    """

    typical = _non_negative(channel_typical_views)
    if typical == 0:
        return 0.0
    ratio = _non_negative(video_views) / typical
    if cap is not None:
        safe_cap = _non_negative(cap)
        if safe_cap == 0:
            raise ValueError("cap must be greater than zero")
        ratio = min(ratio, safe_cap)
    return ratio if math.isfinite(ratio) else 0.0


def trend_score(
    *,
    velocity_score: Optional[float] = None,
    outlier_score: Optional[float] = None,
    engagement_score: Optional[float] = None,
    freshness_score: Optional[float] = None,
    competition_score: Optional[float] = None,
) -> float:
    """Combine normalized signals into an experimental 0.0-1.0 score.

    Inputs outside the shared range, missing values, NaN, and infinity are
    clamped safely. ``competition_score`` represents opportunity (higher means
    more favorable/less crowded), not raw competitive intensity.
    """

    score = (
        0.30 * _normalized(velocity_score)
        + 0.25 * _normalized(outlier_score)
        + 0.20 * _normalized(engagement_score)
        + 0.15 * _normalized(freshness_score)
        + 0.10 * _normalized(competition_score)
    )
    return _normalized(score)
