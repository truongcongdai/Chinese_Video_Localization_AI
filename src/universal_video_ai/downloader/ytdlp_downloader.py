from __future__ import annotations

import os
import logging
from pathlib import Path

import yt_dlp

from .base import BaseDownloader
from .download_result import DownloadResult
from .platform import Platform
from universal_video_ai.cookies.manager import CookieManager

logger = logging.getLogger(__name__)

# Optional cookies for yt-dlp, only used if actually configured/present.
# Some platforms (Douyin in particular) intermittently require "fresh
# cookies (not necessarily logged in)" for their web-detail JSON endpoint.
# This is opt-in: if no env var is set, or the file doesn't exist, behavior
# is unchanged (no cookies sent) so platforms that don't need this keep
# working exactly as before.
#
# Set one of these to a Netscape-format cookies file exported from a
# logged-in (or even anonymous but cookie-primed) browser session, e.g.:
#   DOUYIN_COOKIES_FILE=/path/to/douyin_cookies.txt
#   YTDLP_COOKIES_FILE=/path/to/generic_cookies.txt   (fallback for any platform)
def _cookiefile_for(platform: Platform) -> str | None:
    env_key = f"{platform.name.upper()}_COOKIES_FILE"
    candidate = os.environ.get(env_key) or os.environ.get("YTDLP_COOKIES_FILE")
    if candidate and Path(candidate).is_file():
        return str(Path(candidate).resolve())
    domain_by_platform = {
        Platform.DOUYIN: "douyin.com",
        Platform.TIKTOK: "tiktok.com",
        Platform.YOUTUBE: "youtube.com",
        Platform.FACEBOOK: "facebook.com",
    }
    domain = domain_by_platform.get(platform)
    if domain:
        managed = CookieManager().find_cookie_for_domain(domain)
        if managed and managed.is_file():
            return str(managed)
    return None


def _cookies_from_browser_for(platform: Platform) -> tuple[str, ...] | None:
    """Return yt-dlp's browser-cookie tuple.

    Douyin channel scans use a dedicated, app-managed Chromium profile. Once
    that profile has a cookie database, prefer it over the user's normal
    Chrome profile so an open Chrome window cannot lock downloads out. An
    explicitly named/path profile (for example ``chrome:Profile 1``) still
    takes precedence.
    """
    env_key = f"{platform.name.upper()}_COOKIES_FROM_BROWSER"
    configured = (
        os.environ.get(env_key)
        or os.environ.get("YTDLP_COOKIES_FROM_BROWSER")
        or ""
    ).strip()

    if platform == Platform.DOUYIN and (not configured or ":" not in configured):
        configured_profile = (
            os.environ.get("DOUYIN_CHANNEL_BROWSER_USER_DATA_DIR") or ""
        ).strip()
        if configured_profile:
            managed_profile = Path(configured_profile).expanduser()
        else:
            project_root = Path(__file__).resolve().parents[3]
            managed_profile = (
                project_root / "local_data" / "browser_profiles" / "douyin_channel"
            )
        if (managed_profile / "Default" / "Network" / "Cookies").is_file():
            return ("chrome", str(managed_profile.resolve()))

    if not configured:
        return None
    browser, _, profile = configured.partition(":")
    browser = browser.strip().lower()
    if not browser:
        return None
    return (browser, profile.strip()) if profile.strip() else (browser,)


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

        cookiefile = _cookiefile_for(self.platform)
        if cookiefile:
            options["cookiefile"] = cookiefile
            logger.info("yt-dlp using cookie file for %s: %s", self.platform.value, cookiefile)
        else:
            browser_cookies = _cookies_from_browser_for(self.platform)
            if browser_cookies:
                options["cookiesfrombrowser"] = browser_cookies
                logger.info(
                    "yt-dlp loading %s cookies from browser %s",
                    self.platform.value,
                    browser_cookies[0],
                )

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

            description=info.get("description", "") or "",

            thumbnail_url=info.get("thumbnail", "") or "",

            tags=[str(item) for item in (info.get("tags") or []) if str(item).strip()],

            raw_metadata={
                "id": info.get("id"),
                "webpage_url": info.get("webpage_url"),
                "upload_date": info.get("upload_date"),
                "timestamp": info.get("timestamp"),
                "view_count": info.get("view_count"),
                "like_count": info.get("like_count"),
                "comment_count": info.get("comment_count"),
                "categories": info.get("categories") or [],
            },
        )
