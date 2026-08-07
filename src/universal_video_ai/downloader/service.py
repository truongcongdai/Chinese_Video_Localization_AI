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
            cached_entry = self.cache.get_entry(url)
            if cached_entry:
                # Copy from cache to output dir while preserving the metadata
                # used by the Reup Publishing Pack. Older cache entries simply
                # fall back to empty metadata without breaking downloads.
                import shutil
                cached_path = Path(cached_entry["video_path"])
                output_path = output_dir / cached_path.name
                shutil.copy2(cached_path, output_path)

                from .platform import Platform
                metadata = dict(cached_entry.get("source_metadata") or {})
                platform_value = str(metadata.get("platform") or "other").lower()
                try:
                    cached_platform = Platform(platform_value)
                except ValueError:
                    cached_platform = Platform.OTHER
                return DownloadResult(
                    success=True,
                    platform=cached_platform,
                    original_url=url,
                    final_url=str(metadata.get("final_url") or url),
                    video_path=output_path,
                    title=str(metadata.get("title") or ""),
                    uploader=str(metadata.get("uploader") or ""),
                    duration=float(metadata.get("duration") or 0.0),
                    width=int(metadata.get("width") or 0),
                    height=int(metadata.get("height") or 0),
                    filesize=int(metadata.get("filesize") or output_path.stat().st_size),
                    extension=str(metadata.get("extension") or output_path.suffix.lstrip(".") or "mp4"),
                    description=str(metadata.get("description") or ""),
                    thumbnail_url=str(metadata.get("thumbnail_url") or ""),
                    tags=[str(item) for item in (metadata.get("tags") or [])],
                    raw_metadata=dict(metadata.get("raw_metadata") or {}),
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
            platform_value = getattr(result.platform, "value", str(result.platform))
            self.cache.put(
                url,
                result.video_path,
                source_metadata={
                    "platform": platform_value,
                    "final_url": result.final_url,
                    "title": result.title,
                    "uploader": result.uploader,
                    "duration": result.duration,
                    "width": result.width,
                    "height": result.height,
                    "filesize": result.filesize,
                    "extension": result.extension,
                    "description": result.description,
                    "thumbnail_url": result.thumbnail_url,
                    "tags": list(result.tags or []),
                    "raw_metadata": dict(result.raw_metadata or {}),
                },
            )
        
        return result