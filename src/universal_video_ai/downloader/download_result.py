from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

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

    description: str = ""

    thumbnail_url: str = ""

    tags: List[str] = field(default_factory=list)

    raw_metadata: Dict[str, Any] = field(default_factory=dict)