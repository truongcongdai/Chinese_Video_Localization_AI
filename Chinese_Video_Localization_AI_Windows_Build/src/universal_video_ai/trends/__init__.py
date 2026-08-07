"""
Trend Scanner module for discovering trending videos across platforms.

This module provides a unified interface for scanning trending content from
multiple platforms, with optional Agent-Reach integration as a fallback provider.
"""

from universal_video_ai.trends.models import TrendItemCandidate, TrendScanResult
from universal_video_ai.trends.service import TrendScanner

__all__ = ["TrendItemCandidate", "TrendScanResult", "TrendScanner"]
