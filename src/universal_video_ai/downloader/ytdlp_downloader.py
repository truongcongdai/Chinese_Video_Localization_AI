from __future__ import annotations

import os
import sys
import logging
from pathlib import Path

import yt_dlp

from .base import BaseDownloader
from .download_result import DownloadResult
from .platform import Platform

logger = logging.getLogger(__name__)


def _downloaded_filepath(ydl, info: dict) -> Path:
    """Resolve the file yt-dlp actually produced after merge/remux."""
    candidates = []
    if info.get("filepath"):
        candidates.append(Path(info["filepath"]))
    if info.get("_filename"):
        candidates.append(Path(info["_filename"]))

    prepared = Path(ydl.prepare_filename(info))
    candidates.extend((prepared.with_suffix(".mp4"), prepared))
    # Component paths are last: for split DASH downloads these can be the
    # video-only/audio-only inputs rather than the final merged output.
    for item in info.get("requested_downloads") or ():
        if isinstance(item, dict) and item.get("filepath"):
            candidates.append(Path(item["filepath"]))
    for candidate in candidates:
        if candidate.exists():
            return candidate
    # Unit-test/dry-run fallback; normal downloads return an existing path.
    return prepared.with_suffix(".mp4")


class DouyinCookiesRequiredError(RuntimeError):
    """Raised after every safe automatic Douyin cookie source was exhausted."""


def _browser_cookie_candidates(platform: Platform) -> list[tuple[str, ...]]:
    """Return browser profiles to try without exporting cookies to disk."""
    configured = os.environ.get(f"{platform.name.upper()}_COOKIES_FROM_BROWSER")
    if configured is None:
        configured = os.environ.get("YTDLP_COOKIES_FROM_BROWSER")
    if configured is not None:
        value = configured.strip()
        if value.lower() in {"", "0", "false", "no", "off", "none"}:
            return []
        return [(name.strip().lower(),) for name in value.split(",") if name.strip()]

    if platform != Platform.DOUYIN:
        return []
    # Most Windows builds are used with Edge or Chrome. Firefox is also
    # included because yt-dlp documents it as the most reliable cookie source.
    if os.name == "nt":
        return [("edge",), ("chrome",), ("firefox",)]
    if sys.platform == "darwin":
        return [("chrome",), ("safari",), ("firefox",)]
    return [("chrome",), ("chromium",), ("firefox",)]


def _is_cookie_error(exc: BaseException) -> bool:
    message = str(exc).lower()
    return any(marker in message for marker in (
        "cookie", "failed to load cookies", "could not find", "decrypt",
    ))

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
        return candidate
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
                + ("" if self.platform in {Platform.TIKTOK, Platform.DOUYIN} else "/bv*+ba/b")
            ),

            "merge_output_format": "mp4",

            "noplaylist": True,

            "quiet": False,

            "no_warnings": False,

            "writesubtitles": False,

            "writeautomaticsub": False,

        }

        options.update(self.get_extra_options())
        cookiefile = _cookiefile_for(self.platform)
        cookie_attempts: list[dict] = []
        if cookiefile:
            cookie_attempts.append({"cookiefile": cookiefile})
        elif self.platform == Platform.DOUYIN:
            cookie_attempts.extend(
                {"cookiesfrombrowser": candidate}
                for candidate in _browser_cookie_candidates(self.platform)
            )
            # Preserve the previous cookie-less behavior as the last attempt.
            cookie_attempts.append({})
        else:
            cookie_attempts.append({})

        last_cookie_error: BaseException | None = None
        for cookie_options in cookie_attempts:
            attempt_options = {**options, **cookie_options}
            browser = cookie_options.get("cookiesfrombrowser")
            if browser:
                logging_label = browser[0]
                logger.info("Douyin: trying fresh cookies from %s", logging_label)
            try:
                with yt_dlp.YoutubeDL(attempt_options) as ydl:
                    info = ydl.extract_info(url, download=True)
                    filepath = _downloaded_filepath(ydl, info)
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
                    extension=filepath.suffix.lstrip(".") or "mp4",
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
            except Exception as exc:
                if self.platform != Platform.DOUYIN or not _is_cookie_error(exc):
                    raise
                last_cookie_error = exc

        browsers = ", ".join(item[0] for item in _browser_cookie_candidates(self.platform))
        detail = f" Đã tự thử: {browsers}." if browsers else ""
        raise DouyinCookiesRequiredError(
            "Douyin yêu cầu cookie mới. Hãy mở Douyin bằng Edge, Chrome hoặc Firefox "
            "trên máy chạy ứng dụng, tải lại trang Douyin một lần rồi bấm Thử lại."
            f"{detail} Nếu vẫn lỗi, cấu hình DOUYIN_COOKIES_FILE."
        ) from last_cookie_error
