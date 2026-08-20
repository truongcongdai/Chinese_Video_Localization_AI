"""Authenticated FastAPI adapter for Channel Agent own-channel reads."""

from __future__ import annotations

from datetime import date
import sqlite3
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from universal_video_ai import config
from universal_video_ai.channel_agent.service import ChannelAgentService
from universal_video_ai.channel_agent.youtube import (
    GoogleOAuthTokenService,
    YouTubeReadOnlyError,
    YouTubeReadOnlyService,
    default_date_range,
)
from universal_video_ai.channel_agent.trends import (
    MAX_QUERIES_PER_SCAN,
    MAX_RESULTS_PER_QUERY,
    MAX_ENRICHMENT_CHANNELS,
    TrendScanAlreadyRunning,
    TrendScanError,
    YouTubeTrendScanner,
    YouTubeTrendSearchProvider,
    trend_min_relevance,
)
from universal_video_ai.channel_agent.competitors import (
    MAX_COMPETITORS,
    RECENT_VIDEOS,
    CompetitorError,
    CompetitorIntelligenceService,
    CompetitorRefreshRunning,
    YouTubeCompetitorProvider,
    opportunity_gaps,
)
from universal_video_ai.web.auth import get_current_user_id
from universal_video_ai.web.store import Store


router = APIRouter(prefix="/api/channel-agent", tags=["channel-agent"])


class ChannelAgentStatusResponse(BaseModel):
    enabled: bool
    version: str
    youtube_connected: bool
    youtube_credential_present: bool
    youtube_connection_verified: Optional[bool]
    ollama_available: Optional[bool]


class TrendQueryBody(BaseModel):
    query: str
    relevance_language: Optional[str] = None
    region_code: Optional[str] = None
    published_within_days: int = 30
    duration_filter: str = "long"
    search_order: str = "date"
    enabled: bool = True
    topic_terms: Optional[str] = None
    exclusion_terms: Optional[str] = None
    notes: Optional[str] = None


class TrendQueryUpdateBody(BaseModel):
    query: Optional[str] = None
    relevance_language: Optional[str] = None
    region_code: Optional[str] = None
    published_within_days: Optional[int] = None
    duration_filter: Optional[str] = None
    search_order: Optional[str] = None
    enabled: Optional[bool] = None
    topic_terms: Optional[str] = None
    exclusion_terms: Optional[str] = None
    notes: Optional[str] = None


class CompetitorAddBody(BaseModel):
    reference: str
    notes: Optional[str] = None


class CompetitorRefreshBody(BaseModel):
    competitor_id: Optional[int] = None
    mode: str = "long"


def _store_from_request(request: Request) -> Store:
    store = getattr(request.app.state, "store", None)
    if store is None:
        raise HTTPException(503, "Application storage is unavailable.")
    return store


def _service(store: Store) -> YouTubeReadOnlyService:
    return YouTubeReadOnlyService(GoogleOAuthTokenService(store))


def _trend_service(store: Store) -> YouTubeTrendScanner:
    youtube = _service(store)
    return YouTubeTrendScanner(store, YouTubeTrendSearchProvider(youtube))


def _competitor_service(store: Store) -> CompetitorIntelligenceService:
    youtube = _service(store)
    return CompetitorIntelligenceService(store, YouTubeCompetitorProvider(youtube))


def _validate_trend_query(data: dict[str, Any]) -> dict[str, Any]:
    if "query" in data:
        data["query"] = str(data["query"] or "").strip()
        if not data["query"] or len(data["query"]) > 200:
            raise HTTPException(422, "Research query must contain 1–200 characters.")
    if data.get("published_within_days") is not None and not 1 <= int(data["published_within_days"]) <= 365:
        raise HTTPException(422, "published_within_days must be between 1 and 365.")
    if data.get("duration_filter") is not None and data["duration_filter"] not in {"any", "short", "medium", "long"}:
        raise HTTPException(422, "Unsupported duration_filter.")
    if data.get("search_order") is not None and data["search_order"] not in {"relevance", "date", "viewCount"}:
        raise HTTPException(422, "Unsupported search_order.")
    for field in ("topic_terms", "exclusion_terms"):
        if field in data and data[field] is not None:
            data[field] = str(data[field]).strip() or None
            if data[field] and len(data[field]) > 2000:
                raise HTTPException(422, f"{field} must contain at most 2000 characters.")
    return data


