"""
Scoring utilities for Trend Scanner.

This module provides functions to calculate trend scores for video items
based on various metrics like views, likes, comments, and recency.
"""

import logging
from datetime import datetime, timedelta
from typing import Optional
from universal_video_ai.trends.models import TrendItemCandidate

_logger = logging.getLogger(__name__)


def calculate_trend_score(
    item: TrendItemCandidate,
    max_views: Optional[int] = None,
    max_likes: Optional[int] = None,
    max_comments: Optional[int] = None,
    recency_weight: float = 0.3,
    engagement_weight: float = 0.7,
) -> float:
    """
    Calculate a trend score for a video item.

    The score combines engagement metrics (views, likes, comments) with
    recency to identify trending content.

    Args:
        item: The TrendItemCandidate to score
        max_views: Maximum expected views for normalization (optional)
        max_likes: Maximum expected likes for normalization (optional)
        max_comments: Maximum expected comments for normalization (optional)
        recency_weight: Weight for recency in the score (0-1)
        engagement_weight: Weight for engagement in the score (0-1)

    Returns:
        A trend score between 0 and 1
    """
    engagement_score = 0.0

    # Normalize engagement metrics
    if item.view_count and max_views and max_views > 0:
        engagement_score += (item.view_count / max_views) * 0.4

    if item.like_count and max_likes and max_likes > 0:
        engagement_score += (item.like_count / max_likes) * 0.3

    if item.comment_count and max_comments and max_comments > 0:
        engagement_score += (item.comment_count / max_comments) * 0.3

    # Calculate recency score
    recency_score = 0.0
    if item.published_at:
        try:
            pub_date = datetime.fromisoformat(item.published_at.replace("Z", "+00:00"))
            days_old = (datetime.utcnow() - pub_date).days
            # Decay score over 30 days
            recency_score = max(0.0, 1.0 - (days_old / 30.0))
        except Exception as exc:
            _logger.debug("Failed to parse published_at '%s': %s", item.published_at, exc)

    # Combine scores
    total_score = (engagement_score * engagement_weight) + (recency_score * recency_weight)
    return min(1.0, max(0.0, total_score))


def normalize_scores(items: list[TrendItemCandidate]) -> list[TrendItemCandidate]:
    """
    Normalize trend scores across a list of items to a 0-1 range.

    Args:
        items: List of TrendItemCandidate objects

    Returns:
        List with updated trend scores
    """
    if not items:
        return items

    # Extract existing scores
    scores = [item.trend_score for item in items if item.trend_score > 0]
    if not scores:
        return items

    min_score = min(scores)
    max_score = max(scores)

    if max_score == min_score:
        # All scores are the same, set to 0.5
        for item in items:
            if item.trend_score > 0:
                item.trend_score = 0.5
    else:
        # Normalize to 0-1 range
        for item in items:
            if item.trend_score > 0:
                item.trend_score = (item.trend_score - min_score) / (max_score - min_score)

    return items
