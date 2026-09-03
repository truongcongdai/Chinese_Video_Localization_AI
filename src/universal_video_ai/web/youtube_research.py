from __future__ import annotations

import asyncio
from dataclasses import asdict
from datetime import datetime
import inspect
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from universal_video_ai import config
from universal_video_ai.analytics.youtube_research import (
    CompetitionAnalyzer,
    OpportunityAnalyzer,
    ResearchProjectNotFoundError,
    ResearchVideo,
    TrendAnalyzer,
    YouTubeCollectorError,
    YouTubeCollectorTimeoutError,
    YouTubeCollectorUnavailableError,
    YouTubeResearchService,
    YtDlpYouTubeResearchCollector,
)

from .auth import get_current_user_id


router = APIRouter(prefix="/api/youtube-research", tags=["youtube-research"])
_PROJECT_SCAN_LOCKS: dict[tuple[int, int, int], asyncio.Lock] = {}


class ResearchProjectBody(BaseModel):
    niche: str = Field(default="", max_length=200)
    keyword: str = Field(default="", max_length=200)
    target_language: Optional[str] = Field(default=None, max_length=32)
    target_country: Optional[str] = Field(default=None, max_length=64)


class ResearchScanBody(BaseModel):
    max_results: int = Field(default=20, ge=1, le=config.YOUTUBE_RESEARCH_MAX_RESULTS)


class LocalizeCandidateBody(BaseModel):
    video_id: str = Field(min_length=1, max_length=128)
    target_language: str = Field(default="vi", min_length=1, max_length=32)


class ResearchVideoBody(BaseModel):
    video_id: str
    title: str
    canonical_url: str = ""
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


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


def _require_feature() -> None:
    if not config.YOUTUBE_RESEARCH_ENABLED:
        raise _error(403, "feature_disabled", "YouTube Research is disabled")


def _service(request: Request) -> YouTubeResearchService:
    repository = getattr(request.app.state, "youtube_research_repository", None)
    collector = getattr(request.app.state, "youtube_research_collector", None)
    if repository is None:
        raise _error(503, "database_unavailable", "YouTube Research database is unavailable")
    if collector is None:
        raise _error(503, "collector_unavailable", "YouTube collector is unavailable")
    return YouTubeResearchService(
        repository,
        collector,
        hard_max_results=config.YOUTUBE_RESEARCH_MAX_RESULTS,
    )


def _project_dict(project: Any) -> dict[str, Any]:
    return asdict(project)


def _project_scan_lock(request: Request, user_id: int, project_id: int) -> asyncio.Lock:
    key = (id(request.app), int(user_id), int(project_id))
    lock = _PROJECT_SCAN_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _PROJECT_SCAN_LOCKS[key] = lock
    return lock


@router.get("/status")
async def youtube_research_status(
    request: Request,
    _user_id: int = Depends(get_current_user_id),
) -> dict[str, Any]:
    configured_collector = getattr(
        request.app.state, "youtube_research_collector", None
    )
    available = configured_collector is not None and (
        not isinstance(configured_collector, YtDlpYouTubeResearchCollector)
        or configured_collector.is_available()
    )
    return {
        "enabled": config.YOUTUBE_RESEARCH_ENABLED,
        "max_concurrent_jobs": config.YOUTUBE_RESEARCH_MAX_CONCURRENT_JOBS,
        "max_results": config.YOUTUBE_RESEARCH_MAX_RESULTS,
        "max_comments": config.YOUTUBE_RESEARCH_MAX_COMMENTS,
        "ai_enabled": config.YOUTUBE_RESEARCH_ENABLE_AI,
        "local_embeddings_enabled": config.YOUTUBE_RESEARCH_ENABLE_LOCAL_EMBEDDINGS,
        "ocr_enabled": config.YOUTUBE_RESEARCH_ENABLE_OCR,
        "thumbnail_face_detection_enabled": (
            config.YOUTUBE_RESEARCH_ENABLE_THUMBNAIL_FACE_DETECTION
        ),
        "collector_available": available,
        "collector_enabled": config.YOUTUBE_RESEARCH_ENABLED and available,
        "database_enabled": (
            getattr(request.app.state, "youtube_research_repository", None) is not None
        ),
    }


