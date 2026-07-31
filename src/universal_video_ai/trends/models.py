"""
Data models for Trend Scanner.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional


@dataclass
class TrendItemCandidate:
    """A candidate trending video item from any platform."""
    platform: str
    source_url: str
    title: Optional[str] = None
    author: Optional[str] = None
    thumbnail_url: Optional[str] = None
    duration_seconds: Optional[float] = None
    view_count: Optional[int] = None
    like_count: Optional[int] = None
    comment_count: Optional[int] = None
    share_count: Optional[int] = None
    published_at: Optional[str] = None
    trend_score: float = 0.0
    raw: Dict[str, Any] = field(default_factory=dict)

    def __hash__(self):
        return hash((self.platform, self.source_url))

    def __eq__(self, other):
        if not isinstance(other, TrendItemCandidate):
            return False
        return self.platform == other.platform and self.source_url == other.source_url


@dataclass
class TrendScanResult:
    """Result of a trend scan operation."""
    scan_id: str
    topic: str
    platforms: List[str]
    items: List[TrendItemCandidate]
    warnings: List[str] = field(default_factory=list)
    error: Optional[str] = None
    max_results: int = 20
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
