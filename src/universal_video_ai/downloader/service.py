from __future__ import annotations

from pathlib import Path

from .factory import DownloaderFactory

from .platform_detector import PlatformDetector

from .download_result import DownloadResult


class DownloadService:

    """
    Public API for downloading videos.

    Nobody should instantiate downloaders directly.

    Always use this service.
    """

    def __init__(self):

        self.detector = PlatformDetector()

    def download(

        self,

        url: str,

        output_dir: Path,

    ) -> DownloadResult:

        platform = self.detector.detect(url)

        downloader = DownloaderFactory.create(
            platform
        )

        return downloader.download(
            url=url,
            output_dir=output_dir,
        )