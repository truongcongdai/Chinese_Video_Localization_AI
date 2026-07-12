from __future__ import annotations

import os
from pathlib import Path

import yt_dlp

from .base import BaseDownloader
from .download_result import DownloadResult
from .platform import Platform

try:
    from universal_video_ai.config import COOKIE_DIR
except Exception:  # config import shouldn't be able to break downloading
    COOKIE_DIR = None


def _resolve_cookiefile() -> "str | None":
    """
    Find a Netscape-format cookies.txt to hand to yt-dlp, if one exists.

    Douyin (and sometimes TikTok) now reject anonymous requests with
    "Fresh cookies (not necessarily logged in) are needed" — yt-dlp's own
    documented workaround is supplying real browser cookies. Priority:
    1. DOUYIN_COOKIES_FILE env var (explicit override)
    2. <COOKIE_DIR>/douyin.txt (project convention — see README_WEB.md)
    Export cookies with a browser extension like "Get cookies.txt LOCALLY"
    while logged into douyin.com, and save the file at that path. This is
    read fresh on every download call, so updating the file takes effect
    immediately with no restart needed.
    """
    env_path = os.environ.get("DOUYIN_COOKIES_FILE")
    if env_path and Path(env_path).is_file():
        return env_path
    if COOKIE_DIR is not None:
        default_path = Path(COOKIE_DIR) / "douyin.txt"
        if default_path.is_file():
            return str(default_path)
    return None


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
        return {
            "http_headers": {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Referer": "https://www.douyin.com/",
            },
            "cookiefile": _resolve_cookiefile(),
            "nocheckcertificate": True,
            "ignoreerrors": True,
        }

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

            # Force Douyin extractor for Douyin URLs
            "force_generic_extractor": False,

            # TikTok/Douyin expose the SAME video as multiple format IDs:
            # a "download_addr" / "watermark" stream (the one their app
            # stamps with the logo + @username + account name when you use
            # its own "save video" feature) and a "play_addr"-style direct
            # stream (h264_*/bytevc1_* format ids) used for in-app playback,
            # which has NO watermark burned into the pixels at all. Plain
            # "bv*+ba/b" doesn't distinguish between them and can pick the
            # watermarked one, so we explicitly exclude any format whose id
            # contains "watermark" or "download_addr", preferring the clean
            # stream. If a given video genuinely has no clean format
            # available, the final "/bv*+ba/b" fallback still downloads
            # something rather than failing outright.
            "format": (
                "bestvideo[format_id!*=watermark][format_id!*=download_addr]"
                "+bestaudio[format_id!*=watermark][format_id!*=download_addr]"
                "/best[format_id!*=watermark][format_id!*=download_addr]"
                "/bv*+ba/b"
            ),

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

            if info is None:
                raise RuntimeError(f"yt-dlp failed to extract info from {url}")

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