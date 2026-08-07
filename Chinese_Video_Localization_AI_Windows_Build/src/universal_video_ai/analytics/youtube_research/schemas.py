from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional


@dataclass(frozen=True)
class ResearchVideo:
    video_id: str
    title: str
    channel_id: str = ""
    channel_title: str = ""
    published_at: Optional[datetime] = None
    view_count: Optional[int] = None
    like_count: Optional[int] = None
    comment_count: Optional[int] = None
    subscriber_count: Optional[int] = None
    description: str = ""
    duration_seconds: Optional[int] = None
    thumbnail_url: str = ""
    search_query: str = ""
    collected_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class ScoreComponent:
    name: str
    score: float
    explanation: str


@dataclass(frozen=True)
class TrendAnalysis:
    trend_score: float
    confidence_score: float
    video_count: int
    new_24h_count: int
    new_7d_count: int
    new_30d_count: int
    median_views: float
    median_view_velocity: float
    median_age_hours: float
    engagement_rate: float
    components: list[ScoreComponent]
    explanations: list[str]


@dataclass(frozen=True)
class CompetitionAnalysis:
    competition_score: float
    confidence_score: float
    competing_video_count: int
    new_30d_count: int
    median_top_subscribers: float
    median_top_views: float
    near_duplicate_title_count: int
    supply_score: float
    authority_dominance_score: float
    title_saturation_score: float
    freshness_score: float
    small_channel_breakout_score: float
    explanations: list[str]


@dataclass(frozen=True)
class OpportunityAnalysis:
    raw_score: float
    adjusted_score: float
    confidence_score: float
    trend_score: float
    competition_score: float
    content_gap_score: float
    evergreen_score: float
    monetization_potential_score: float
    positive_signals: list[str]
    negative_signals: list[str]
    risks: list[str]
    explanations: list[str]
    suggested_angles: list[str]
    suggested_formats: list[str]
    metadata: dict[str, Any] = field(default_factory=dict)
