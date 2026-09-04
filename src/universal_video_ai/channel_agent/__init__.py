"""Opt-in AI Channel Agent foundation.

The localization pipeline does not import this package. Channel Agent code may
reuse existing application services, keeping the dependency direction one-way.
"""

from .models import RightsStatus, SourceMetadata, VideoMetricSnapshot
from .service import ChannelAgentService, ChannelAgentStatus

__all__ = [
    "ChannelAgentService",
    "ChannelAgentStatus",
    "RightsStatus",
    "SourceMetadata",
    "VideoMetricSnapshot",
]
