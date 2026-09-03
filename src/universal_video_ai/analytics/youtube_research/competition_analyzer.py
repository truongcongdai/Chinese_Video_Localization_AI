from __future__ import annotations

from datetime import datetime, timezone

from .normalization import clamp, jaccard_similarity, log_score, median, safe_divide
from .schemas import CompetitionAnalysis, ResearchVideo
from .scoring import confidence_from_sample_size, weighted_score


class CompetitionAnalyzer:
    def analyze(self, videos: list[ResearchVideo], now: datetime | None = None) -> CompetitionAnalysis:
        now = now or datetime.now(timezone.utc)
        count = len(videos)
        sorted_by_views = sorted(
            videos,
            key=lambda item: item.view_count if item.view_count is not None else -1,
            reverse=True,
        )
        top_videos = sorted_by_views[:10]
        new_30d = sum(
            1 for video in videos
            if video.published_at is not None
            and self._age_hours(video, now) <= 24 * 30
        )
        top_subscribers = [
            video.subscriber_count
            for video in top_videos
            if video.subscriber_count is not None
        ]
        top_views = [
            video.view_count
            for video in top_videos
            if video.view_count is not None
        ]

        duplicate_pairs = 0
        comparisons = 0
        titles = [video.title for video in videos if video.title]
        for index, title in enumerate(titles):
            for other in titles[index + 1:]:
                comparisons += 1
                if jaccard_similarity(title, other) >= 0.72:
                    duplicate_pairs += 1

        small_breakouts = [
            video for video in videos
            if (
                video.subscriber_count is not None
                and video.view_count is not None
                and video.subscriber_count <= 50000
                and video.view_count >= 100000
            )
        ]
        channel_counts: dict[str, int] = {}
        for video in videos:
            if video.channel_id:
                channel_counts[video.channel_id] = channel_counts.get(video.channel_id, 0) + 1
        dominant_share = safe_divide(max(channel_counts.values(), default=0), max(count, 1))

        supply_score = log_score(count, reference=100.0)
        authority_dominance_score = clamp(
            log_score(median(top_subscribers), reference=1_000_000.0) * 0.65 +
            dominant_share * 100.0 * 0.35
        )
        title_saturation_score = clamp(safe_divide(duplicate_pairs, max(comparisons, 1)) * 250.0)
        freshness_score = clamp(safe_divide(new_30d, max(count, 1)) * 100.0)
        small_breakout_score = clamp(safe_divide(len(small_breakouts), max(count, 1)) * 100.0)
        competition_score = weighted_score([
            (0.30, supply_score),
            (0.25, authority_dominance_score),
            (0.20, title_saturation_score),
            (0.15, freshness_score),
            (0.10, 100.0 - small_breakout_score),
        ])
        explanations = [
            f"{count} competing videos were available in the sample.",
            f"{duplicate_pairs} near-duplicate title pairs were detected.",
            f"{len(small_breakouts)} small-channel breakout videos were detected.",
        ]
        return CompetitionAnalysis(
            competition_score=competition_score,
            confidence_score=confidence_from_sample_size(count),
            competing_video_count=count,
            new_30d_count=new_30d,
            median_top_subscribers=median(top_subscribers),
            median_top_views=median(top_views),
            near_duplicate_title_count=duplicate_pairs,
            supply_score=supply_score,
            authority_dominance_score=authority_dominance_score,
            title_saturation_score=title_saturation_score,
            freshness_score=freshness_score,
            small_channel_breakout_score=small_breakout_score,
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
