from __future__ import annotations

from pathlib import Path

import yt_dlp

from .base import BaseDownloader
from .download_result import DownloadResult
from .platform import Platform


class YTDLPDownloader(BaseDownloader):
    """
    Generic downloader powered by yt-dlp.

    All platform downloaders inherit from this class.
    """

    def __init__(self, platform: Platform):
        super().__init__(platform)

    # ---------------------------------------------------------

    def get_extra_options(self) -> dict:
        """
        Platform specific options.

        Override in subclasses if needed.
        """

        return {}

    # ---------------------------------------------------------

    def download(
        self,
        url: str,
        output_dir: Path,
    ) -> DownloadResult:

        output_dir.mkdir(parents=True, exist_ok=True)

        output_template = str(output_dir / "%(title)s.%(ext)s")

        options = {

            "outtmpl": output_template,

            "format": "bv*+ba/b",

            "merge_output_format": "mp4",

            "noplaylist": True,

            "quiet": False,

            "no_warnings": False,

            "writesubtitles": False,

            "writeautomaticsub": False,

        }

        options.update(self.get_extra_options())

        with yt_dlp.YoutubeDL(options) as ydl:

            info = ydl.extract_info(
                url,
                download=True,
            )

            filepath = Path(
                ydl.prepare_filename(info)
            ).with_suffix(".mp4")

        return DownloadResult(

            success=True,

            platform=self.platform,

            original_url=url,

            final_url=info.get("webpage_url", url),

            video_path=filepath,

            title=info.get("title", ""),

            uploader=info.get("uploader", ""),

            duration=info.get("duration", 0),

            width=info.get("width", 0),

            height=info.get("height", 0),

            filesize=info.get("filesize", 0),

            extension="mp4",
        )