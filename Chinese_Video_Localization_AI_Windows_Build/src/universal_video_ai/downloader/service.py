from __future__ import annotations

from pathlib import Path
from typing import Optional

from .factory import DownloaderFactory
from .platform_detector import PlatformDetector
from .download_result import DownloadResult
from .rate_limiter import get_rate_limiter
from .download_cache import get_download_cache


class DownloadService:

    """
    Public API for downloading videos.

    Nobody should instantiate downloaders directly.

    Always use this service.
    """

    def __init__(self, user_id: Optional[int] = None, use_cache: bool = True):

        self.detector = PlatformDetector()
        self.user_id = user_id
        self.rate_limiter = get_rate_limiter()
        self.use_cache = use_cache
        self.cache = get_download_cache() if use_cache else None

    def download(

        self,

        url: str,

        output_dir: Path,

    ) -> DownloadResult:
        # Check cache first
        if self.cache:
            cached_path = self.cache.get(url)
            if cached_path:
                # Copy from cache to output dir
                import shutil
                output_path = output_dir / cached_path.name
                shutil.copy2(cached_path, output_path)
                
                # Return cached result
                from .platform import Platform
                return DownloadResult(
                    success=True,
                    platform=Platform.OTHER,
                    original_url=url,
                    final_url=url,
                    video_path=output_path,
                )
        
        # Rate limiting is handled at the caller level (async)
        # This sync method just performs the download
        platform = self.detector.detect(url)

        downloader = DownloaderFactory.create(
            platform
        )

        result = downloader.download(
            url=url,
            output_dir=output_dir,
        )
        
        # Cache successful downloads
        if result.success and self.cache:
            self.cache.put(url, result.video_path)
        
        return result