def _require_enabled() -> None:
    if not config.is_ai_channel_agent_enabled():
        raise HTTPException(404, "AI Channel Agent is disabled.")


def _dates(days: int, start_date: Optional[date], end_date: Optional[date]) -> tuple[date, date]:
    if start_date is None and end_date is None:
        return default_date_range(days)
    if start_date is None or end_date is None:
        raise HTTPException(422, "Provide both start_date and end_date, or neither.")
    if start_date > end_date:
        raise HTTPException(422, "start_date must not be after end_date.")
    if (end_date - start_date).days > 365:
        raise HTTPException(422, "Date range must not exceed 366 days.")
    return start_date, end_date


def _provider_call(call: Any) -> Any:
    try:
        return call()
    except YouTubeReadOnlyError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc


@router.get("/status", response_model=ChannelAgentStatusResponse)
def channel_agent_status(
    user_id: int = Depends(get_current_user_id),
    store: Store = Depends(_store_from_request),
) -> ChannelAgentStatusResponse:
    """Return cheap local credential state; no Google network probe."""

    enabled = config.is_ai_channel_agent_enabled()
    youtube = _service(store).connection_status(user_id) if enabled else None
    status = ChannelAgentService(
        enabled=enabled,
        youtube_connected=bool(youtube and youtube.connected),
        youtube_credential_present=bool(youtube and youtube.credential_present),
        youtube_connection_verified=(youtube.connection_verified if youtube else None),
        ollama_available=None,
    ).status()
    return ChannelAgentStatusResponse(**status.to_dict())


@router.get("/youtube/status")
def youtube_status(
    verify: bool = False,
    user_id: int = Depends(get_current_user_id),
    store: Store = Depends(_store_from_request),
) -> dict[str, Any]:
    _require_enabled()
    return _service(store).connection_status(user_id, verify=verify).to_dict()


@router.get("/youtube/channel")
def youtube_channel(
    user_id: int = Depends(get_current_user_id),
    store: Store = Depends(_store_from_request),
) -> dict[str, Any]:
    _require_enabled()
    return _provider_call(lambda: _service(store).get_own_channel(user_id)).to_dict()


@router.get("/youtube/overview")
def youtube_overview(
    days: int = Query(28, ge=1, le=366),
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    user_id: int = Depends(get_current_user_id),
    store: Store = Depends(_store_from_request),
) -> dict[str, Any]:
    _require_enabled()
    start, end = _dates(days, start_date, end_date)
    return _provider_call(lambda: _service(store).get_overview(user_id, start, end)).to_dict()


@router.get("/youtube/top-videos")
def youtube_top_videos(
    days: int = Query(28, ge=1, le=366),
    limit: int = Query(10, ge=1, le=25),
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    user_id: int = Depends(get_current_user_id),
    store: Store = Depends(_store_from_request),
) -> list[dict[str, Any]]:
    _require_enabled()
    start, end = _dates(days, start_date, end_date)
    result = _provider_call(
        lambda: _service(store).get_top_videos(user_id, start, end, limit=limit)
    )
    return [item.to_dict() for item in result]


@router.get("/youtube/traffic-sources")
def youtube_traffic_sources(
    days: int = Query(28, ge=1, le=366),
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    user_id: int = Depends(get_current_user_id),
    store: Store = Depends(_store_from_request),
) -> list[dict[str, Any]]:
    _require_enabled()
    start, end = _dates(days, start_date, end_date)
    result = _provider_call(lambda: _service(store).get_traffic_sources(user_id, start, end))
    return [item.to_dict() for item in result]


@router.get("/youtube/content-type")
def youtube_content_type(
    days: int = Query(28, ge=1, le=366),
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    user_id: int = Depends(get_current_user_id),
    store: Store = Depends(_store_from_request),
) -> dict[str, Any]:
    _require_enabled()
    start, end = _dates(days, start_date, end_date)
    return _provider_call(lambda: _service(store).get_content_types(user_id, start, end)).to_dict()


@router.get("/trends/queries")
def trend_queries(user_id: int = Depends(get_current_user_id),
                  store: Store = Depends(_store_from_request)) -> list[dict[str, Any]]:
    _require_enabled()
    return store.list_trend_queries(user_id)


