"""Small domain models needed by the Channel Agent CP0 foundation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional


class RightsStatus(str, Enum):
    """Known rights classifications for a research source.

    ``UNKNOWN`` is deliberately the default. Discovering public metadata does
    not establish that third-party media may be copied or republished.
    """

    UNKNOWN = "unknown"
    IDEA_ONLY = "idea_only"
    LICENSED = "licensed"
    OWNED = "owned"


@dataclass(frozen=True)
class SourceMetadata:
    """Platform-neutral metadata for a future research collector.

    CP0 defines the boundary only; it does not download sources or persist a
    new schema. Optional counters preserve the distinction between unavailable
    data and a measured zero.
    """

    platform: str
    source_id: str
    source_url: str
    title: str
    captured_at: datetime
    channel_id: str = ""
    channel_name: str = ""
    published_at: Optional[datetime] = None
    duration_seconds: Optional[float] = None
    views: Optional[int] = None
    likes: Optional[int] = None
    comments: Optional[int] = None
    rights_status: RightsStatus = RightsStatus.UNKNOWN


@dataclass(frozen=True)
class VideoMetricSnapshot:
    """Minimum input for calculating metadata deltas between two captures."""

    source_id: str
    captured_at: datetime
    views: Optional[int] = None
    likes: Optional[int] = None
    comments: Optional[int] = None
