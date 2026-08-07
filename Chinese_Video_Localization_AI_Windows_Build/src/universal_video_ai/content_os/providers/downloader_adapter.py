"""
Adapter to use existing downloader service as a trend provider.

This adapts the existing DownloadService from the video localization
pipeline to work as a trend provider for Content OS.
"""
import logging
from typing import List, Dict, Any, Optional
from pathlib import Path

from universal_video_ai.downloader.service import DownloadService
from universal_video_ai.downloader.platform_detector import Platform
from .base import TrendProvider, TrendSource

logger = logging.getLogger(__name__)


class DownloaderAdapter(TrendProvider):
    """
    Adapter that uses the existing DownloadService as a trend provider.
    
    This provider doesn't actually discover trends - it just validates
    URLs and provides metadata for manually-provided URLs.
    """
    
    def __init__(self, user_id: Optional[int] = None):
        self.user_id = user_id
        self.downloader = DownloadService(user_id=user_id)
        self.logger = logging.getLogger(__name__)
    
    @property
    def provider_name(self) -> str:
        return "downloader_adapter"
    
    @property
    def supported_platforms(self) -> List[str]:
        return ["youtube", "tiktok", "douyin", "kuaishou", "facebook"]
    
    def search_trends(
        self,
        query: str,
        platform: str,
        limit: int = 20,
        published_within_hours: int = 72,
    ) -> List[TrendSource]:
        """
        This adapter doesn't discover trends - it validates URLs.
        
        For actual trend discovery, use a dedicated trend provider.
        This method returns an empty list.
        """
        self.logger.warning(
            "DownloaderAdapter does not discover trends. "
            "Use a dedicated trend provider for trend discovery."
        )
        return []
    
    def is_available(self) -> bool:
        """Check if downloader is available."""
        return True  # DownloadService is always available
    
    def normalize_url(self, url: str) -> str:
        """Normalize URL to canonical form."""
        # Basic normalization - strip trailing slashes and fragments
        url = url.strip()
        if url.endswith("/"):
            url = url[:-1]
        return url
    
    def validate_url(self, url: str) -> Dict[str, Any]:
        """
        Validate a URL and extract metadata.
        
        Args:
            url: URL to validate
            
        Returns:
            Dictionary with validation result and metadata
        """
        try:
            platform = self.downloader.detector.detect(url)
            
            # GENERIC platform means the URL is not recognized
            if platform.value == "generic":
                return {
                    "valid": False,
                    "error": "Unrecognized URL format or platform",
                }
            
            return {
                "valid": True,
                "platform": platform.value,
                "canonical_url": self.normalize_url(url),
            }
        except Exception as e:
            self.logger.error(f"URL validation failed: {e}")
            return {
                "valid": False,
                "error": str(e),
            }
    
    def download_source(
        self,
        url: str,
        output_dir: Path,
    ) -> Dict[str, Any]:
        """
        Download a source using the downloader service.
        
        Args:
            url: URL to download
            output_dir: Output directory
            
        Returns:
            Download result
        """
        try:
            result = self.downloader.download(url, output_dir)
            
            return {
                "success": result.success,
                "platform": result.platform.value if result.success else None,
                "video_path": str(result.video_path) if result.success else None,
                "final_url": result.final_url if result.success else None,
            }
        except Exception as e:
            self.logger.error(f"Download failed: {e}")
            return {
                "success": False,
                "error": str(e),
            }
