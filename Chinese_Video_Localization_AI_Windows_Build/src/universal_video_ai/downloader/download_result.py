from dataclasses import dataclass
from pathlib import Path

from .platform import Platform


@dataclass(slots=True)
class DownloadResult:
    """
    Result returned by every downloader.

    Every downloader (YouTube, Facebook, Douyin...)
    MUST return this object.
    """

    success: bool

    platform: Platform

    original_url: str

    final_url: str

    video_path: Path

    title: str = ""

    uploader: str = ""

    duration: float = 0.0

    width: int = 0

    height: int = 0

    filesize: int = 0

    extension: str = "mp4"