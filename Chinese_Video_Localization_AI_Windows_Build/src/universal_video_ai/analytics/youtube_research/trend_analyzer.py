from __future__ import annotations

from datetime import datetime, timezone

from .normalization import clamp, log_score, median, safe_divide, winsorized
from .schemas import ResearchVideo, ScoreComponent, TrendAnalysis
from .scoring import confidence_from_sample_size, weighted_score


class TrendAnalyzer:
    def analyze(self, videos: list[ResearchVideo], now: datetime | None = None) -> TrendAnalysis:
        now = now or datetime.now(timezone.utc)
        ages = [self._age_hours(video, now) for video in videos]
        views = [max(0, video.view_count or 0) for video in videos]
        likes = [max(0, video.like_count or 0) for video in videos]
        comments = [max(0, video.comment_count or 0) for video in videos]
        velocities = [
            safe_divide(view, max(age, 1.0))
            for view, age in zip(views, ages)
        ]
        stable_velocities = winsorized(velocities)

        new_24h = sum(1 for age in ages if age <= 24)
        new_7d = sum(1 for age in ages if age <= 24 * 7)
        new_30d = sum(1 for age in ages if age <= 24 * 30)
        median_views = median(views)
        median_velocity = median(stable_velocities)
        median_age = median(ages)
        engagement = safe_divide(sum(likes) + sum(comments), max(sum(views), 1))

        velocity_score = log_score(median_velocity, reference=1000.0)
        publishing_growth_score = clamp(
            55.0 * safe_divide(new_7d, max(len(videos), 1)) +
            45.0 * safe_divide(new_24h, max(new_7d, 1))
        )
        engagement_score = clamp(engagement * 1000.0)
        freshness_score = clamp(100.0 - safe_divide(median_age, 24 * 30) * 100.0)

        trend_score = weighted_score([
            (0.35, velocity_score),
            (0.25, publishing_growth_score),
            (0.20, engagement_score),
            (0.20, freshness_score),
        ])
        components = [
            ScoreComponent("velocity_score", velocity_score, "Median winsorized view velocity normalized against 1k views/hour."),
            ScoreComponent("publishing_growth_score", publishing_growth_score, "Recent publishing volume from the last 24 hours and 7 days."),
            ScoreComponent("engagement_score", engagement_score, "Likes and comments divided by total views."),
            ScoreComponent("freshness_score", freshness_score, "Higher when median video age is below 30 days."),
        ]
        explanations = [
            f"{new_7d} of {len(videos)} videos were published in the last 7 days.",
            f"Median view velocity is {median_velocity:.2f} views/hour.",
        ]
        return TrendAnalysis(
            trend_score=trend_score,
            confidence_score=confidence_from_sample_size(len(videos)),
            video_count=len(videos),
            new_24h_count=new_24h,
            new_7d_count=new_7d,
            new_30d_count=new_30d,
            median_views=median_views,
            median_view_velocity=median_velocity,
            median_age_hours=median_age,
            engagement_rate=engagement,
            components=components,
            explanations=explanations,
        )

    @staticmethod
    def _age_hours(video: ResearchVideo, now: datetime) -> float:
        if not video.published_at:
            return 24.0 * 365.0
        published = video.published_at
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        return max((now - published).total_seconds() / 3600.0, 1.0)
