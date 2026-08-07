"""Lightweight YouTube opportunity research primitives.

This package is intentionally CPU-only and side-effect free at import time.
Phase 2 exposes scoring and analyzer building blocks only; API, jobs,
database persistence, collectors, and frontend integration are added later.
"""

from .competition_analyzer import CompetitionAnalyzer
from .opportunity_analyzer import OpportunityAnalyzer
from .schemas import (
    CompetitionAnalysis,
    OpportunityAnalysis,
    ResearchVideo,
    TrendAnalysis,
)
from .trend_analyzer import TrendAnalyzer

__all__ = [
    "CompetitionAnalysis",
    "CompetitionAnalyzer",
    "OpportunityAnalysis",
    "OpportunityAnalyzer",
    "ResearchVideo",
    "TrendAnalysis",
    "TrendAnalyzer",
]