@router.post("/projects")
async def create_research_project(
    body: ResearchProjectBody,
    request: Request,
    user_id: int = Depends(get_current_user_id),
) -> dict[str, Any]:
    _require_feature()
    try:
        return _project_dict(
            _service(request).create_project(
                user_id,
                niche=body.niche,
                keyword=body.keyword,
                target_language=body.target_language,
                target_country=body.target_country,
            )
        )
    except ValueError as exc:
        raise _error(400, "invalid_research_project", str(exc)) from exc


@router.get("/projects")
async def list_research_projects(
    request: Request,
    user_id: int = Depends(get_current_user_id),
) -> list[dict[str, Any]]:
    _require_feature()
    return [_project_dict(item) for item in _service(request).list_projects(user_id)]


@router.get("/projects/{project_id}")
async def get_research_project(
    project_id: int,
    request: Request,
    user_id: int = Depends(get_current_user_id),
) -> dict[str, Any]:
    _require_feature()
    try:
        return _project_dict(_service(request).get_project(project_id, user_id))
    except ResearchProjectNotFoundError as exc:
        raise _error(404, "project_not_found", "Research project not found") from exc


@router.post("/projects/{project_id}/scan")
async def scan_research_project(
    project_id: int,
    body: ResearchScanBody,
    request: Request,
    user_id: int = Depends(get_current_user_id),
) -> dict[str, Any]:
    _require_feature()
    try:
        async with _project_scan_lock(request, user_id, project_id):
            return await _service(request).scan(
                project_id, user_id, max_results=body.max_results
            )
    except ResearchProjectNotFoundError as exc:
        raise _error(404, "project_not_found", "Research project not found") from exc
    except YouTubeCollectorUnavailableError as exc:
        raise _error(503, "collector_unavailable", str(exc)) from exc
    except YouTubeCollectorTimeoutError as exc:
        raise _error(504, "collector_timeout", str(exc)) from exc
    except YouTubeCollectorError as exc:
        raise _error(503, "collector_failed", str(exc)) from exc
    except ValueError as exc:
        raise _error(400, "invalid_scan", str(exc)) from exc


@router.get("/projects/{project_id}/results")
async def get_research_results(
    project_id: int,
    request: Request,
    user_id: int = Depends(get_current_user_id),
) -> dict[str, Any]:
    _require_feature()
    try:
        results = _service(request).ranked_results(project_id, user_id)
    except ResearchProjectNotFoundError as exc:
        raise _error(404, "project_not_found", "Research project not found") from exc
    return {"project_id": project_id, "results": results}


@router.post("/projects/{project_id}/localize")
async def localize_research_candidate(
    project_id: int,
    body: LocalizeCandidateBody,
    request: Request,
    user_id: int = Depends(get_current_user_id),
) -> dict[str, Any]:
    _require_feature()
    service = _service(request)
    try:
        service.get_project(project_id, user_id)
    except ResearchProjectNotFoundError as exc:
        raise _error(404, "project_not_found", "Research project not found") from exc
    video = service.repository.get_video(project_id, user_id, body.video_id)
    if video is None:
        raise _error(404, "candidate_not_found", "Research candidate not found")
    submit = getattr(request.app.state, "youtube_research_submit_localization", None)
    if not callable(submit):
        raise _error(503, "localization_unavailable", "Localization submission is unavailable")
    result = submit(video["canonical_url"], body.target_language, user_id)
    if inspect.isawaitable(result):
        result = await result
    return result


@router.post("/analyze/opportunity")
async def analyze_opportunity(
    body: OpportunityAnalyzeBody,
    _user_id: int = Depends(get_current_user_id),
) -> dict[str, Any]:
    _require_feature()
    if not body.videos:
        raise _error(400, "empty_sample", "videos must not be empty")
    if len(body.videos) > config.YOUTUBE_RESEARCH_MAX_RESULTS:
        raise _error(
            400,
            "sample_too_large",
            f"videos exceeds YOUTUBE_RESEARCH_MAX_RESULTS={config.YOUTUBE_RESEARCH_MAX_RESULTS}",
        )

    videos = [
        ResearchVideo(
            video_id=item.video_id,
            title=item.title,
            canonical_url=item.canonical_url,
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
