from __future__ import annotations

import math
import os
import sys
import unittest
from unittest.mock import patch
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from universal_video_ai.analytics.youtube_research import (  # noqa: E402
    CompetitionAnalyzer,
    OpportunityAnalyzer,
    ResearchVideo,
    TrendAnalyzer,
)
from universal_video_ai.analytics.youtube_research.normalization import clamp, jaccard_similarity  # noqa: E402
from universal_video_ai.analytics.youtube_research.scoring import confidence_factor  # noqa: E402


class YouTubeResearchTests(unittest.TestCase):
    def _videos(self) -> list[ResearchVideo]:
        now = datetime(2026, 7, 26, tzinfo=timezone.utc)
        return [
            ResearchVideo(
                video_id=f"v{idx}",
                channel_id=f"c{idx % 4}",
                title=f"Python automation tutorial for creators {idx}",
                published_at=now - timedelta(hours=idx * 12 + 1),
                view_count=1000 * (idx + 1),
                like_count=50 * (idx + 1),
                comment_count=5 * idx,
                subscriber_count=10_000 * (idx + 1),
            )
            for idx in range(12)
        ]

    def test_feature_flag_defaults_disabled(self) -> None:
        from universal_video_ai import config

        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(config._env_bool("YOUTUBE_RESEARCH_ENABLED", False))
            self.assertEqual(
                config._env_int("YOUTUBE_RESEARCH_MAX_CONCURRENT_JOBS", 1, minimum=1),
                1,
            )

    def test_score_clamp_and_confidence(self) -> None:
        self.assertEqual(clamp(float("nan")), 0.0)
        self.assertEqual(clamp(150), 100.0)
        self.assertEqual(clamp(-3), 0.0)
        self.assertAlmostEqual(confidence_factor(15, 30), 0.5)

    def test_trend_handles_missing_zero_and_outlier(self) -> None:
        now = datetime(2026, 7, 26, tzinfo=timezone.utc)
        videos = [
            ResearchVideo(video_id="a", title="", published_at=None, view_count=None),
            ResearchVideo(video_id="b", title="Fresh", published_at=now, view_count=0),
            ResearchVideo(video_id="c", title="Outlier", published_at=now, view_count=10**12, like_count=1),
        ]
        result = TrendAnalyzer().analyze(videos, now=now)
        self.assertGreaterEqual(result.trend_score, 0)
        self.assertLessEqual(result.trend_score, 100)
        self.assertFalse(math.isnan(result.median_view_velocity))

    def test_competition_detects_duplicate_titles(self) -> None:
        now = datetime(2026, 7, 26, tzinfo=timezone.utc)
        videos = self._videos()
        videos.append(ResearchVideo(video_id="dup1", title="How to automate Python for creators", published_at=now))
        videos.append(ResearchVideo(video_id="dup2", title="Automate Python for creators guide", published_at=now))
        result = CompetitionAnalyzer().analyze(videos, now=now)
        self.assertGreater(result.near_duplicate_title_count, 0)
        self.assertGreaterEqual(result.competition_score, 0)
        self.assertLessEqual(result.competition_score, 100)

    def test_opportunity_score_is_adjusted_by_confidence(self) -> None:
        now = datetime(2026, 7, 26, tzinfo=timezone.utc)
        trend = TrendAnalyzer().analyze(self._videos(), now=now)
        competition = CompetitionAnalyzer().analyze(self._videos(), now=now)
        result = OpportunityAnalyzer().analyze(
            trend,
            competition,
            content_gap_score=80,
            evergreen_score=70,
            monetization_potential_score=60,
        )
        self.assertGreaterEqual(result.adjusted_score, 0)
        self.assertLessEqual(result.adjusted_score, result.raw_score)
        self.assertFalse(result.metadata["score_is_prediction"])
        self.assertTrue(result.explanations)

    def test_text_similarity_pipeline(self) -> None:
        similarity = jaccard_similarity(
            "Beginner Python automation tutorial",
            "Python automation guide for beginners",
        )
        self.assertGreater(similarity, 0.25)


if __name__ == "__main__":
    unittest.main()
