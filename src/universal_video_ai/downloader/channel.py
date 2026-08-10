from __future__ import annotations

import copy
import html
import json
import logging
import os
import re
import shutil
import threading
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from http.cookiejar import MozillaCookieJar
from pathlib import Path
from typing import Any, Iterator, Optional
from urllib.parse import parse_qs, unquote, urlparse, urlunparse

import requests

from .platform import Platform
from .platform_detector import PlatformDetector

logger = logging.getLogger(__name__)


def _cookiefile_for_platform(platform: Platform) -> str | None:
    candidate = (
        os.environ.get(f"{platform.name.upper()}_COOKIES_FILE")
        or os.environ.get("YTDLP_COOKIES_FILE")
    )
    if candidate and os.path.isfile(candidate):
        return candidate
    return None


def _bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


class DouyinAuthRequired(RuntimeError):
    """Raised when Douyin blocks profile enumeration behind login/verification."""


def _managed_douyin_profile_dir() -> str:
    configured = (os.environ.get("DOUYIN_CHANNEL_BROWSER_USER_DATA_DIR") or "").strip()
    if configured:
        path = Path(configured).expanduser()
    else:
        # Dedicated app-managed profile. Never attach to the user's normal Chrome profile.
        project_root = Path(__file__).resolve().parents[3]
        path = project_root / "local_data" / "browser_profiles" / "douyin_channel"
    path.mkdir(parents=True, exist_ok=True)
    return str(path)


def _douyin_auth_text(text: str, url: str = "") -> bool:
    body = str(text or "")
    lower = body.lower()
    url_lower = str(url or "").lower()
    tokens = ("验证码", "安全验证", "扫码登录", "登录后", "请登录", "访问过于频繁")
    return (
        any(token in body for token in tokens)
        or "captcha" in lower
        or "passport.douyin.com" in url_lower
        or "/verify" in url_lower
    )


class URLIntent(str, Enum):
    VIDEO = "video"
    CHANNEL = "channel"
    UNKNOWN = "unknown"


@dataclass(slots=True)
class URLClassification:
    original_url: str
    resolved_url: str
    platform: Platform
    intent: URLIntent
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["platform"] = self.platform.value
        data["intent"] = self.intent.value
        return data


@dataclass(slots=True)
class ChannelVideoCandidate:
    source_url: str
    platform: Platform
    video_id: str = ""
    title: str = ""
    uploader: str = ""
    duration: float = 0.0
    thumbnail_url: str = ""
    published_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["platform"] = self.platform.value
        return data


@dataclass(slots=True)
class ChannelScanResult:
    channel_url: str
    resolved_url: str
    platform: Platform
    channel_title: str = ""
    channel_id: str = ""
    videos: list[ChannelVideoCandidate] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    truncated: bool = False
    complete: bool = False
    has_more: Optional[bool] = None
    cursor: str = ""
    scan_source: str = ""
    stop_reason: str = ""
    network_pages: int = 0

    def to_dict(self, include_videos: bool = True) -> dict[str, Any]:
        payload = {
            "channel_url": self.channel_url,
            "resolved_url": self.resolved_url,
            "platform": self.platform.value,
            "channel_title": self.channel_title,
            "channel_id": self.channel_id,
            "video_count": len(self.videos),
            "warnings": list(self.warnings),
            "truncated": self.truncated,
            "complete": self.complete,
            "has_more": self.has_more,
            "cursor": self.cursor,
            "scan_source": self.scan_source,
            "stop_reason": self.stop_reason,
            "network_pages": self.network_pages,
        }
        if include_videos:
            payload["videos"] = [video.to_dict() for video in self.videos]
        return payload


class VideoURLClassifier:
    """Classify supported social URLs as a single video or a channel/profile."""

    _SHORT_HOSTS = {
        "v.douyin.com",
        "vm.tiktok.com",
        "vt.tiktok.com",
    }

    def __init__(self, timeout_seconds: int = 20):
        self.detector = PlatformDetector()
        self.timeout_seconds = timeout_seconds

    @staticmethod
    def _extract_url(text: str) -> str:
        raw = (text or "").strip()
        match = re.search(r"https?://[^\s<>'\"]+", raw)
        if not match:
            return raw
        return match.group(0).rstrip(".,;:!?)]}】》”’\"'")

    def resolve_short_url(self, url: str) -> str:
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").lower()
        if hostname not in self._SHORT_HOSTS:
            return url
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
            )
        }
        try:
            with requests.get(
                url,
                headers=headers,
                allow_redirects=True,
                timeout=self.timeout_seconds,
                stream=True,
            ) as response:
                return response.url or url
        except requests.RequestException as exc:
            logger.warning("Could not resolve short social URL %s: %s", url, exc)
            return url

    def classify(self, text: str, resolve_short: bool = True) -> URLClassification:
        original = self._extract_url(text)
        resolved = self.resolve_short_url(original) if resolve_short else original
        platform = self.detector.detect(resolved)
        parsed = urlparse(resolved)
        path = parsed.path.rstrip("/")
        query = parse_qs(parsed.query)
        original_host = (urlparse(original).hostname or "").lower()

        if resolved == original and original_host in self._SHORT_HOSTS:
            return URLClassification(original, resolved, platform, URLIntent.VIDEO, "unresolved_short_share")

        if platform == Platform.YOUTUBE:
            host = (parsed.hostname or "").lower()
            if host == "youtu.be" or "v" in query:
                return URLClassification(original, resolved, platform, URLIntent.VIDEO, "youtube_video")
            if re.search(r"/(?:shorts|live|embed)/[^/]+", path, re.I):
                return URLClassification(original, resolved, platform, URLIntent.VIDEO, "youtube_video")
            if re.search(r"/(?:channel|c|user)/[^/]+(?:/videos)?$", path, re.I) or re.search(
                r"/@[^/]+(?:/videos)?$", path, re.I
            ):
                return URLClassification(original, resolved, platform, URLIntent.CHANNEL, "youtube_channel")
            if "list" in query:
                return URLClassification(original, resolved, platform, URLIntent.CHANNEL, "youtube_collection")
            return URLClassification(original, resolved, platform, URLIntent.UNKNOWN, "unrecognized_youtube_url")

        if platform == Platform.TIKTOK:
            if re.search(r"/@[^/]+/video/\d+", path, re.I) or re.search(r"/video/\d+", path, re.I):
                return URLClassification(original, resolved, platform, URLIntent.VIDEO, "tiktok_video")
            if re.fullmatch(r"/@[^/]+", path, re.I):
                return URLClassification(original, resolved, platform, URLIntent.CHANNEL, "tiktok_profile")
            return URLClassification(original, resolved, platform, URLIntent.UNKNOWN, "unrecognized_tiktok_url")

        if platform == Platform.DOUYIN:
            if re.search(r"/(?:video|note|share/video|share/note)/\d+", path, re.I):
                return URLClassification(original, resolved, platform, URLIntent.VIDEO, "douyin_video")
            # A Douyin profile copied while a modal video is open often has
            # ?vid=... or ?modal_id=... appended. The path still identifies a
            # channel and channel mode must ignore those volatile query params.
            if re.search(r"/user/[^/]+", path, re.I):
                return URLClassification(original, resolved, platform, URLIntent.CHANNEL, "douyin_profile")
            return URLClassification(original, resolved, platform, URLIntent.UNKNOWN, "unrecognized_douyin_url")

        return URLClassification(original, resolved, platform, URLIntent.UNKNOWN, "legacy_platform")


