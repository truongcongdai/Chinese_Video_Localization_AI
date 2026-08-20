"""Read-only YouTube contracts for the future CP1 integration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class YouTubeChannelIdentity:
    channel_id: str
    title: str


class YouTubeChannelService(Protocol):
    """Boundary for authenticated, read-only own-channel data."""

    def is_connected(self) -> bool:
        """Return whether a usable Channel Agent connection exists."""

        ...

    def get_own_channel(self) -> YouTubeChannelIdentity:
        """Return the authenticated channel identity."""

        ...


class YouTubeAnalyticsService(Protocol):
    """Boundary for read-only YouTube Analytics queries."""

    def get_channel_summary(self) -> dict[str, Any]:
        """Return a provider-neutral own-channel analytics summary."""

        ...
