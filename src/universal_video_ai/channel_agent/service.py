"""Minimal status service for the AI Channel Agent subsystem."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Optional


@dataclass(frozen=True)
class ChannelAgentStatus:
    enabled: bool
    version: str = "mvp"
    youtube_connected: bool = False
    youtube_credential_present: bool = False
    youtube_connection_verified: Optional[bool] = None
    ollama_available: Optional[bool] = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class ChannelAgentService:
    """Report CP0 state without making external calls or starting workers."""

    def __init__(
        self,
        *,
        enabled: bool,
        youtube_connected: bool = False,
        youtube_credential_present: bool = False,
        youtube_connection_verified: Optional[bool] = None,
        ollama_available: Optional[bool] = None,
    ) -> None:
        self._enabled = bool(enabled)
        self._youtube_connected = bool(youtube_connected)
        self._youtube_credential_present = bool(youtube_credential_present)
        self._youtube_connection_verified = youtube_connection_verified
        self._ollama_available = ollama_available

    def status(self) -> ChannelAgentStatus:
        """Return truthful local state; unknown checks remain ``None``."""

        return ChannelAgentStatus(
            enabled=self._enabled,
            youtube_connected=self._youtube_connected if self._enabled else False,
            youtube_credential_present=(
                self._youtube_credential_present if self._enabled else False
            ),
            youtube_connection_verified=(
                self._youtube_connection_verified if self._enabled else None
            ),
            ollama_available=self._ollama_available if self._enabled else None,
        )
