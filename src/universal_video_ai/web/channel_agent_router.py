"""FastAPI adapter for the opt-in AI Channel Agent foundation."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

from universal_video_ai import config
from universal_video_ai.channel_agent.service import ChannelAgentService


router = APIRouter(prefix="/api/channel-agent", tags=["channel-agent"])


class ChannelAgentStatusResponse(BaseModel):
    enabled: bool
    version: str
    youtube_connected: bool
    ollama_available: Optional[bool]


@router.get("/status", response_model=ChannelAgentStatusResponse)
def channel_agent_status() -> ChannelAgentStatusResponse:
    """Return local CP0 state without credentials or network probes."""

    status = ChannelAgentService(
        enabled=config.is_ai_channel_agent_enabled(),
        # CP1 will supply an authenticated, read-only YouTube implementation.
        youtube_connected=False,
        # CP0 deliberately does not contact Ollama merely to report status.
        ollama_available=None,
    ).status()
    return ChannelAgentStatusResponse(**status.to_dict())