@router.post("/trends/queries", status_code=201)
def create_trend_query(body: TrendQueryBody, user_id: int = Depends(get_current_user_id),
                       store: Store = Depends(_store_from_request)) -> dict[str, Any]:
    _require_enabled()
    data = _validate_trend_query(body.model_dump())
    try:
        query_id = store.create_trend_query(user_id, **data)
    except sqlite3.IntegrityError as exc:
        raise HTTPException(409, "This research query already exists.") from exc
    return next(item for item in store.list_trend_queries(user_id) if item["id"] == query_id)


@router.put("/trends/queries/{query_id}")
def update_trend_query(query_id: int, body: TrendQueryUpdateBody,
                       user_id: int = Depends(get_current_user_id),
                       store: Store = Depends(_store_from_request)) -> dict[str, Any]:
    _require_enabled()
    data = _validate_trend_query(body.model_dump(exclude_unset=True))
    try:
        updated = store.update_trend_query(user_id, query_id, **data)
    except sqlite3.IntegrityError as exc:
        raise HTTPException(409, "This research query already exists.") from exc
    if not updated:
        raise HTTPException(404, "Research query not found.")
    return next(item for item in store.list_trend_queries(user_id) if item["id"] == query_id)


@router.delete("/trends/queries/{query_id}", status_code=204)
def delete_trend_query(query_id: int, user_id: int = Depends(get_current_user_id),
                       store: Store = Depends(_store_from_request)) -> None:
    _require_enabled()
    if not store.delete_trend_query(user_id, query_id):
        raise HTTPException(404, "Research query not found.")


@router.post("/trends/scan")
def scan_trends(user_id: int = Depends(get_current_user_id),
                store: Store = Depends(_store_from_request)) -> dict[str, Any]:
    _require_enabled()
    try:
        return _trend_service(store).scan(user_id)
    except TrendScanAlreadyRunning as exc:
        raise HTTPException(409, str(exc)) from exc
    except TrendScanError as exc:
        raise HTTPException(422, str(exc)) from exc
    except YouTubeReadOnlyError as exc:
        message = str(exc)
        if exc.code == "youtube_quota_exceeded":
            message = "YouTube API quota is unavailable for this scan. Try again later or reduce the number of research queries."
        raise HTTPException(exc.status_code, {"code": exc.code, "message": message}) from exc


@router.get("/trends/status")
def trend_scan_status(user_id: int = Depends(get_current_user_id),
                      store: Store = Depends(_store_from_request)) -> dict[str, Any]:
    _require_enabled()
    return {
        "last_scan": store.latest_trend_scan(user_id),
        "limits": {"max_queries": MAX_QUERIES_PER_SCAN,
                   "results_per_query": MAX_RESULTS_PER_QUERY,
                   "max_enrichment_channels": MAX_ENRICHMENT_CHANNELS,
                   "min_relevance": trend_min_relevance()},
    }


@router.get("/trends/candidates")
def trend_candidates(limit: int = Query(50, ge=1, le=200),
                     min_score: float = Query(0.0, ge=0.0, le=1.0),
                     min_relevance: Optional[float] = Query(None, ge=0.0, le=1.0),
                     include_filtered: bool = Query(False),
                     user_id: int = Depends(get_current_user_id),
                     store: Store = Depends(_store_from_request)) -> list[dict[str, Any]]:
    _require_enabled()
    threshold = trend_min_relevance() if min_relevance is None else min_relevance
    return store.list_trend_candidates(
        user_id,
        limit=limit,
        min_score=min_score,
        min_relevance=0.0 if include_filtered and min_relevance is None else threshold,
        include_filtered=include_filtered,
    )


@router.get("/trends/candidates/{candidate_id}")
def trend_candidate_detail(candidate_id: int, user_id: int = Depends(get_current_user_id),
                           store: Store = Depends(_store_from_request)) -> dict[str, Any]:
    _require_enabled()
    candidate = store.get_trend_candidate(user_id, candidate_id)
    if not candidate:
        raise HTTPException(404, "Trend candidate not found.")
    candidate["snapshots"] = store.list_trend_snapshots(user_id, candidate_id)
    return candidate


@router.get("/competitors")
def competitors(include_filtered: bool = Query(False),
                user_id: int = Depends(get_current_user_id),
                store: Store = Depends(_store_from_request)) -> list[dict[str, Any]]:
    _require_enabled()
    return store.list_competitors(user_id, limit=MAX_COMPETITORS, include_filtered=include_filtered)


