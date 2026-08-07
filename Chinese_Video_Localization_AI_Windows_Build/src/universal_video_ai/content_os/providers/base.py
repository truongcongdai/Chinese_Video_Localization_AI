"""
Base provider interface for trend sources.

All trend providers implement this interface for discovering
and fetching trending content from various platforms.
"""
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
from dataclasses import dataclass


@dataclass
class TrendSource:
    """A single trend source item."""
    platform: str
    source_url: str
    canonical_url: str
    title: str
    author: Optional[str] = None
    thumbnail_url: Optional[str] = None
    published_at: Optional[str] = None
    metrics: Dict[str, Any] = None
    raw_metadata: Dict[str, Any] = None
    
    def __post_init__(self):
        if self.metrics is None:
            self.metrics = {}
        if self.raw_metadata is None:
            self.raw_metadata = {}


class TrendProvider(ABC):
    """
    Base class for trend providers.
    
    Providers discover trending content from platforms like YouTube,
    TikTok, Douyin, etc.
    """
    
    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Return the provider name."""
        pass
    
    @property
    @abstractmethod
    def supported_platforms(self) -> List[str]:
        """Return list of supported platforms."""
        pass
    
    @abstractmethod
    def search_trends(
        self,
        query: str,
        platform: str,
        limit: int = 20,
        published_within_hours: int = 72,
    ) -> List[TrendSource]:
        """
        Search for trending content.
        
        Args:
            query: Search query or topic
            platform: Platform to search (youtube, tiktok, douyin, etc.)
            limit: Maximum number of results
            published_within_hours: Only return content from last N hours
            
        Returns:
            List of trend sources
        """
        pass
    
    @abstractmethod
    def is_available(self) -> bool:
        """Check if the provider is available (API keys, etc.)."""
        pass
    
    def normalize_url(self, url: str) -> str:
        """
        Normalize a URL to canonical form.
        
        Args:
            url: Original URL
            
        Returns:
            Canonical URL
        """
        return url
    
    def calculate_trend_score(self, source: TrendSource) -> float:
        """
        Calculate a trend score for a source.
        
        Default implementation uses view count and engagement rate.
        Subclasses can override for platform-specific scoring.
        
        Args:
            source: Trend source to score
            
        Returns:
            Score between 0 and 1
        """
        views = source.metrics.get("view_count", 0)
        likes = source.metrics.get("like_count", 0)
        comments = source.metrics.get("comment_count", 0)
        shares = source.metrics.get("share_count", 0)
        
        if views == 0:
            return 0.0
        
        # Engagement rate
        engagement = likes + comments + shares
        engagement_rate = engagement / views if views > 0 else 0
        
        # Log-normalize views (log10 of views / 10, capped at 1)
        import math
        view_score = min(math.log10(max(views, 1)) / 10, 1.0)
        
        # Combine view score and engagement rate
        score = (view_score * 0.7) + (min(engagement_rate * 10, 1.0) * 0.3)
        
        return score
