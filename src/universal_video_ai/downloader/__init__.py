"""
Public API for the downloader package.

Export a concise set of symbols so consumers can import from
`universal_video_ai.downloader` directly.
"""

from __future__ import annotations

from .service import DownloadService
from .factory import DownloaderFactory
from .platform import Platform
from .platform_detector import PlatformDetector
from .download_result import DownloadResult
from .channel import (
    ChannelListingService, ChannelScanResult, ChannelVideoCandidate,
    URLClassification, URLIntent, VideoURLClassifier,
)
from .strategy import DownloadStrategy
from .validator import UrlValidator, FileValidator, validate_url_or_raise

__all__ = [
    "DownloadService",
    "DownloaderFactory",
    "Platform",
    "PlatformDetector",
    "DownloadResult",
    "ChannelListingService",
    "ChannelScanResult",
    "ChannelVideoCandidate",
    "URLClassification",
    "URLIntent",
    "VideoURLClassifier",
    "DownloadStrategy",
    "UrlValidator",
    "FileValidator",
    "validate_url_or_raise",
]