@router.post("/competitors/discover")
def discover_competitors(user_id: int = Depends(get_current_user_id),
                         store: Store = Depends(_store_from_request)) -> dict[str, Any]:
    _require_enabled()
    return _provider_call(lambda: _competitor_service(store).discover(user_id))


@router.post("/competitors", status_code=201)
def add_competitor(body: CompetitorAddBody, user_id: int = Depends(get_current_user_id),
                   store: Store = Depends(_store_from_request)) -> dict[str, Any]:
    _require_enabled()
    if not body.reference.strip() or len(body.reference) > 500:
        raise HTTPException(422, "Enter a valid YouTube channel URL, handle, or channel ID.")
    try:
        return _competitor_service(store).add(user_id, body.reference, body.notes)
    except CompetitorError as exc:
        raise HTTPException(422, str(exc)) from exc
    except YouTubeReadOnlyError as exc:
        raise HTTPException(exc.status_code, {"code": exc.code, "message": str(exc)}) from exc


@router.post("/competitors/refresh")
def refresh_competitors(body: CompetitorRefreshBody,
                        user_id: int = Depends(get_current_user_id),
                        store: Store = Depends(_store_from_request)) -> dict[str, Any]:
    _require_enabled()
    try:
        return _competitor_service(store).refresh(user_id, body.competitor_id, body.mode)
    except CompetitorRefreshRunning as exc:
        raise HTTPException(409, str(exc)) from exc
    except CompetitorError as exc:
        raise HTTPException(422, str(exc)) from exc
    except YouTubeReadOnlyError as exc:
        message = str(exc)
        if exc.code == "youtube_quota_exceeded":
            message = "YouTube API quota is unavailable for competitor refresh. Try fewer competitors or refresh later."
        raise HTTPException(exc.status_code, {"code": exc.code, "message": message}) from exc


@router.get("/competitors/gaps")
def competitor_gaps(include_filtered: bool = Query(False),
                    user_id: int = Depends(get_current_user_id),
                    store: Store = Depends(_store_from_request)) -> list[dict[str, Any]]:
    _require_enabled()
    channels = store.list_competitors(user_id, limit=MAX_COMPETITORS, include_filtered=True)
    candidates = store.list_trend_candidates(
        user_id, limit=200, min_relevance=trend_min_relevance(), include_filtered=False,
    )
    return opportunity_gaps(channels, candidates, include_filtered=include_filtered)


@router.get("/competitors/{competitor_id}")
def competitor_detail(competitor_id: int, user_id: int = Depends(get_current_user_id),
                      store: Store = Depends(_store_from_request)) -> dict[str, Any]:
    _require_enabled()
    competitor = store.get_competitor(user_id, competitor_id)
    if not competitor:
        raise HTTPException(404, "Competitor not found.")
    competitor["videos"] = store.list_competitor_videos(user_id, competitor_id, limit=RECENT_VIDEOS)
    competitor["snapshots"] = store.list_competitor_snapshots(user_id, competitor_id)
    return competitor


@router.get("/competitors/{competitor_id}/videos")
def competitor_videos(competitor_id: int, limit: int = Query(20, ge=1, le=100),
                      user_id: int = Depends(get_current_user_id),
                      store: Store = Depends(_store_from_request)) -> list[dict[str, Any]]:
    _require_enabled()
    if not store.get_competitor(user_id, competitor_id):
        raise HTTPException(404, "Competitor not found.")
    return store.list_competitor_videos(user_id, competitor_id, limit)


@router.get("/competitors/{competitor_id}/snapshots")
def competitor_snapshots(competitor_id: int, user_id: int = Depends(get_current_user_id),
                         store: Store = Depends(_store_from_request)) -> list[dict[str, Any]]:
    _require_enabled()
    if not store.get_competitor(user_id, competitor_id):
        raise HTTPException(404, "Competitor not found.")
    return store.list_competitor_snapshots(user_id, competitor_id)


@router.delete("/competitors/{competitor_id}", status_code=204)
def delete_competitor(competitor_id: int, user_id: int = Depends(get_current_user_id),
                      store: Store = Depends(_store_from_request)) -> None:
    _require_enabled()
    if not store.delete_competitor(user_id, competitor_id):
        raise HTTPException(404, "Competitor not found.")
