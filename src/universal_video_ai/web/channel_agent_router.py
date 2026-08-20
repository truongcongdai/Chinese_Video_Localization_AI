"""Authenticated FastAPI adapter for Channel Agent own-channel reads."""

from __future__ import annotations

from datetime import date
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


def _store_from_request(request: Request) -> Store:
    store = getattr(request.app.state, "store", None)
    if store is None:
        raise HTTPException(503, "Application storage is unavailable.")
    return store


def _service(store: Store) -> YouTubeReadOnlyService:
    return YouTubeReadOnlyService(GoogleOAuthTokenService(store))


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
