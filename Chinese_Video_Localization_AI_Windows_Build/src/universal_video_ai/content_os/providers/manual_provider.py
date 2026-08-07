"""
Manual trend provider for user-provided URLs.

Allows users to manually provide URLs as trend sources
instead of relying on automated trend discovery.
"""
import logging
from typing import List, Dict, Any, Optional
from urllib.parse import urlparse

from .base import TrendProvider, TrendSource

logger = logging.getLogger(__name__)


class ManualProvider(TrendProvider):
    """
    Provider for manually-provided trend sources.
    
    Users can provide URLs directly, and this provider
    validates and normalizes them.
    """
    
    @property
    def provider_name(self) -> str:
        return "manual"
    
    @property
    def supported_platforms(self) -> List[str]:
        return ["youtube", "tiktok", "douyin", "kuaishou", "facebook", "instagram"]
    
    def search_trends(
        self,
        query: str,
        platform: str,
        limit: int = 20,
        published_within_hours: int = 72,
    ) -> List[TrendSource]:
        """
        Manual provider doesn't search - it validates provided URLs.
        
        This method returns an empty list.
        """
        return []
    
    def is_available(self) -> bool:
        """Manual provider is always available."""
        return True
    
    def validate_urls(self, urls: List[str]) -> List[Dict[str, Any]]:
        """
        Validate a list of URLs.
        
        Args:
            urls: List of URLs to validate
            
        Returns:
            List of validation results
        """
        results = []
        for url in urls:
            result = self.validate_url(url)
            results.append(result)
        return results
    
    def validate_url(self, url: str) -> Dict[str, Any]:
        """
        Validate a single URL.
        
        Args:
            url: URL to validate
            
        Returns:
            Validation result
        """
        try:
            parsed = urlparse(url)
            
            if not parsed.scheme or not parsed.netloc:
                return {
                    "valid": False,
                    "error": "Invalid URL format",
                }
            
            if parsed.scheme not in ["http", "https"]:
                return {
                    "valid": False,
                    "error": "URL must use http or https",
                }
            
            platform = self._detect_platform_from_url(url)
            canonical = self.normalize_url(url)
            
            return {
                "valid": True,
                "platform": platform,
                "canonical_url": canonical,
            }
        except Exception as e:
            logger.error(f"URL validation failed: {e}")
            return {
                "valid": False,
                "error": str(e),
            }
    
    def _detect_platform_from_url(self, url: str) -> str:
        """Detect platform from URL."""
        url_lower = url.lower()
        
        if "youtube.com" in url_lower or "youtu.be" in url_lower:
            return "youtube"
        elif "tiktok.com" in url_lower:
            return "tiktok"
        elif "douyin.com" in url_lower:
            return "douyin"
        elif "kuaishou.com" in url_lower:
            return "kuaishou"
        elif "facebook.com" in url_lower or "fb.watch" in url_lower:
            return "facebook"
        elif "instagram.com" in url_lower:
            return "instagram"
        else:
            return "other"
    
    def create_source_from_url(
        self,
        url: str,
        title: str,
        metrics: Optional[Dict[str, Any]] = None,
    ) -> TrendSource:
        """
        Create a TrendSource from a URL.
        
        Args:
            url: Source URL
            title: Title for the source
            metrics: Optional metrics
            
        Returns:
            TrendSource instance
        """
        platform = self._detect_platform_from_url(url)
        canonical = self.normalize_url(url)
        
        return TrendSource(
            platform=platform,
            source_url=url,
            canonical_url=canonical,
            title=title,
            metrics=metrics or {},
        )