class _QuietYTDLPLogger:
    """Capture yt-dlp diagnostics without writing expected fallback errors to stderr."""

    def __init__(self) -> None:
        self.errors: list[str] = []

    def debug(self, message: str) -> None:
        return None

    def warning(self, message: str) -> None:
        logger.debug("yt-dlp channel warning: %s", message)

    def error(self, message: str) -> None:
        self.errors.append(str(message))
        logger.debug("yt-dlp channel error: %s", message)


class ChannelListingService:
    """Enumerate public video URLs from YouTube/TikTok/Douyin channels.

    Discovery is separate from downloading. Every discovered URL is still sent
    through the project's existing DownloadService and normal localization job.

    Douyin profile URLs are handled through a fallback chain:
      1. yt-dlp using a canonical query-free profile URL;
      2. a real Chromium page via optional Playwright, scrolling and collecting
         public /video/{id} links;
      3. public bootstrap JSON embedded in the profile HTML (may be partial).
    """

    SUPPORTED_PLATFORMS = {Platform.YOUTUBE, Platform.TIKTOK, Platform.DOUYIN}

    def __init__(self, hard_limit: Optional[int] = None):
        self.classifier = VideoURLClassifier()
        self.hard_limit = max(1, hard_limit or int(os.environ.get("CHANNEL_SCAN_HARD_LIMIT", "10000")))
        self.cache_ttl_seconds = max(0, int(os.environ.get("CHANNEL_SCAN_CACHE_TTL_SECONDS", "600")))
        self._scan_cache: dict[tuple[str, int, bool], tuple[float, ChannelScanResult]] = {}
        self._scan_cache_lock = threading.Lock()

    @staticmethod
    def _iter_entries(entries: Any) -> Iterator[dict[str, Any]]:
        if entries is None:
            return
        for entry in entries:
            if not entry:
                continue
            if isinstance(entry, dict) and entry.get("entries"):
                yield from ChannelListingService._iter_entries(entry.get("entries"))
            elif isinstance(entry, dict):
                yield entry

    @staticmethod
    def _candidate_url(entry: dict[str, Any], platform: Platform) -> str:
        for key in ("webpage_url", "original_url", "url"):
            value = str(entry.get(key) or "").strip()
            if value.startswith("http://") or value.startswith("https://"):
                return value

        video_id = str(entry.get("id") or entry.get("aweme_id") or "").strip()
        if not video_id:
            return ""
        if platform == Platform.YOUTUBE:
            return f"https://www.youtube.com/watch?v={video_id}"
        if platform == Platform.DOUYIN:
            return f"https://www.douyin.com/video/{video_id}"
        if platform == Platform.TIKTOK:
            username = str(
                entry.get("uploader_id") or entry.get("channel_id") or entry.get("uploader") or ""
            ).strip().lstrip("@")
            if username:
                return f"https://www.tiktok.com/@{username}/video/{video_id}"
        return ""

    @staticmethod
    def _canonical_channel_url(classification: URLClassification) -> str:
        parsed = urlparse(classification.resolved_url)
        host = (parsed.hostname or "").lower()
        path = re.sub(r"/+", "/", parsed.path or "/").rstrip("/")

        if classification.platform == Platform.DOUYIN:
            match = re.search(r"/user/([^/?#]+)", path, re.I)
            if match:
                return f"https://www.douyin.com/user/{match.group(1)}"

        if classification.platform == Platform.TIKTOK:
            match = re.search(r"/@([^/?#]+)", path, re.I)
            if match:
                return f"https://www.tiktok.com/@{match.group(1)}"

        if classification.platform == Platform.YOUTUBE:
            query = parse_qs(parsed.query)
            if classification.reason == "youtube_collection" and query.get("list"):
                return f"https://www.youtube.com/playlist?list={query['list'][0]}"
            if re.search(r"/(?:channel|c|user)/[^/]+$", path, re.I) or re.search(r"/@[^/]+$", path, re.I):
                path = f"{path}/videos"
            scheme = parsed.scheme or "https"
            return urlunparse((scheme, host, path, "", "", ""))

        return urlunparse((parsed.scheme or "https", host, path, "", "", ""))

    @staticmethod
    def _load_netscape_cookies(cookiefile: str | None) -> MozillaCookieJar | None:
        if not cookiefile:
            return None
        jar = MozillaCookieJar(cookiefile)
        try:
            jar.load(ignore_discard=True, ignore_expires=True)
            return jar
        except Exception as exc:
            logger.warning("Could not read cookie file %s: %s", cookiefile, exc)
            return None

    @classmethod
    def _requests_session(cls, platform: Platform) -> requests.Session:
        session = requests.Session()
        jar = cls._load_netscape_cookies(_cookiefile_for_platform(platform))
        if jar is not None:
            session.cookies.update(jar)
        session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.6",
        })
        return session

    @staticmethod
    def _result_from_entries(
        *,
        classification: URLClassification,
        canonical_url: str,
        entries: Any,
        channel_title: str,
        channel_id: str,
        effective_limit: int,
    ) -> ChannelScanResult:
        result = ChannelScanResult(
            channel_url=classification.original_url,
            resolved_url=canonical_url,
            platform=classification.platform,
            channel_title=channel_title,
            channel_id=channel_id,
        )
        seen: set[str] = set()
        for entry in ChannelListingService._iter_entries(entries):
            source_url = ChannelListingService._candidate_url(entry, classification.platform)
            if not source_url or source_url in seen:
                continue
            seen.add(source_url)
            result.videos.append(
                ChannelVideoCandidate(
                    source_url=source_url,
                    platform=classification.platform,
                    video_id=str(entry.get("id") or entry.get("aweme_id") or ""),
                    title=str(entry.get("title") or entry.get("description") or entry.get("desc") or ""),
                    uploader=str(entry.get("uploader") or entry.get("channel") or ""),
                    duration=float(entry.get("duration") or 0.0),
                    thumbnail_url=str(entry.get("thumbnail") or ""),
                    published_at=str(entry.get("upload_date") or entry.get("timestamp") or ""),
                )
            )
            if len(result.videos) >= effective_limit:
                break
        return result

    def _scan_with_ytdlp(
        self,
        classification: URLClassification,
        canonical_url: str,
        effective_limit: int,
    ) -> tuple[ChannelScanResult | None, str]:
        quiet_logger = _QuietYTDLPLogger()
        options: dict[str, Any] = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "extract_flat": "in_playlist",
            "ignoreerrors": True,
            "lazy_playlist": False,
            "playlistend": effective_limit,
            "socket_timeout": 30,
            "retries": 3,
            "logger": quiet_logger,
        }
        cookiefile = _cookiefile_for_platform(classification.platform)
        if cookiefile:
            options["cookiefile"] = cookiefile

        try:
            import yt_dlp
            with yt_dlp.YoutubeDL(options) as ydl:
                info = ydl.extract_info(canonical_url, download=False)
        except Exception as exc:
            return None, str(exc)

        if not isinstance(info, dict) or not info.get("entries"):
            detail = "; ".join(quiet_logger.errors[-3:]) or "extractor returned no playlist entries"
            return None, detail

        result = self._result_from_entries(
            classification=classification,
            canonical_url=canonical_url,
            entries=info.get("entries"),
            channel_title=str(info.get("title") or info.get("channel") or info.get("uploader") or ""),
            channel_id=str(info.get("id") or info.get("channel_id") or info.get("uploader_id") or ""),
            effective_limit=effective_limit,
        )
        if not result.videos:
            return None, "extractor returned entries but none had valid video URLs"
        result.scan_source = "yt-dlp"
        # yt-dlp returns a finite playlist here. A result that did not hit the
        # requested safety limit is treated as complete; hitting the limit may
        # still mean the channel contains more entries.
        result.complete = len(result.videos) < effective_limit
        result.has_more = not result.complete
        result.truncated = not result.complete
        result.stop_reason = "playlist_exhausted" if result.complete else "limit_reached"
        return result, ""

    @staticmethod
    def _playwright_cookies(cookiefile: str | None) -> list[dict[str, Any]]:
        jar = ChannelListingService._load_netscape_cookies(cookiefile)
        if jar is None:
            return []
        output: list[dict[str, Any]] = []
        for cookie in jar:
            item: dict[str, Any] = {
                "name": cookie.name,
                "value": cookie.value,
                "domain": cookie.domain,
                "path": cookie.path or "/",
                "secure": bool(cookie.secure),
            }
            if cookie.expires:
                item["expires"] = float(cookie.expires)
            output.append(item)
        return output

    @staticmethod
    def _playwright_launch_candidates(headless: bool) -> list[tuple[str, dict[str, Any]]]:
        """Return browser launch options from most explicit to most portable.

        Playwright's Python package can be installed while its bundled browser
        is missing. In that common case, try an installed Chrome/Edge channel
        before failing the scan. No existing browser profile is used unless the
        operator explicitly configures a dedicated user-data directory.
        """
        candidates: list[tuple[str, dict[str, Any]]] = []
        executable = (os.environ.get("DOUYIN_CHANNEL_BROWSER_EXECUTABLE") or "").strip()
        configured_channel = (os.environ.get("DOUYIN_CHANNEL_BROWSER_CHANNEL") or "").strip()
        if executable:
            candidates.append((f"executable:{executable}", {"headless": headless, "executable_path": executable}))
        if configured_channel:
            candidates.append((f"channel:{configured_channel}", {"headless": headless, "channel": configured_channel}))

        executable_paths: list[str] = []
        for command in (
            "google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
            "microsoft-edge", "microsoft-edge-stable", "msedge",
        ):
            located = shutil.which(command)
            if located:
                executable_paths.append(located)
        for root in filter(None, (
            os.environ.get("LOCALAPPDATA"),
            os.environ.get("PROGRAMFILES"),
            os.environ.get("PROGRAMFILES(X86)"),
        )):
            base = Path(root)
            for relative in (
                Path("Google/Chrome/Application/chrome.exe"),
                Path("Microsoft/Edge/Application/msedge.exe"),
            ):
                candidate = base / relative
                if candidate.is_file():
                    executable_paths.append(str(candidate))
        for system_executable in executable_paths:
            candidates.append((
                f"system-executable:{system_executable}",
                {"headless": headless, "executable_path": system_executable},
            ))

        # Bundled Chromium first when it is actually installed, then system
        # browser channels. Playwright raises a clear error for unavailable
        # channels and the next candidate is attempted.
        candidates.extend([
            ("playwright-chromium", {"headless": headless}),
            ("system-chrome", {"headless": headless, "channel": "chrome"}),
            ("system-edge", {"headless": headless, "channel": "msedge"}),
        ])

        deduplicated: list[tuple[str, dict[str, Any]]] = []
        seen: set[tuple[tuple[str, str], ...]] = set()
        for label, options in candidates:
            key = tuple(sorted((str(k), str(v)) for k, v in options.items()))
            if key in seen:
                continue
            seen.add(key)
            deduplicated.append((label, options))
        return deduplicated

    @staticmethod
    def _expected_douyin_author_id(canonical_url: str) -> str:
        match = re.search(r"/user/([^/?#]+)", str(canonical_url or ""), re.I)
        return unquote(match.group(1)).strip() if match else ""

    @staticmethod
    def _douyin_record_owner_ids(record: dict[str, Any]) -> set[str]:
        values = {
            str(record.get("author_sec_uid") or "").strip(),
            str(record.get("author_uid") or "").strip(),
            str(record.get("author_unique_id") or "").strip(),
        }
        return {value for value in values if value}

    @classmethod
    def _douyin_record_matches_owner(cls, record: dict[str, Any], expected_author_id: str) -> bool:
        expected = str(expected_author_id or "").strip()
        if not expected:
            return True
        owner_ids = cls._douyin_record_owner_ids(record)
        return expected in owner_ids

    @staticmethod
    def _douyin_post_response_matches_profile(response_url: str, expected_author_id: str) -> bool:
        url = unquote(str(response_url or ""))
        lower = url.lower()
        if not any(marker in lower for marker in ("aweme/post", "aweme/v1/web/aweme/post", "/post/")):
            return False
        expected = str(expected_author_id or "").strip()
        if not expected:
            return True
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        candidates: set[str] = set()
        for key in ("sec_user_id", "sec_uid", "user_id", "uid"):
            for value in query.get(key, []):
                candidates.add(str(value).strip())
        # When Douyin changes the query key but still embeds the sec_uid in the
        # URL, accept it. Otherwise reject pagination from unrelated feeds.
        return expected in candidates or expected in url

    @staticmethod
    def _douyin_record_from_item(item: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
        video_id = str(
            item.get("aweme_id")
            or item.get("awemeId")
            or item.get("item_id")
            or item.get("itemId")
            or item.get("video_id")
            or ""
        ).strip()
        if not re.fullmatch(r"\d{10,}", video_id):
            return None

        author = item.get("author") if isinstance(item.get("author"), dict) else {}
        video = item.get("video") if isinstance(item.get("video"), dict) else {}
        cover = (
            item.get("cover")
            or video.get("cover")
            or video.get("origin_cover")
            or video.get("dynamic_cover")
            or {}
        )
        thumbnail = ""
        if isinstance(cover, dict):
            urls = cover.get("url_list") or cover.get("urlList") or []
            if isinstance(urls, list) and urls:
                thumbnail = str(urls[0] or "")
        elif isinstance(cover, str):
            thumbnail = cover

        duration = item.get("duration") or video.get("duration") or 0
        try:
            duration_value = float(duration or 0)
            if duration_value > 1000:
                duration_value /= 1000.0
        except (TypeError, ValueError):
            duration_value = 0.0

        return video_id, {
            "id": video_id,
            "aweme_id": video_id,
            "desc": str(item.get("desc") or item.get("title") or ""),
            "uploader": str(author.get("nickname") or author.get("unique_id") or ""),
            "author_sec_uid": str(author.get("sec_uid") or author.get("secUid") or ""),
            "author_uid": str(author.get("uid") or author.get("user_id") or author.get("userId") or ""),
            "author_unique_id": str(author.get("unique_id") or author.get("uniqueId") or ""),
            "duration": duration_value,
            "thumbnail": thumbnail,
            "timestamp": str(item.get("create_time") or item.get("createTime") or ""),
            "owner_verified": False,
        }

    @classmethod
    def _merge_douyin_payload(
        cls,
        payload: Any,
        records: dict[str, dict[str, Any]],
        *,
        expected_author_id: str = "",
        require_owner: bool = False,
    ) -> None:
        for item in cls._walk_json(payload):
            parsed = cls._douyin_record_from_item(item)
            if parsed is None:
                continue
            video_id, record = parsed
            owner_ids = cls._douyin_record_owner_ids(record)
            if expected_author_id:
                if owner_ids and not cls._douyin_record_matches_owner(record, expected_author_id):
                    # This is the core contamination guard: profile pages also
                    # load recommendations/related awemes from other creators.
                    continue
                if require_owner and not owner_ids:
                    continue
                record["owner_verified"] = bool(owner_ids and cls._douyin_record_matches_owner(record, expected_author_id))
            if video_id not in records:
                records[video_id] = record
            else:
                existing = records[video_id]
                for key, value in record.items():
                    if value not in (None, "", 0, 0.0, False) and existing.get(key) in (None, "", 0, 0.0, False):
                        existing[key] = value
                if record.get("owner_verified"):
                    existing["owner_verified"] = True

    @classmethod
    def _extract_douyin_records_from_html(
        cls, page: str, *, expected_author_id: str = "", require_owner: bool = False,
    ) -> dict[str, dict[str, Any]]:
        decoded_page = html.unescape(page or "")
        records: dict[str, dict[str, Any]] = {}
        script_patterns = (
            r'<script[^>]+id=["\']RENDER_DATA["\'][^>]*>(.*?)</script>',
            r'<script[^>]+id=["\']__UNIVERSAL_DATA_FOR_REHYDRATION__["\'][^>]*>(.*?)</script>',
            r'<script[^>]+type=["\']application/json["\'][^>]*>(.*?)</script>',
            r'window\._ROUTER_DATA\s*=\s*({.*?})\s*</script>',
        )
        for pattern in script_patterns:
            for match in re.finditer(pattern, decoded_page, re.S | re.I):
                raw = unquote(match.group(1)).strip()
                if not raw:
                    continue
                try:
                    cls._merge_douyin_payload(
                        json.loads(raw), records,
                        expected_author_id=expected_author_id,
                        require_owner=require_owner,
                    )
                except Exception:
                    continue

        # Douyin changes bootstrap nesting frequently. Keep conservative ID
        # fallbacks so the per-video downloader can retrieve metadata later.
        if not require_owner:
            for match in re.finditer(
                r'(?:aweme_id|awemeId|item_id|itemId|video_id)["\']?\s*[:=]\s*["\'](\d{10,})',
                decoded_page,
            ):
                video_id = match.group(1)
                records.setdefault(video_id, {"id": video_id, "aweme_id": video_id})
            for match in re.finditer(r"/video/(\d{10,})", decoded_page):
                video_id = match.group(1)
                records.setdefault(video_id, {"id": video_id, "aweme_id": video_id})
        return records

    @staticmethod
    def _coerce_has_more(value: Any) -> Optional[bool]:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"1", "true", "yes"}:
                return True
            if normalized in {"0", "false", "no"}:
                return False
        return None

    @classmethod
    def _extract_douyin_pagination(cls, payload: Any) -> list[dict[str, Any]]:
        """Extract pagination hints from Douyin network/bootstrap payloads.

        The public web response shape changes frequently. Rather than depend on
        one exact nesting path, inspect every object that contains a plausible
        aweme/post list and retain its has_more/cursor fields.
        """
        pages: list[dict[str, Any]] = []
        list_keys = (
            "aweme_list", "awemeList", "post_list", "postList", "item_list",
            "itemList", "items", "data_list", "dataList",
        )
        cursor_keys = (
            "max_cursor", "maxCursor", "cursor", "next_cursor", "nextCursor",
            "min_cursor", "minCursor",
        )
        has_more_keys = ("has_more", "hasMore", "hasmore", "more")
        for node in cls._walk_json(payload):
            list_value = None
            for key in list_keys:
                candidate = node.get(key)
                if isinstance(candidate, list):
                    list_value = candidate
                    break
            if list_value is None:
                continue
            # Ignore unrelated generic `items` arrays unless at least one item
            # looks like a Douyin aweme record.
            if list_value and not any(
                isinstance(item, dict) and cls._douyin_record_from_item(item) is not None
                for item in list_value[:5]
            ):
                continue
            has_more: Optional[bool] = None
            for key in has_more_keys:
                if key in node:
                    has_more = cls._coerce_has_more(node.get(key))
                    if has_more is not None:
                        break
            cursor = ""
            for key in cursor_keys:
                value = node.get(key)
                if value not in (None, ""):
                    cursor = str(value)
                    break
            pages.append({
                "has_more": has_more,
                "cursor": cursor,
                "count": len(list_value),
            })
        return pages

    @staticmethod
    def _scroll_douyin_page(page: Any) -> dict[str, Any]:
        """Scroll the document, the largest nested scroller and last video card."""
        script = r"""
        () => {
          const metrics = {moved: false, maxHeight: 0, scrollers: 0, videoLinks: 0};
          const root = document.scrollingElement || document.documentElement || document.body;
          const candidates = [root];
          for (const el of document.querySelectorAll('main, section, div, ul')) {
            const style = window.getComputedStyle(el);
            const overflowY = style.overflowY || '';
            if (el.scrollHeight > el.clientHeight + 160 &&
                (overflowY === 'auto' || overflowY === 'scroll' || el === root)) {
              candidates.push(el);
            }
          }
          const unique = [...new Set(candidates)]
            .sort((a, b) => (b.scrollHeight - b.clientHeight) - (a.scrollHeight - a.clientHeight))
            .slice(0, 6);
          for (const el of unique) {
            const before = el.scrollTop;
            const step = Math.max(700, Math.floor((el.clientHeight || window.innerHeight) * 0.92));
            el.scrollTop = Math.min(el.scrollHeight, before + step);
            try { el.dispatchEvent(new Event('scroll', {bubbles: true})); } catch (_) {}
            metrics.moved = metrics.moved || el.scrollTop > before;
            metrics.maxHeight = Math.max(metrics.maxHeight, el.scrollHeight || 0);
            metrics.scrollers += 1;
          }
          const links = [...document.querySelectorAll('a[href*="/video/"]')];
          metrics.videoLinks = links.length;
          const last = links[links.length - 1];
          if (last) {
            try { last.scrollIntoView({block: 'end', inline: 'nearest'}); } catch (_) {}
          }
          window.scrollBy(0, Math.max(700, Math.floor(window.innerHeight * 0.9)));
          window.dispatchEvent(new Event('scroll'));
          return metrics;
        }
        """
        try:
            value = page.evaluate(script)
            return value if isinstance(value, dict) else {}
        except Exception:
            return {}

    def _open_playwright_context(
        self,
        playwright: Any,
        *,
        headless: bool,
        user_data_dir_override: Optional[str] = None,
    ) -> tuple[Any, Any, str]:
        viewport = {"width": 1440, "height": 1000}
        user_agent = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        )
        user_data_dir = (user_data_dir_override or _managed_douyin_profile_dir()).strip()
        launch_errors: list[str] = []
        for label, launch_options in self._playwright_launch_candidates(headless):
            browser = None
            try:
                if user_data_dir:
                    # Always use a dedicated app-managed profile so a one-time
                    # Douyin login/captcha survives server restarts. This is never
                    # the user's normal Chrome profile.
                    context = playwright.chromium.launch_persistent_context(
                        user_data_dir=user_data_dir,
                        viewport=viewport,
                        user_agent=user_agent,
                        locale="zh-CN",
                        **launch_options,
                    )
                    return context, None, label
                browser = playwright.chromium.launch(**launch_options)
                context = browser.new_context(
                    viewport=viewport,
                    user_agent=user_agent,
                    locale="zh-CN",
                )
                return context, browser, label
            except Exception as exc:
                if browser is not None:
                    try:
                        browser.close()
                    except Exception:
                        pass
                launch_errors.append(f"{label}: {str(exc).splitlines()[0]}")

        raise RuntimeError(
            "Không khởi động được browser cho Douyin. "
            + " | ".join(launch_errors[-4:])
            + ". Cài browser bằng `python -m playwright install chromium`, hoặc cấu hình "
              "DOUYIN_CHANNEL_BROWSER_CHANNEL=chrome/msedge."
        )

    def _scan_douyin_with_playwright(
        self,
        classification: URLClassification,
        canonical_url: str,
        effective_limit: int,
        *,
        known_video_ids: Optional[set[str]] = None,
        deep_scan: bool = False,
        headless_override: Optional[bool] = None,
        allow_auth_wait: bool = False,
    ) -> ChannelScanResult:
        if not _bool_env("DOUYIN_CHANNEL_BROWSER_ENABLED", True):
            raise RuntimeError("Douyin browser fallback is disabled")
        try:
            from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError(
                "Playwright chưa được cài. Chạy: pip install playwright && python -m playwright install chromium"
            ) from exc

        timeout_ms = max(10_000, int(float(os.environ.get("DOUYIN_CHANNEL_BROWSER_TIMEOUT_SECONDS", "90")) * 1000))
        wait_ms = max(300, int(os.environ.get("DOUYIN_CHANNEL_SCROLL_WAIT_MS", "1200")))
        configured_idle = max(2, int(os.environ.get("DOUYIN_CHANNEL_MAX_IDLE_ROUNDS", "8")))
        idle_limit = max(configured_idle, 24 if deep_scan else configured_idle)
        configured_rounds = max(10, int(os.environ.get("DOUYIN_CHANNEL_MAX_SCROLL_ROUNDS", "500")))
        max_scroll_rounds = max(configured_rounds, 1600 if deep_scan else configured_rounds)
        deep_timeout_seconds = max(60, int(os.environ.get("DOUYIN_CHANNEL_DEEP_SCAN_TIMEOUT_SECONDS", "600")))
        total_timeout_seconds = deep_timeout_seconds if deep_scan else max(60, timeout_ms // 1000 + 60)
        deadline = time.monotonic() + total_timeout_seconds
        headless = _bool_env("DOUYIN_CHANNEL_BROWSER_HEADLESS", True) if headless_override is None else bool(headless_override)
        auth_wait_seconds = max(30, int(os.environ.get("DOUYIN_CHANNEL_LOGIN_WAIT_SECONDS", "180")))
        known_video_ids = {str(value) for value in (known_video_ids or set()) if str(value)}
        expected_author_id = self._expected_douyin_author_id(canonical_url)

        result = ChannelScanResult(
            channel_url=classification.original_url,
            resolved_url=canonical_url,
            platform=Platform.DOUYIN,
            channel_id=self._expected_douyin_author_id(canonical_url) or canonical_url.rsplit("/", 1)[-1],
        )
        records: dict[str, dict[str, Any]] = {}
        body_text = ""
        launch_label = ""
        pagination: dict[str, Any] = {
            "has_more": None,
            "cursor": "",
            "pages": 0,
            "saw_pagination": False,
        }
        stop_reason = ""

        try:
            with sync_playwright() as playwright:
                context, browser, launch_label = self._open_playwright_context(playwright, headless=headless)
                try:
                    cookies = self._playwright_cookies(_cookiefile_for_platform(Platform.DOUYIN))
                    if cookies:
                        context.add_cookies(cookies)
                    page = context.pages[0] if context.pages else context.new_page()

                    def capture_response(response: Any) -> None:
                        response_url = str(getattr(response, "url", "") or "").lower()
                        if not any(marker in response_url for marker in (
                            "/aweme/", "aweme/post", "aweme/favorite", "user/profile/other",
                            "/post/", "aweme/v1/web",
                        )):
                            return
                        try:
                            content_type = str(response.headers.get("content-type", "")).lower()
                            if "json" not in content_type:
                                return
                            payload = response.json()
                            self._merge_douyin_payload(
                                payload,
                                records,
                                expected_author_id=expected_author_id,
                                require_owner=bool(expected_author_id),
                            )
                            if self._douyin_post_response_matches_profile(response_url, expected_author_id):
                                for page_meta in self._extract_douyin_pagination(payload):
                                    pagination["saw_pagination"] = True
                                    pagination["pages"] += 1
                                    if page_meta.get("has_more") is not None:
                                        pagination["has_more"] = page_meta["has_more"]
                                    if page_meta.get("cursor"):
                                        pagination["cursor"] = str(page_meta["cursor"])
                        except Exception:
                            # Some responses are compressed/streamed or blocked;
                            # DOM and bootstrap extraction remain available.
                            return

                    page.on("response", capture_response)
                    page.goto(canonical_url, wait_until="domcontentloaded", timeout=timeout_ms)
                    page.wait_for_timeout(min(6000, wait_ms * 3))
                    result.channel_title = page.title() or ""

                    def page_requires_auth() -> bool:
                        try:
                            body = page.locator("body").inner_text(timeout=3000)
                        except Exception:
                            body = ""
                        return _douyin_auth_text(body, getattr(page, "url", ""))

                    if page_requires_auth():
                        if headless or not allow_auth_wait:
                            raise DouyinAuthRequired(
                                "Douyin yêu cầu đăng nhập/xác minh cho profile này."
                            )
                        logger.warning(
                            "Douyin yêu cầu đăng nhập/xác minh. Đã mở Chromium có profile riêng; "
                            "hãy đăng nhập/quét QR/xác minh trong cửa sổ browser. Tool sẽ tự tiếp tục sau khi hoàn tất."
                        )
                        auth_deadline = time.monotonic() + auth_wait_seconds
                        cleared = False
                        while time.monotonic() < auth_deadline:
                            page.wait_for_timeout(1500)
                            if not page_requires_auth():
                                cleared = True
                                try:
                                    page.goto(canonical_url, wait_until="domcontentloaded", timeout=timeout_ms)
                                    page.wait_for_timeout(min(5000, wait_ms * 3))
                                except Exception:
                                    pass
                                break
                        if not cleared:
                            raise DouyinAuthRequired(
                                f"Hết {auth_wait_seconds}s chờ đăng nhập/xác minh Douyin. "
                                "Hãy hoàn tất xác minh trong cửa sổ Chromium rồi thử lại."
                            )
                        result.warnings.append(
                            "Đã dùng phiên Douyin đã xác minh trong profile browser riêng; session được lưu cho lần quét sau."
                        )

                    # Some profile links open on a non-post tab. Best-effort
                    # activate the public works/posts tab before scrolling.
                    for tab_text in ("作品", "视频"):
                        try:
                            locator = page.get_by_text(tab_text, exact=True).first
                            if locator.is_visible(timeout=800):
                                locator.click(timeout=1500)
                                page.wait_for_timeout(min(2200, wait_ms * 2))
                                break
                        except Exception:
                            continue

                    # Read common hydration globals after JS initialization.
                    for expression in (
                        "window.__UNIVERSAL_DATA_FOR_REHYDRATION__ || null",
                        "window._ROUTER_DATA || null",
                        "window.__INITIAL_STATE__ || null",
                    ):
                        try:
                            hydration_payload = page.evaluate(f"() => ({expression})")
                            self._merge_douyin_payload(
                                hydration_payload,
                                records,
                                expected_author_id=expected_author_id,
                                require_owner=bool(expected_author_id),
                            )
                        except Exception:
                            pass

                    idle_rounds = 0
                    previous_count = len(records)
                    previous_pages = int(pagination["pages"])
                    previous_height = 0
                    terminal_rounds = 0
                    for round_index in range(max_scroll_rounds):
                        if time.monotonic() >= deadline:
                            stop_reason = "timeout"
                            break
                        try:
                            hrefs = page.eval_on_selector_all(
                                'a[href]',
                                "elements => elements.map(element => element.href || element.getAttribute('href') || '')",
                            )
                        except Exception:
                            hrefs = []
                        # Do not inject every /video/ link from the page into
                        # the catalog. Douyin profile pages also contain related
                        # and recommendation cards from other creators. Only
                        # owner-verified awemes captured from profile payloads
                        # are allowed into `records`.
                        _ = hrefs

                        # Periodically parse live HTML for virtualized cards and
                        # bootstrap fragments that do not expose anchor tags.
                        if round_index == 0 or round_index % 4 == 0:
                            try:
                                html_records = self._extract_douyin_records_from_html(
                                    page.content(),
                                    expected_author_id=expected_author_id,
                                    require_owner=bool(expected_author_id),
                                )
                                for video_id, item in html_records.items():
                                    records.setdefault(video_id, item)
                            except Exception:
                                pass

                        if len(records) >= effective_limit:
                            stop_reason = "limit_reached"
                            break

                        if pagination["saw_pagination"] and pagination["has_more"] is False:
                            terminal_rounds += 1
                            if terminal_rounds >= 2:
                                stop_reason = "terminal_cursor"
                                break
                        else:
                            terminal_rounds = 0

                        metrics = self._scroll_douyin_page(page)
                        try:
                            page.mouse.wheel(0, max(1000, int(page.viewport_size["height"] * 0.95) if page.viewport_size else 1000))
                        except Exception:
                            pass
                        page.wait_for_timeout(wait_ms)

                        current_height = int(metrics.get("maxHeight") or 0)
                        current_pages = int(pagination["pages"])
                        made_progress = (
                            len(records) > previous_count
                            or current_pages > previous_pages
                            or current_height > previous_height
                        )
                        if made_progress:
                            idle_rounds = 0
                        else:
                            idle_rounds += 1
                        previous_count = len(records)
                        previous_pages = current_pages
                        previous_height = max(previous_height, current_height)

                        if idle_rounds >= idle_limit:
                            stop_reason = "idle_exhausted"
                            break
                    else:
                        stop_reason = "scroll_round_limit"

                    try:
                        body_text = page.locator("body").inner_text(timeout=5000)
                    except Exception:
                        body_text = ""
                    try:
                        html_records = self._extract_douyin_records_from_html(
                            page.content(),
                            expected_author_id=expected_author_id,
                            require_owner=bool(expected_author_id),
                        )
                        for video_id, item in html_records.items():
                            records.setdefault(video_id, item)
                    except Exception:
                        pass
                finally:
                    try:
                        context.close()
                    finally:
                        if browser is not None:
                            browser.close()
        except DouyinAuthRequired:
            raise
        except PlaywrightTimeoutError as exc:
            raise RuntimeError(f"Douyin profile browser timed out: {exc}") from exc
        except Exception as exc:
            raise RuntimeError(f"Douyin browser scan failed: {exc}") from exc

        if not records:
            lower_text = body_text.lower()
            if _douyin_auth_text(body_text):
                raise DouyinAuthRequired("Douyin yêu cầu đăng nhập/xác minh cho profile này.")
            raise RuntimeError(
                "Browser đã mở profile nhưng không thu được video từ DOM hoặc network responses. "
                "Thử DOUYIN_CHANNEL_BROWSER_HEADLESS=false, cấu hình cookie/profile riêng, và cập nhật Playwright."
            )

        result.scan_source = "playwright"
        result.has_more = pagination["has_more"]
        result.cursor = str(pagination["cursor"] or "")
        result.network_pages = int(pagination["pages"] or 0)
        result.stop_reason = stop_reason or "browser_finished"
        result.complete = bool(pagination["saw_pagination"] and pagination["has_more"] is False)
        result.truncated = not result.complete
        result.warnings.append(f"Đã quét profile bằng browser fallback ({launch_label}).")
        if expected_author_id:
            result.warnings.append(
                f"Ownership Guard đã bật: chỉ nhận video có author.sec_uid khớp profile {expected_author_id[:12]}…"
            )
        new_records = len(set(records) - known_video_ids)
        if not result.complete:
            if pagination["has_more"] is True:
                result.warnings.append(
                    f"Douyin vẫn báo còn video. Đã thu được {len(records)} video trong lượt này "
                    f"({new_records} video chưa có trong catalog); hãy dùng Quét tiếp/Quét sâu."
                )
            elif not pagination["saw_pagination"]:
                result.warnings.append(
                    "Chưa quan sát được tín hiệu phân trang kết thúc từ Douyin; kết quả được đánh dấu chưa hoàn tất."
                )
            else:
                result.warnings.append(
                    "Lượt quét dừng trước khi Douyin xác nhận hết video; kết quả được đánh dấu chưa hoàn tất."
                )
        for video_id, item in list(records.items())[:effective_limit]:
            result.videos.append(
                ChannelVideoCandidate(
                    source_url=f"https://www.douyin.com/video/{video_id}",
                    platform=Platform.DOUYIN,
                    video_id=video_id,
                    title=str(item.get("desc") or item.get("title") or ""),
                    uploader=str(item.get("uploader") or ""),
                    duration=float(item.get("duration") or 0.0),
                    thumbnail_url=str(item.get("thumbnail") or ""),
                    published_at=str(item.get("timestamp") or ""),
                )
            )
        return result

    @staticmethod
    def _walk_json(value: Any) -> Iterator[dict[str, Any]]:
        if isinstance(value, dict):
            yield value
            for child in value.values():
                yield from ChannelListingService._walk_json(child)
        elif isinstance(value, list):
            for child in value:
                yield from ChannelListingService._walk_json(child)

    def _scan_douyin_bootstrap(
        self,
        classification: URLClassification,
        canonical_url: str,
        effective_limit: int,
    ) -> ChannelScanResult:
        session = self._requests_session(Platform.DOUYIN)
        try:
            response = session.get(canonical_url, timeout=45, allow_redirects=True)
            response.raise_for_status()
        except requests.RequestException as exc:
            raise RuntimeError(f"Douyin profile HTML request failed: {exc}") from exc

        expected_author_id = self._expected_douyin_author_id(canonical_url)
        records = self._extract_douyin_records_from_html(
            response.text,
            expected_author_id=expected_author_id,
            require_owner=bool(expected_author_id),
        )

        if not records:
            raise RuntimeError(
                "Profile HTML không có video nào xác minh đúng chủ kênh; đã bỏ qua các aweme/recommendation không thuộc profile."
            )

        result = ChannelScanResult(
            channel_url=classification.original_url,
            resolved_url=canonical_url,
            platform=Platform.DOUYIN,
            channel_id=self._expected_douyin_author_id(canonical_url) or canonical_url.rsplit("/", 1)[-1],
            warnings=[
                "Đã dùng fallback HTML của Douyin. Kết quả có thể chỉ gồm các video được preload; "
                "cài Playwright để cuộn toàn bộ profile."
            ],
            truncated=True,
            complete=False,
            has_more=None,
            scan_source="html_bootstrap",
            stop_reason="bootstrap_partial",
        )
        for video_id, item in list(records.items())[:effective_limit]:
            result.videos.append(
                ChannelVideoCandidate(
                    source_url=f"https://www.douyin.com/video/{video_id}",
                    platform=Platform.DOUYIN,
                    video_id=video_id,
                    title=str(item.get("desc") or item.get("title") or ""),
                    uploader=str(item.get("uploader") or ""),
                    duration=float(item.get("duration") or 0.0),
                    thumbnail_url=str(item.get("thumbnail") or ""),
                    published_at=str(item.get("timestamp") or ""),
                )
            )
        return result

    def canonicalize(self, channel_url: str) -> tuple[URLClassification, str]:
        classification = self.classifier.classify(channel_url)
        if classification.platform not in self.SUPPORTED_PLATFORMS:
            raise ValueError("Chỉ hỗ trợ tải toàn bộ kênh YouTube, TikTok hoặc Douyin")
        if classification.intent != URLIntent.CHANNEL:
            raise ValueError("Link này không phải link kênh/profile. Hãy bật chế độ kênh và dán đúng link kênh")
        return classification, self._canonical_channel_url(classification)

    def scan(
        self,
        channel_url: str,
        max_videos: int = 0,
        *,
        force_refresh: bool = False,
        known_video_ids: Optional[set[str]] = None,
        deep: bool = False,
    ) -> ChannelScanResult:
        classification, canonical_url = self.canonicalize(channel_url)

        requested_limit = max(0, int(max_videos or 0))
        effective_limit = min(requested_limit or self.hard_limit, self.hard_limit)
        logger.info("Scanning %s channel with canonical URL: %s", classification.platform.value, canonical_url)

        cache_key = (canonical_url, effective_limit, bool(deep))
        if self.cache_ttl_seconds > 0 and not force_refresh:
            with self._scan_cache_lock:
                cached = self._scan_cache.get(cache_key)
                if cached and time.monotonic() - cached[0] <= self.cache_ttl_seconds:
                    logger.info("Using cached channel scan for %s", canonical_url)
                    return copy.deepcopy(cached[1])
                if cached:
                    self._scan_cache.pop(cache_key, None)

        if (
            classification.platform == Platform.DOUYIN
            and not _bool_env("DOUYIN_CHANNEL_TRUST_YTDLP_PROFILE", False)
        ):
            # yt-dlp profile enumeration cannot reliably prove every aweme's
            # author against the sec_uid in the requested profile URL. Use the
            # browser/HTML paths below where author.sec_uid can be validated.
            ytdlp_result, ytdlp_error = None, "skipped: ownership guard requires author.sec_uid verification"
        else:
            ytdlp_result, ytdlp_error = self._scan_with_ytdlp(
                classification,
                canonical_url,
                effective_limit,
            )
        if ytdlp_result is not None:
            result = ytdlp_result
        elif classification.platform == Platform.DOUYIN:
            fallback_errors: list[str] = []
            try:
                result = self._scan_douyin_with_playwright(
                    classification,
                    canonical_url,
                    effective_limit,
                    known_video_ids=known_video_ids,
                    deep_scan=deep,
                )
                result.warnings.append("yt-dlp không enumerate được profile Douyin; đã dùng Chromium fallback.")
            except DouyinAuthRequired as auth_exc:
                fallback_errors.append(str(auth_exc))
                if _bool_env("DOUYIN_CHANNEL_AUTO_LOGIN_RECOVERY", True):
                    logger.warning(
                        "Douyin auth required for %s. Retrying in visible Chromium with managed persistent profile %s",
                        canonical_url, _managed_douyin_profile_dir(),
                    )
                    try:
                        result = self._scan_douyin_with_playwright(
                            classification,
                            canonical_url,
                            effective_limit,
                            known_video_ids=known_video_ids,
                            deep_scan=deep,
                            headless_override=False,
                            allow_auth_wait=True,
                        )
                        result.warnings.insert(
                            0,
                            "Douyin yêu cầu xác minh nên tool đã tự mở Chromium. Phiên đăng nhập được lưu trong profile riêng để tái sử dụng.",
                        )
                    except RuntimeError as visible_exc:
                        fallback_errors.append(str(visible_exc))
                        result = None
                else:
                    result = None
                if result is None:
                    try:
                        result = self._scan_douyin_bootstrap(
                            classification,
                            canonical_url,
                            effective_limit,
                        )
                        result.warnings.insert(0, "Chromium fallback không khả dụng; đã dùng HTML bootstrap fallback.")
                    except RuntimeError as html_exc:
                        fallback_errors.append(str(html_exc))
                        raise RuntimeError(
                            "Douyin đang yêu cầu đăng nhập/xác minh. Tool đã thử tự phục hồi nhưng chưa hoàn tất. "
                            f"Mở Chromium vừa được tool bật và hoàn tất QR/CAPTCHA trong tối đa "
                            f"{os.environ.get('DOUYIN_CHANNEL_LOGIN_WAIT_SECONDS', '180')} giây, sau đó chạy lại. "
                            f"Profile đăng nhập được lưu tại {_managed_douyin_profile_dir()}."
                        ) from html_exc
            except RuntimeError as exc:
                fallback_errors.append(str(exc))
                try:
                    result = self._scan_douyin_bootstrap(
                        classification,
                        canonical_url,
                        effective_limit,
                    )
                    result.warnings.insert(0, "Chromium fallback không khả dụng; đã dùng HTML bootstrap fallback.")
                except RuntimeError as html_exc:
                    fallback_errors.append(str(html_exc))
                    cookie_hint = (
                        " Hãy cấu hình DOUYIN_COOKIES_FILE nếu profile yêu cầu đăng nhập/xác minh."
                        if not _cookiefile_for_platform(Platform.DOUYIN)
                        else ""
                    )
                    raise RuntimeError(
                        "Không quét được profile Douyin sau các phương án Chromium và HTML. "
                        f"fallback: {' | '.join(fallback_errors)}.{cookie_hint}"
                    ) from html_exc
        else:
            platform_name = classification.platform.value
            cookie_hint = (
                f" Có thể cần {platform_name.upper()}_COOKIES_FILE/YTDLP_COOKIES_FILE "
                "nếu nền tảng yêu cầu phiên đăng nhập."
                if classification.platform == Platform.TIKTOK
                else ""
            )
            raise RuntimeError(
                f"Không quét được kênh {platform_name}: {ytdlp_error or 'extractor returned no entries'}."
                f"{cookie_hint}"
            )

        if not result.videos:
            raise RuntimeError("Quét kênh thành công nhưng không tìm thấy URL video hợp lệ")

        # Deduplicate again across provider fallbacks and preserve order.
        unique: list[ChannelVideoCandidate] = []
        seen_urls: set[str] = set()
        for video in result.videos:
            if video.source_url in seen_urls:
                continue
            seen_urls.add(video.source_url)
            unique.append(video)
            if len(unique) >= effective_limit:
                break
        result.videos = unique
        result.resolved_url = canonical_url

        if requested_limit == 0 and len(result.videos) >= self.hard_limit:
            result.truncated = True
            result.warnings.append(
                f"Kênh có thể còn video khác; đã dừng ở giới hạn an toàn {self.hard_limit}. "
                "Tăng CHANNEL_SCAN_HARD_LIMIT nếu máy chủ đủ tài nguyên."
            )
        if self.cache_ttl_seconds > 0 and (result.complete or not deep):
            with self._scan_cache_lock:
                self._scan_cache[cache_key] = (time.monotonic(), copy.deepcopy(result))
        return result