"""Lightweight YouTube opportunity research primitives.

This package is intentionally CPU-only and side-effect free at import time.
The package contains deterministic analyzers plus the bounded metadata
collector and application service used by the web research workspace.
"""

from .collector import (
    YtDlpYouTubeResearchCollector,
    YouTubeCollectorError,
    YouTubeCollectorTimeoutError,
    YouTubeCollectorUnavailableError,
    YouTubeResearchCollector,
)
from .competition_analyzer import CompetitionAnalyzer
from .opportunity_analyzer import OpportunityAnalyzer
from .schemas import (
    CompetitionAnalysis,
    OpportunityAnalysis,
    ResearchVideo,
    TrendAnalysis,
)
from .trend_analyzer import TrendAnalyzer
from .service import ResearchProjectNotFoundError, YouTubeResearchService

__all__ = [
    "CompetitionAnalysis",
    "CompetitionAnalyzer",
    "OpportunityAnalysis",
    "OpportunityAnalyzer",
    "ResearchVideo",
    "TrendAnalysis",
    "TrendAnalyzer",
    "YtDlpYouTubeResearchCollector",
    "YouTubeCollectorError",
    "YouTubeCollectorTimeoutError",
    "YouTubeCollectorUnavailableError",
    "YouTubeResearchCollector",
    "ResearchProjectNotFoundError",
    "YouTubeResearchService",
]
