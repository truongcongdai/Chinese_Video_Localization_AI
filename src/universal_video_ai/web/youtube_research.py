from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from universal_video_ai.analytics.youtube_research import (
    CompetitionAnalyzer,
    OpportunityAnalyzer,
    ResearchVideo,
    TrendAnalyzer,
)
from universal_video_ai.config import (
    YOUTUBE_RESEARCH_ENABLE_AI,
    YOUTUBE_RESEARCH_ENABLE_LOCAL_EMBEDDINGS,
    YOUTUBE_RESEARCH_ENABLE_OCR,
    YOUTUBE_RESEARCH_ENABLE_THUMBNAIL_FACE_DETECTION,
    YOUTUBE_RESEARCH_MAX_COMMENTS,
    YOUTUBE_RESEARCH_MAX_CONCURRENT_JOBS,
    YOUTUBE_RESEARCH_MAX_RESULTS,
)


router = APIRouter(prefix="/api/youtube-research", tags=["youtube-research"])


class ResearchVideoBody(BaseModel):
    video_id: str
    title: str
    channel_id: str = ""
    channel_title: str = ""
    published_at: Optional[datetime] = None
    view_count: Optional[int] = Field(default=None, ge=0)
    like_count: Optional[int] = Field(default=None, ge=0)
    comment_count: Optional[int] = Field(default=None, ge=0)
    subscriber_count: Optional[int] = Field(default=None, ge=0)
    description: str = ""
    duration_seconds: Optional[int] = Field(default=None, ge=0)
    thumbnail_url: str = ""
    search_query: str = ""


class OpportunityAnalyzeBody(BaseModel):
    videos: list[ResearchVideoBody] = Field(default_factory=list)
    content_gap_score: float = Field(default=50.0, ge=0.0, le=100.0)
    evergreen_score: float = Field(default=50.0, ge=0.0, le=100.0)
    monetization_potential_score: float = Field(default=50.0, ge=0.0, le=100.0)


@router.get("/status")
async def youtube_research_status() -> dict:
    return {
        "enabled": True,
        "max_concurrent_jobs": YOUTUBE_RESEARCH_MAX_CONCURRENT_JOBS,
        "max_results": YOUTUBE_RESEARCH_MAX_RESULTS,
        "max_comments": YOUTUBE_RESEARCH_MAX_COMMENTS,
        "ai_enabled": YOUTUBE_RESEARCH_ENABLE_AI,
        "local_embeddings_enabled": YOUTUBE_RESEARCH_ENABLE_LOCAL_EMBEDDINGS,
        "ocr_enabled": YOUTUBE_RESEARCH_ENABLE_OCR,
        "thumbnail_face_detection_enabled": YOUTUBE_RESEARCH_ENABLE_THUMBNAIL_FACE_DETECTION,
        "collector_enabled": False,
        "database_enabled": True,
    }


@router.post("/analyze/opportunity")
async def analyze_opportunity(body: OpportunityAnalyzeBody) -> dict:
    if not body.videos:
        raise HTTPException(status_code=400, detail="videos must not be empty")
    if len(body.videos) > YOUTUBE_RESEARCH_MAX_RESULTS:
        raise HTTPException(
            status_code=400,
            detail=f"videos exceeds YOUTUBE_RESEARCH_MAX_RESULTS={YOUTUBE_RESEARCH_MAX_RESULTS}",
        )

    videos = [
        ResearchVideo(
            video_id=item.video_id,
            title=item.title,
            channel_id=item.channel_id,
            channel_title=item.channel_title,
            published_at=item.published_at,
            view_count=item.view_count,
            like_count=item.like_count,
            comment_count=item.comment_count,
            subscriber_count=item.subscriber_count,
            description=item.description,
            duration_seconds=item.duration_seconds,
            thumbnail_url=item.thumbnail_url,
            search_query=item.search_query,
        )
        for item in body.videos
    ]
    trend = TrendAnalyzer().analyze(videos)
    competition = CompetitionAnalyzer().analyze(videos)
    opportunity = OpportunityAnalyzer().analyze(
        trend,
        competition,
        content_gap_score=body.content_gap_score,
        evergreen_score=body.evergreen_score,
        monetization_potential_score=body.monetization_potential_score,
    )
    return {
        "trend": asdict(trend),
        "competition": asdict(competition),
        "opportunity": asdict(opportunity),
    }
