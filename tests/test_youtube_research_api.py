from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from universal_video_ai.web.youtube_research import (
    OpportunityAnalyzeBody,
    analyze_opportunity,
    youtube_research_status,
)


def test_youtube_research_status_endpoint() -> None:
    payload = asyncio.run(youtube_research_status())
    assert payload["enabled"] is True
    assert payload["collector_enabled"] is False
    assert payload["database_enabled"] is True


def test_youtube_research_opportunity_endpoint() -> None:
    payload = asyncio.run(
        analyze_opportunity(
            OpportunityAnalyzeBody.model_validate({
            "videos": [
                {
                    "video_id": "v1",
                    "title": "Python automation tutorial for beginners",
                    "published_at": datetime(2026, 7, 25, tzinfo=timezone.utc).isoformat(),
                    "view_count": 10000,
                    "like_count": 700,
                    "comment_count": 80,
                    "subscriber_count": 20000,
                },
                {
                    "video_id": "v2",
                    "title": "Python automation guide for creators",
                    "published_at": datetime(2026, 7, 20, tzinfo=timezone.utc).isoformat(),
                    "view_count": 8000,
                    "like_count": 500,
                    "comment_count": 40,
                    "subscriber_count": 15000,
                },
            ],
            "content_gap_score": 75,
            "evergreen_score": 80,
            "monetization_potential_score": 60,
            })
        )
    )
    assert 0 <= payload["trend"]["trend_score"] <= 100
    assert 0 <= payload["competition"]["competition_score"] <= 100
    assert 0 <= payload["opportunity"]["adjusted_score"] <= payload["opportunity"]["raw_score"]
    assert payload["opportunity"]["metadata"]["score_is_prediction"] is False


def test_youtube_research_opportunity_rejects_empty_sample() -> None:
    with pytest.raises(HTTPException) as exc:
        asyncio.run(analyze_opportunity(OpportunityAnalyzeBody(videos=[])))
    assert exc.value.status_code == 400
