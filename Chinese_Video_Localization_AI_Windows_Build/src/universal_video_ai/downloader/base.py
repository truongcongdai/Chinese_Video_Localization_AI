from abc import ABC, abstractmethod
from pathlib import Path

from .download_result import DownloadResult
from .platform import Platform


class BaseDownloader(ABC):
    """
    Abstract downloader.

    Every downloader must inherit this class.
    """

    def __init__(self, platform: Platform):
        self.platform = platform

    @abstractmethod
    def download(
        self,
        url: str,
        output_dir: Path,
    ) -> DownloadResult:
        """
        Download a video.

        Parameters
        ----------
        url:
            Original URL.

        output_dir:
            Directory where the video should be saved.

        Returns
        -------
        DownloadResult
        """
        raise NotImplementedError