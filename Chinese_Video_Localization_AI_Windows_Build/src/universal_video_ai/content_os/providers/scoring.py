"""
Trend scoring utilities.

Functions for scoring and ranking trend sources.
"""
import logging
from typing import List, Dict, Any
from datetime import datetime, timedelta

from .base import TrendSource

logger = logging.getLogger(__name__)


def score_sources(
    sources: List[TrendSource],
    query_relevance: Dict[str, float] = None,
    recency_weight: float = 0.3,
    engagement_weight: float = 0.5,
    relevance_weight: float = 0.2,
) -> List[TrendSource]:
    """
    Score and sort trend sources.
    
    Args:
        sources: List of trend sources to score
        query_relevance: Dictionary mapping source_url to relevance score (0-1)
        recency_weight: Weight for recency in scoring
        engagement_weight: Weight for engagement in scoring
        relevance_weight: Weight for query relevance in scoring
        
    Returns:
        Sorted list of sources with updated metrics
    """
    query_relevance = query_relevance or {}
    
    for source in sources:
        # Recency score (more recent = higher score)
        recency_score = _calculate_recency_score(source)
        
        # Engagement score
        engagement_score = _calculate_engagement_score(source)
        
        # Query relevance score
        relevance_score = query_relevance.get(source.canonical_url, 0.5)
        
        # Combined score
        combined_score = (
            (recency_score * recency_weight) +
            (engagement_score * engagement_weight) +
            (relevance_score * relevance_weight)
        )
        
        # Store score in metrics
        source.metrics["trend_score"] = combined_score
        source.metrics["recency_score"] = recency_score
        source.metrics["engagement_score"] = engagement_score
        source.metrics["relevance_score"] = relevance_score
    
    # Sort by combined score descending
    return sorted(sources, key=lambda s: s.metrics.get("trend_score", 0), reverse=True)


def _calculate_recency_score(source: TrendSource) -> float:
    """Calculate recency score (0-1)."""
    published_at = source.published_at
    if not published_at:
        return 0.5  # Default for unknown date
    
    try:
        # Try parsing various date formats
        for fmt in ["%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ"]:
            try:
                pub_date = datetime.strptime(published_at, fmt)
                break
            except ValueError:
                continue
        else:
            return 0.5
        
        # Calculate age in hours
        now = datetime.utcnow()
        age_hours = (now - pub_date).total_seconds() / 3600
        
        # Score: 1.0 for < 24h, decays over 7 days
        if age_hours < 24:
            return 1.0
        elif age_hours < 168:  # 7 days
            return 1.0 - ((age_hours - 24) / 144)  # Linear decay
        else:
            return 0.0
    except Exception:
        return 0.5


def _calculate_engagement_score(source: TrendSource) -> float:
    """Calculate engagement score (0-1)."""
    views = source.metrics.get("view_count", 0)
    likes = source.metrics.get("like_count", 0)
    comments = source.metrics.get("comment_count", 0)
    shares = source.metrics.get("share_count", 0)
    
    if views == 0:
        return 0.0
    
    # Engagement rate
    total_engagement = likes + comments + shares
    engagement_rate = total_engagement / views
    
    # Log-normalize views
    import math
    if views > 0:
        view_score = min(math.log10(views) / 6, 1.0)  # Log scale, max at 1M views
    else:
        view_score = 0.0
    
    # Cap engagement rate at 10% for score
    engagement_score = min(engagement_rate * 10, 1.0)
    
    # Combine view score and engagement rate
    return (view_score * 0.6) + (engagement_score * 0.4)


def filter_by_risk(
    sources: List[TrendSource],
    max_reuse_risk: str = "medium",
    max_copyright_risk: str = "medium",
) -> List[TrendSource]:
    """
    Filter sources by risk level.
    
    Args:
        sources: List of trend sources
        max_reuse_risk: Maximum allowed reuse risk (low/medium/high)
        max_copyright_risk: Maximum allowed copyright risk (low/medium/high)
        
    Returns:
        Filtered list of sources
    """
    risk_order = {"low": 0, "medium": 1, "high": 2}
    
    max_reuse_level = risk_order.get(max_reuse_risk, 1)
    max_copyright_level = risk_order.get(max_copyright_risk, 1)
    
    filtered = []
    for source in sources:
        reuse_risk = source.metrics.get("reuse_risk", "medium")
        copyright_risk = source.metrics.get("copyright_risk", "medium")
        
        reuse_level = risk_order.get(reuse_risk, 1)
        copyright_level = risk_order.get(copyright_risk, 1)
        
        if reuse_level <= max_reuse_level and copyright_level <= max_copyright_level:
            filtered.append(source)
    
    return filtered
