"""Metadata-only source discovery through existing platform capabilities."""
from __future__ import annotations

from dataclasses import dataclass, asdict, replace
from datetime import datetime, timezone
from collections import deque
from contextlib import contextmanager
from contextvars import ContextVar, copy_context
from concurrent.futures import ThreadPoolExecutor
import html
import logging
import json
import os
import math
import re
from pathlib import Path
import threading
import time
from urllib.parse import urlparse, parse_qs, quote, unquote, urljoin

from universal_video_ai.downloader.channel import ChannelListingService
from universal_video_ai.reup.ranking import rank_source, source_rank_key
from universal_video_ai.reup.keywords import content_relevance
from universal_video_ai.reup.source_sessions import SourceSessions

__all__ = ["Candidate", "source_identity", "SourceDiscovery", "discovery_issue", "DiscoveryUnavailable"]
_logger = logging.getLogger(__name__)
_HOSTS = {"youtube.com": "youtube", "www.youtube.com": "youtube", "m.youtube.com": "youtube",
          "youtu.be": "youtube", "www.bilibili.com": "bilibili", "bilibili.com": "bilibili",
          "space.bilibili.com": "bilibili", "www.tiktok.com": "tiktok", "tiktok.com": "tiktok", "m.tiktok.com": "tiktok", "www.douyin.com": "douyin", "douyin.com": "douyin"}


def duration_seconds(platform: str, node: dict) -> float:
    """Normalize platform metadata units; zero denotes an unknown duration."""
    video = node.get("video") if isinstance(node.get("video"), dict) else {}
    raw = video.get("duration") if platform in {"douyin", "tiktok"} else node.get("duration")
    if raw is None:
        raw = node.get("duration")
    if isinstance(raw, str) and ":" in raw:
        try:
            parts = [float(value) for value in raw.split(":")]
            return sum(value * 60 ** index for index, value in enumerate(reversed(parts)))
        except ValueError:
            return 0
    try:
        value = float(raw)
        if isinstance(raw, bool) or not math.isfinite(value) or value <= 0:
            return 0
        return value / 1000 if platform == "douyin" else value
    except (TypeError, ValueError, OverflowError):
        return 0


def discovery_issue(error: Exception) -> dict:
    if isinstance(error, DiscoveryUnavailable):
        return dict(error.issue)
    detail = str(error).casefold()
    if "source browser busy" in detail:
        return {"status": "session_open", "message": "Cửa sổ nguồn đang mở và tự lưu phiên. Đóng cửa sổ đó rồi kiểm tra tìm nguồn; không cần đăng nhập lại."}
    if any(code in detail for code in ("http 502", "http 503", "http 504")):
        return {"status": "upstream_error", "message": "Máy chủ nguồn đang lỗi 502/503/504. Phiên đã lưu được giữ; hãy tải lại hoặc thử tìm sau, không cần đăng nhập lại."}
    if any(word in detail for word in ("captcha", "requires verification", "verify")):
        return {"status": "verification_required", "message": "Nền tảng yêu cầu xác minh trong phiên trình duyệt; chưa tìm được video từ nguồn này."}
    if "412" in detail:
        return {"status": "http_412", "message": "Nền tảng từ chối yêu cầu (HTTP 412), kể cả sau khi thử trang tìm kiếm công khai."}
    if "no browser" in detail:
        return {"status": "browser_unavailable", "message": "Chưa có trình duyệt để khảo sát nguồn này."}
    if "429" in detail or "too many requests" in detail:
        return {"status": "rate_limited", "message": "Nền tảng giới hạn lượt tìm kiếm. Agent tạm nghỉ trước khi thử lại."}
    if "no visible" in detail:
        return {"status": "no_results", "message": "Chưa đọc được kết quả từ trang nguồn. Có thể cần đăng nhập, trang đổi bố cục hoặc nguồn đang hạn chế truy cập."}
    if any(word in detail for word in ("login", "sign in", "log in", "redirected away")):
        return {"status": "login_required", "message": "Nguồn chưa trả được video. Mở đăng nhập nguồn, hoàn tất xác minh và lưu phiên rồi tìm lại."}
    return {"status": "unavailable", "message": "Nguồn chưa trả được kết quả. Kiểm tra kết nối hoặc phiên đăng nhập của nền tảng."}


class DiscoveryUnavailable(ValueError):
    def __init__(self, issue: dict) -> None:
        self.issue = dict(issue)
        super().__init__(issue["message"])


def source_identity(url: str) -> tuple[str, str, str]:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in {None, 443}:
        raise ValueError("Use a direct HTTPS video URL from YouTube, Bilibili, TikTok or Douyin.")
    platform = _HOSTS.get((parsed.hostname or "").lower())
    video_id = ""
    if platform == "youtube":
        if parsed.hostname == "youtu.be":
            video_id = parsed.path.strip("/")
        elif parsed.path == "/watch":
            video_id = parse_qs(parsed.query).get("v", [""])[0]
        else:
            match = re.fullmatch(r"/(?:shorts|embed)/([A-Za-z0-9_-]+)(?:/)?", parsed.path)
            video_id = match[1] if match else ""
        if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
            raise ValueError("YouTube source must identify one video.")
        canonical = f"https://www.youtube.com/watch?v={video_id}"
    elif platform == "bilibili":
        match = re.fullmatch(r"/(?:[a-z]{2}(?:-[A-Za-z]{2,8})?/)?video/(BV[A-Za-z0-9]+|av\d+)/?", parsed.path)
        if not match:
            raise ValueError("Bilibili source must identify one video.")
        video_id = match[1]
        # Different parts of a multipart video are different source items.
        part = parse_qs(parsed.query).get("p", ["1"])[0]
        if not part.isdigit() or int(part) < 1:
            raise ValueError("Invalid Bilibili part.")
        canonical = f"https://www.bilibili.com/video/{video_id}?p={int(part)}"
        video_id += f":p{int(part)}"
    elif platform == "tiktok":
        match = re.fullmatch(r"/@([A-Za-z0-9_.]+)/video/(\d+)/?", parsed.path)
        if not match:
            raise ValueError("TikTok source must identify one video.")
        video_id = match[2]
        canonical = f"https://www.tiktok.com/@{match[1]}/video/{video_id}"
    elif platform == "douyin":
        match = re.fullmatch(r"/video/(\d+)/?", parsed.path)
        if not match:
            raise ValueError("Douyin source must identify one video.")
        video_id = match[1]
        canonical = f"https://www.douyin.com/video/{video_id}"
    else:
        raise ValueError("Unsupported source platform.")
    return platform, video_id, canonical


@dataclass(frozen=True)
class Candidate:
    url: str
    platform: str
    video_id: str
    title: str = ""
    duration: float = 0
    view_count: int | None = None
    published_at: str | None = None
    channel_id: str = ""
    discovery_source: str = "search"
    search_query: str = ""
    like_count: int | None = None
    comment_count: int | None = None
    tags: tuple[str, ...] = ()
    description: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


class SourceDiscovery:
    def __init__(self, listing: ChannelListingService | None = None, *, sessions: SourceSessions | None = None) -> None:
        self.listing = listing or ChannelListingService(hard_limit=30)
        self.sessions = sessions
        self._owner = ContextVar(f"discovery_owner_{id(self)}", default=0)
        self._guard = threading.Lock()
        self._platform_locks: dict[tuple, threading.Lock] = {}
        self._cache: dict[tuple, tuple[float, list[Candidate]]] = {}
        self._blocked: dict[tuple, tuple[float, dict]] = {}

    @contextmanager
    def user_scope(self, user_id: int):
        token = self._owner.set(user_id)
        try:
            yield
        finally:
            self._owner.reset(token)

    def _credential_key(self, platform: str) -> tuple:
        owner = self._owner.get()
        signature = self.sessions.signature(owner, platform) if self.sessions else 0
        cookie = self._cookiefile(platform)
        try:
            cookie_signature = Path(cookie).stat().st_mtime_ns if cookie else 0
        except OSError:
            cookie_signature = 0
        return owner, platform, signature, cookie, cookie_signature

    def reset(self, owner: int, platform: str) -> None:
        with self._guard:
            self._cache = {key: value for key, value in self._cache.items() if key[:2] != (owner, platform)}
            self._blocked = {key: value for key, value in self._blocked.items() if key[:2] != (owner, platform)}

    def _cookiefile(self, platform: str) -> str | None:
        if self.sessions and self._owner.get() and platform in {"douyin", "tiktok", "bilibili"}:
            saved = self.sessions.directory(self._owner.get(), platform) / "cookies.txt"
            if saved.is_file():
                return str(saved)
        return os.getenv(f"{platform.upper()}_COOKIES_FILE") or os.getenv("YTDLP_COOKIES_FILE")

    def _cached(self, platform: str, kind: str, query: str, limit: int, operation) -> list[Candidate]:
        scope = self._credential_key(platform)
        key = (*scope, kind, query, limit)
        with self._guard:
            lock = self._platform_locks.setdefault((scope[0], platform), threading.Lock())
        # A single active request per account/platform prevents stampedes and
        # preserves the session when analysis and source scans overlap.
        with lock:
            scope = self._credential_key(platform)
            directory = self.sessions.directory(self._owner.get(), platform) if self.sessions and self._owner.get() and platform in {"douyin", "tiktok", "bilibili"} else None
            key = (*scope, kind, query, limit)
            now = time.monotonic()
            with self._guard:
                self._cache = {k: v for k, v in self._cache.items() if v[0] > now}
                self._blocked = {k: v for k, v in self._blocked.items() if v[0] > now}
                cached = self._cache.get(key)
                blocked = self._blocked.get(scope) or self._blocked.get(key)
            if cached:
                return list(cached[1])
            if blocked:
                until, issue = blocked
                raise DiscoveryUnavailable({**issue, "retry_after_seconds": max(1, int(until - now))})
            try:
                result = operation()
            except Exception as error:
                if not isinstance(error, (ValueError, OSError)) and not type(error).__module__.startswith(("playwright.", "yt_dlp.")):
                    _logger.exception("Unexpected source discovery failure platform=%s", platform)
                issue = discovery_issue(error)
                cooldown = 2 if issue["status"] == "session_open" else 30 if issue["status"] == "upstream_error" else 600 if issue["status"] in {"verification_required", "login_required", "http_412", "browser_unavailable"} else 120
                block_key = scope if issue["status"] in {"verification_required", "login_required", "http_412", "browser_unavailable", "rate_limited"} else key
                with self._guard:
                    self._blocked[block_key] = (time.monotonic() + cooldown, issue)
                    # yt-dlp can rewrite the cookie file even on HTTP 412.
                    # Bind the pause to the resulting credentials as well.
                    after = self._credential_key(platform)
                    if directory is None or self.sessions.directory(self._owner.get(), platform) == directory:
                        new_block = after if block_key == scope else (*after, kind, query, limit)
                        self._blocked[new_block] = (time.monotonic() + cooldown, issue)
                if kind == "search" and directory is not None and self.sessions.directory(self._owner.get(), platform) == directory:
                    self.sessions.record_access(self._owner.get(), platform, issue, query=query, signature=scope[2])
                _logger.info("Source paused platform=%s status=%s retry_after=%ss", platform, issue["status"], cooldown)
                raise DiscoveryUnavailable({**issue, "retry_after_seconds": cooldown}) from error
            if directory is not None and self.sessions.directory(self._owner.get(), platform) != directory:
                raise DiscoveryUnavailable({"status": "session_changed", "message": "Tài khoản nguồn vừa được đổi. Chưa dùng kết quả của phiên cũ; hãy tìm lại bằng tài khoản mới."})
            with self._guard:
                key = (*self._credential_key(platform), kind, query, limit)
                self._cache[key] = (time.monotonic() + (300 if result else 60), list(result))
            if kind == "search" and directory is not None:
                issue = {"status": "searched", "message": f"Đã tìm được {len(result)} video bằng phiên nguồn hiện tại."} if result else {
                    "status": "no_results", "message": "Trang tìm kiếm chưa trả video cho truy vấn này; phiên được giữ."}
                self.sessions.record_access(self._owner.get(), platform, issue, count=len(result), query=query,
                                           signature=self.sessions.signature(self._owner.get(), platform))
            return result

    def search(self, platform: str, topic: str, limit: int = 10) -> list[Candidate]:
        limit = min(20, max(1, limit))
        if self.sessions and self._owner.get() and platform in {"douyin", "tiktok", "bilibili"}:
            captured = []
            for row in self.sessions.read_search(self._owner.get(), platform, topic):
                try:
                    name, identity, canonical = source_identity(row["url"])
                    if name == platform:
                        fields = Candidate.__dataclass_fields__
                        captured.append(replace(Candidate(**{key: value for key, value in row.items() if key in fields}),
                                                url=canonical, platform=name, video_id=identity, search_query=topic, discovery_source="interactive_search"))
                except (ValueError, KeyError, TypeError):
                    continue
            if captured:
                return captured[:limit]
        return self._cached(platform, "search", topic, limit, lambda: self._search(platform, topic, limit))

    def _search(self, platform: str, topic: str, limit: int) -> list[Candidate]:
        if platform == "bilibili" and self.sessions and self._owner.get():
            # The public page can work while the indexed JSON endpoint returns
            # HTTP 412. Reuse the saved browser session before calling that API.
            try:
                return self._public_browse(platform, f"https://search.bilibili.com/all?keyword={quote(topic, safe='')}", limit)
            except Exception as error:
                if discovery_issue(error)["status"] != "browser_unavailable":
                    raise
                return self._indexed_search(platform, topic, limit)
        if platform in {"douyin", "tiktok"}:
            if platform == "douyin":
                return self._douyin_search(topic, limit)
            return self._public_browse(platform, f"https://www.tiktok.com/search/video?q={quote(topic, safe='')}", limit)
        if platform not in {"youtube", "bilibili"}:
            raise ValueError("Unsupported search platform.")
        try:
            results = self._indexed_search(platform, topic, limit)
            if results or platform != "bilibili":
                return results
        except Exception as error:
            if platform != "bilibili":
                raise
            _logger.info("Bilibili indexed search unavailable; trying its public search page")
            try:
                return self._public_browse("bilibili", f"https://search.bilibili.com/all?keyword={quote(topic, safe='')}", limit)
            except Exception as browser_error:
                if discovery_issue(browser_error)["status"] in {"session_open", "upstream_error", "browser_unavailable"}:
                    raise
                if "412" in str(error) and "verification" not in str(browser_error):
                    raise ValueError("Bilibili HTTP 412; public search page also unavailable") from browser_error
                raise
        return self._public_browse("bilibili", f"https://search.bilibili.com/all?keyword={quote(topic, safe='')}", limit)

    def _indexed_search(self, platform: str, topic: str, limit: int) -> list[Candidate]:
        import yt_dlp
        prefix = "ytsearch" if platform == "youtube" else "bilisearch"
        options = {"quiet": True, "skip_download": True, "extract_flat": True, "playlistend": limit,
                   "socket_timeout": 20, "retries": 1, "ignoreerrors": False}
        cookie = self._cookiefile(platform)
        if cookie:
            options["cookiefile"] = cookie
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(f"{prefix}{limit}:{topic}", download=False)
        result = []
        for entry in (info or {}).get("entries", []):
            if not entry:
                continue
            url = entry.get("webpage_url") or entry.get("url") or ""
            if platform == "youtube" and not url.startswith("https://"):
                url = f"https://www.youtube.com/watch?v={entry.get('id', '')}"
            try:
                name, identity, canonical = source_identity(url)
                result.append(Candidate(canonical, name, identity, str(entry.get("title") or ""), float(entry.get("duration") or 0),
                                        view_count=entry.get("view_count"), published_at=(datetime.fromtimestamp(entry["timestamp"], timezone.utc).isoformat()
                                            if isinstance(entry.get("timestamp"), (int, float)) else None),
                                        channel_id=str(entry.get("channel_id") or ""),
                                        like_count=entry.get("like_count"), comment_count=entry.get("comment_count"),
                                        tags=tuple(entry.get("tags") or ()), description=str(entry.get("description") or "")[:3000]))
            except ValueError:
                continue
        return result[:limit]

    def _douyin_search(self, topic: str, limit: int) -> list[Candidate]:
        return self._public_browse("douyin", f"https://www.douyin.com/search/{quote(topic, safe='')}?type=video", limit)

    def market(self, platform: str, limit: int = 20) -> list[Candidate]:
        pages = {"douyin": "https://www.douyin.com/hot", "tiktok": "https://www.tiktok.com/explore",
                 "bilibili": "https://www.bilibili.com/v/popular/all"}
        if platform not in pages:
            raise ValueError("Unsupported public market sample.")
        limit = min(20, max(1, limit))
        return self._cached(platform, "market", "", limit, lambda: self._public_browse(platform, pages[platform], limit))

    @staticmethod
    def matches_query(candidate: Candidate, query: str) -> bool:
        title = " ".join([candidate.title, *candidate.tags]).casefold()
        terms = [word for word in re.findall(r"\w+", query.casefold()) if len(word) > 1]
        for word in terms:
            if re.fullmatch(r"[\u4e00-\u9fff]{4,}", word):
                if any(word[i:i + 2] in title for i in range(len(word) - 1)):
                    return True
        return bool(terms) and sum(word in title for word in terms) >= min(2, len(terms))

    @staticmethod
    def public_metadata(platform: str, payload: object) -> list[Candidate]:
        """Parse metadata delivered to the public page, retaining missing counters."""
        result, pending = {}, [payload]
        visited = 0
        while pending and visited < 8000:
            node = pending.pop()
            visited += 1
            if isinstance(node, list):
                pending.extend(node[:200])
                continue
            if not isinstance(node, dict):
                continue
            pending.extend(value for value in node.values() if isinstance(value, (dict, list)))
            stats = node.get("statistics") or node.get("stats") or node.get("statsV2") or node.get("stat") or node
            if not isinstance(stats, dict):
                continue
            identity = node.get("aweme_id") if platform == "douyin" else node.get("bvid") if platform == "bilibili" else node.get("id")
            title = node.get("desc") if platform != "bilibili" else node.get("title")
            if not identity or not isinstance(title, str) or not title.strip():
                continue
            author = node.get("author") or node.get("owner") or {}
            if platform == "bilibili" and isinstance(author, str):
                author = {"mid": node.get("mid"), "name": author}
            if not isinstance(author, dict):
                continue
            if platform == "tiktok":
                handle = author.get("uniqueId") or author.get("unique_id")
                if not handle:
                    continue
                url = f"https://www.tiktok.com/@{handle}/video/{identity}"
            elif platform == "douyin":
                url = f"https://www.douyin.com/video/{identity}"
            else:
                url = f"https://www.bilibili.com/video/{identity}"
            def counter(*names: str) -> int | None:
                for name in names:
                    value = stats.get(name)
                    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                        return value
                    if isinstance(value, str) and value.isdigit():
                        return int(value)
                return None
            published = None
            timestamp = node.get("create_time") or node.get("createTime") or node.get("pubdate") or node.get("senddate")
            try:
                if timestamp is not None:
                    published = datetime.fromtimestamp(int(timestamp), timezone.utc).isoformat()
            except (ValueError, TypeError, OverflowError, OSError):
                pass
            try:
                name, identity, canonical = source_identity(url)
                title = html.unescape(re.sub(r"<[^>]+>", " ", title))
                result[identity] = Candidate(canonical, name, identity, title[:500],
                    duration=duration_seconds(platform, node),
                    view_count=counter("play_count", "playCount", "view", "play"), published_at=published,
                    channel_id=str(author.get("uid") or author.get("id") or author.get("mid") or ""),
                    like_count=counter("digg_count", "diggCount", "like"), comment_count=counter("comment_count", "commentCount", "reply"),
                    tags=tuple(dict.fromkeys(tag for tag in [*re.findall(r"#([^\s#]+)", title), *str(node.get("tag") or "").split(",")] if tag))[:10],
                    description=str(node.get("description") or node.get("desc") or "")[:3000])
            except ValueError:
                continue
        return list(result.values())

    def _public_browse(self, platform: str, url: str, limit: int) -> list[Candidate]:
        """Read visible metadata only; report login/verification instead of bypassing it."""
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as error:
            raise ValueError("No browser automation available; install Playwright and Chromium") from error
        with sync_playwright() as playwright:
            with self._source_browser_context(playwright, platform) as context:
                owner = self._owner.get()
                directory = self.sessions.directory(owner, platform) if self.sessions and owner else None
                signature = self.sessions.signature(owner, platform) if self.sessions else 0
                cookie = self._cookiefile(platform)
                if cookie and not (self.sessions and owner and self.sessions.signature(owner, platform)):
                    context.add_cookies(self.listing._playwright_cookies(cookie))
                page = context.new_page()
                page.set_default_timeout(3000)
                result = {}
                media_payloads = []
                response_failures = []
                sampling = any(path in url for path in ("/hot", "/explore", "/popular/"))
                searching = "search" in urlparse(url).netloc or "/search" in urlparse(url).path
                def accept(payload: object) -> None:
                    if platform == "douyin" and len(media_payloads) < 10:
                        media_payloads.append(payload)
                    for candidate in self.public_metadata(platform, payload):
                        result[candidate.video_id] = replace(candidate, discovery_source="public_discovery_sample" if sampling else "search")
                def response_metadata(response: object) -> None:
                    try:
                        host = urlparse(response.url).hostname or ""
                        if searching and "search" not in urlparse(response.url).path:
                            return
                        if not (host == f"{platform}.com" or host.endswith(f".{platform}.com")):
                            return
                        if response.status in {401, 403, 412, 429, 502, 503, 504}:
                            response_failures.append(response.status)
                        if "json" in response.headers.get("content-type", "") or "/search/" in urlparse(response.url).path:
                            accept(response.json())
                    except Exception:
                        _logger.debug("Public page metadata response was unavailable", exc_info=True)
                page.on("response", response_metadata)
                navigation = page.goto(url, wait_until="domcontentloaded", timeout=30000)
                if navigation and navigation.status in {401, 403, 412, 429, 502, 503, 504}:
                    raise ValueError(f"{platform} HTTP {navigation.status}; login or verification required")
                if searching and "search" not in urlparse(page.url).path and "search" not in urlparse(page.url).netloc:
                    raise ValueError(f"{platform} redirected away from search; source session is unavailable")
                # Hydration and search responses arrive asynchronously. Poll
                # them together, checking challenges before spending 12s on a
                # link selector which may never appear.
                for attempt in range(12):
                    if self.verification_visible(page):
                        raise ValueError(f"{platform} requires verification; complete it in the source search window.")
                    if any(token in page.url.lower() for token in ("/login", "passport.", "/verify", "/captcha")):
                        raise ValueError(f"{platform} requires verification or login")
                    for candidate in self.page_metadata(platform, page, searching=searching, sampling=sampling):
                        previous = result.get(candidate.video_id)
                        if previous is None:
                            result[candidate.video_id] = candidate
                        elif not previous.title.strip() and candidate.title.strip():
                            result[candidate.video_id] = replace(previous, title=candidate.title)
                    if len(result) >= limit or (result and attempt >= 5):
                        break
                    if attempt in {2, 5, 8}:
                        page.mouse.wheel(0, 1600)
                    page.wait_for_timeout(800)
                if not result:
                    if response_failures:
                        raise ValueError(f"{platform} HTTP {response_failures[0]}; login or verification required")
                    raise ValueError(f"No visible {platform} results; login, layout or platform restrictions may require source URLs.")
                if self.sessions and owner:
                    if platform == "douyin":
                        for payload in media_payloads:
                            self.sessions.save_media(owner, payload, directory=directory, user_agent=page.evaluate("() => navigator.userAgent"))
                    self.sessions.save(owner, platform, context.storage_state(), expected_signature=signature)
                return list(result.values())[:limit]

    @staticmethod
    def verification_visible(page) -> bool:
        selectors = ('iframe[src*="captcha"], iframe[src*="verify"], [class*="captcha_verify"], '
                     '#captcha-verify-container, #secsdk-captcha-drag-wrapper, [id*="captcha-verify"], '
                     '[class*="captcha-verify"], #captcha_container')
        challenges = page.locator(selectors)
        if any(challenges.nth(i).is_visible() for i in range(min(challenges.count(), 8))):
            return True
        text = page.get_by_text(re.compile(r"Drag the slider to|Verify to continue|请完成.*验证|拖动.*拼图", re.I))
        return any(text.nth(i).is_visible() for i in range(min(text.count(), 3))) or "验证码" in page.title()

    @staticmethod
    def search_url(platform: str, query: str) -> str:
        escaped = quote(query, safe="")
        return {"douyin": f"https://www.douyin.com/search/{escaped}?type=video",
                "tiktok": f"https://www.tiktok.com/search/video?q={escaped}",
                "bilibili": f"https://search.bilibili.com/all?keyword={escaped}"}[platform]

    @staticmethod
    def matches_search_url(platform: str, url: str, query: str) -> bool:
        parsed = urlparse(url)
        host = parsed.hostname or ""
        if parsed.scheme != "https" or not (host == platform + ".com" or host.endswith("." + platform + ".com")):
            return False
        values = parse_qs(parsed.query)
        if platform == "douyin":
            return unquote(parsed.path).rstrip("/") == "/search/" + query.rstrip("/")
        if platform == "tiktok":
            return parsed.path.rstrip("/") in {"/search", "/search/video"} and values.get("q", [None])[0] == query
        return "search" in host and values.get("keyword", [None])[0] == query

    @classmethod
    def page_metadata(cls, platform: str, page, *, searching: bool = True, sampling: bool = False) -> list[Candidate]:
        result = {}
        scripts = page.locator('script[type="application/json"], script#RENDER_DATA, script:not([src])')
        for i in range(min(scripts.count(), 40)):
            raw = scripts.nth(i).text_content() or ""
            for candidate in cls.embedded_metadata(platform, raw, searching=searching):
                result[candidate.video_id] = replace(candidate, discovery_source="public_discovery_sample" if sampling else "search")
        links = page.locator('a[href*="/video/"], a[href*="modal_id="]').evaluate_all("""nodes => nodes.slice(0, 100).map(node => ({
            href: node.getAttribute('href') || '',
            title: (node.getAttribute('aria-label') || node.getAttribute('title') || node.querySelector('h3,h2')?.getAttribute('title') || node.querySelector('h3,h2')?.innerText || (node.querySelector('img') ? '' : node.innerText) || '').slice(0, 500)
        }))""")
        for link in links:
            target = link["href"]
            modal = parse_qs(urlparse(target).query).get("modal_id", [""])[0]
            if platform == "douyin" and modal.isdigit():
                target = f"https://www.douyin.com/video/{modal}"
            try:
                name, identity, canonical = source_identity(urljoin(page.url, target))
            except ValueError:
                continue
            if name != platform:
                continue
            title = link["title"].strip()
            previous = result.get(identity)
            if previous is None:
                result[identity] = Candidate(canonical, name, identity, title, discovery_source="public_discovery_sample" if sampling else "search")
            elif not previous.title.strip() and title:
                result[identity] = replace(previous, title=title)
        return list(result.values())

    @contextmanager
    def _source_browser_context(self, driver, platform: str):
        owner = self._owner.get()
        if self.sessions and owner:
            with self.sessions.browser_context(driver, owner, platform, headless=True) as context:
                yield context
            return
        browser = None
        for _, options in self.listing._playwright_launch_candidates(headless=True):
            try:
                browser = driver.chromium.launch(**options)
                break
            except Exception:
                continue
        if browser is None:
            raise ValueError(f"No browser available for {platform} discovery.")
        try:
            context = browser.new_context(locale="en-US" if platform == "tiktok" else "zh-CN")
            yield context
        finally:
            browser.close()

    @classmethod
    def embedded_metadata(cls, platform: str, raw: str, *, searching: bool) -> list[Candidate]:
        if not raw or len(raw) > 5_000_000:
            return []
        try:
            text = unquote(raw) if raw.startswith("%") else raw.strip()
            if text.startswith("window.__INITIAL_STATE__"):
                # Decode JSON only; never execute page JavaScript.
                text = text.split("=", 1)[1].lstrip()
            payload, _ = json.JSONDecoder().raw_decode(text)
            if platform == "tiktok" and searching:
                scope = payload.get("__DEFAULT_SCOPE__", {}) if isinstance(payload, dict) else {}
                payload = [value for key, value in scope.items() if "search" in key] if isinstance(scope, dict) else []
            return cls.public_metadata(platform, payload)
        except (ValueError, TypeError, IndexError, RecursionError):
            return []

    def scan(self, profile: dict) -> dict:
        candidates, warnings = {}, []
        platform_status = []
        content_profile = profile.get("content_profile")
        explicit_sources: set[str] = set()
        for source in profile.get("seed_candidates", []):
            try:
                platform, identity, canonical = source_identity(source["url"])
                if platform not in profile.get("platforms", []):
                    continue
                fields = Candidate.__dataclass_fields__
                candidate = Candidate(**{key: value for key, value in source.items() if key in fields})
                if content_profile:
                    if not content_relevance(candidate.to_dict(), content_profile)["accepted"]:
                        continue
                elif profile.get("search_queries") and not candidate.search_query:
                    query = next((query for query in profile["search_queries"].get(platform, []) if self.matches_query(candidate, query)), None)
                    if query is None:
                        continue
                    candidate = replace(candidate, search_query=query)
                candidates[(platform, identity)] = replace(candidate, url=canonical, platform=platform, video_id=identity)
            except (ValueError, KeyError, TypeError):
                continue
        for url in profile.get("source_urls", []):
            try:
                platform, identity, canonical = source_identity(url)
                candidates[(platform, identity)] = Candidate(canonical, platform, identity)
                explicit_sources.add(canonical)
                continue
            except ValueError:
                pass
            # Validate platform host before invoking channel discovery.
            parsed = urlparse(url)
            if parsed.scheme != "https" or parsed.hostname not in _HOSTS:
                warnings.append("Skipped unsupported source URL.")
                continue
            try:
                scan = self.listing.scan(url, max_videos=10)
                for entry in scan.videos:
                    platform, identity, canonical = source_identity(entry.source_url)
                    candidates[(platform, identity)] = Candidate(canonical, platform, identity, entry.title, entry.duration)
            except Exception:
                _logger.info("Source channel discovery unavailable")
                warnings.append("A configured source channel could not be scanned; check its login and availability.")
        def search_platform(platform: str) -> tuple[dict, list[Candidate], list[str]]:
            found, notices = [], []
            queries = profile.get("search_queries", {}).get(platform) if "search_queries" in profile else profile.get("keywords") or [profile.get("topic", "")]
            queries = [query.strip() for query in queries or [] if isinstance(query, str) and query.strip()][:3]
            status = {"platform": platform, "queries": queries, "status": "searched", "message": "", "accepted": 0}
            if not queries:
                if profile.get("source_urls") and "search_queries" not in profile and not profile.get("keywords") and not profile.get("topic", "").strip():
                    status.update(status="not_requested", message="Chỉ xử lý nguồn đã chọn; chưa yêu cầu tìm thêm video.")
                    return status, found, notices
                missing_context = content_profile and not content_profile.get("context_terms")
                needs_translation = content_profile and not missing_context and platform in {"douyin", "bilibili"}
                status.update(status="translation_required" if needs_translation else "no_query", message="Chưa dịch được từ khóa nội dung sang tiếng Trung; chưa gửi tìm kiếm." if needs_translation else "Chưa có từ khóa nội dung cụ thể; chưa gửi tìm kiếm. Chuẩn bị lại từ khóa từ video của kênh." if missing_context else "Chưa có truy vấn tìm kiếm; chuẩn bị lại từ khóa trước khi tìm video.")
                notices.append(f"{platform}: {status['message']}")
                return status, found, notices
            if content_profile and platform in {"douyin", "bilibili"} and not all(re.search(r"[\u3400-\u9fff]", query) for query in queries):
                status.update(status="translation_required", message="Chưa có từ khóa tiếng Trung hợp lệ; không gửi tiêu đề hoặc từ khóa chưa dịch để tìm kiếm.")
                return status, found, notices
            for topic in queries:
                if not topic.strip():
                    continue
                try:
                    rows = self.search(platform, topic)
                    for candidate in rows:
                        if candidate.platform == platform:
                            found.append(replace(candidate, search_query=topic))
                    if any(candidate.discovery_source == "interactive_search" for candidate in rows):
                        status.update(queries=[topic], basis="interactive_search", message="Đọc kết quả từ cửa sổ tìm kiếm vừa xác minh; chưa mở thêm truy vấn khác.")
                        break
                except Exception as error:
                    _logger.info("Source search unavailable platform=%s status=%s", platform, discovery_issue(error)["status"])
                    status.update(discovery_issue(error))
                    notices.append(f"{platform}: {status['message']}")
                    break
            return status, found, notices

        platforms = list(dict.fromkeys(profile.get("platforms", [])))
        if platforms:
            # Separate platforms can progress concurrently; each task inherits
            # the app account's credentials and each platform serializes itself.
            with ThreadPoolExecutor(max_workers=min(4, len(platforms))) as pool:
                futures = [pool.submit(copy_context().run, search_platform, platform) for platform in platforms]
                for future in futures:
                    status, found, notices = future.result()
                    platform_status.append(status)
                    warnings.extend(notices)
                    for candidate in found:
                        previous = candidates.get((candidate.platform, candidate.video_id))
                        if previous:
                            candidate = replace(candidate,
                                view_count=candidate.view_count if candidate.view_count is not None else previous.view_count,
                                like_count=candidate.like_count if candidate.like_count is not None else previous.like_count,
                                comment_count=candidate.comment_count if candidate.comment_count is not None else previous.comment_count,
                                published_at=candidate.published_at or previous.published_at,
                                tags=candidate.tags or previous.tags)
                        candidates[(candidate.platform, candidate.video_id)] = candidate
        terms = [t.casefold() for t in profile.get("keywords", []) if t]
        exclude = [t.casefold() for t in profile.get("exclude_keywords", []) if t]
        selected = []
        for candidate in candidates.values():
            if candidate.platform == "youtube" and candidate.channel_id and candidate.channel_id == profile.get("channel_id"):
                continue
            title = candidate.title.casefold()
            relevance = content_relevance(candidate.to_dict(), content_profile) if content_profile else None
            if relevance is not None and not relevance["accepted"] and candidate.url not in explicit_sources:
                continue
            if not content_profile and profile.get("search_queries") and candidate.search_query and candidate.url not in explicit_sources:
                if not self.matches_query(candidate, candidate.search_query):
                    continue
            if any(term in title for term in exclude):
                continue
            # Direct video URLs are explicit selections; discovery results must match.
            if terms and candidate.url not in explicit_sources and not any(term in title for term in terms):
                continue
            if candidate.duration and not profile.get("min_seconds", 0) <= candidate.duration <= profile.get("max_seconds", 7200):
                continue
            if not candidate.duration and profile.get("automation") == "auto":
                warnings.append("Automatic processing skipped sources with unknown duration; verify their length before selecting them manually.")
                continue
            row = candidate.to_dict()
            row["ranking"] = rank_source(row)
            if relevance:
                row["relevance"] = relevance
            selected.append(row)
        selected.sort(key=lambda row: ((row.get("relevance") or {}).get("score", 0), source_rank_key(row)), reverse=True)
        for status in platform_status:
            status["retrieved"] = sum(candidate.platform == status["platform"] for candidate in candidates.values())
            status["accepted"] = sum(row["platform"] == status["platform"] for row in selected)
            status["rejected"] = status["retrieved"] - status["accepted"]
        # Independent platform queues prevent large lifetime YouTube view counts
        # from displacing relevant sources found on Douyin/Bilibili/TikTok.
        queues = {platform: deque(row for row in selected if row["platform"] == platform) for platform in profile.get("platforms", [])}
        order = [platform for platform in queues if platform != "youtube"] + (["youtube"] if "youtube" in queues else [])
        diversified = []
        while any(queues.values()):
            for platform in order:
                if queues[platform]:
                    diversified.append(queues[platform].popleft())
        diversified.extend(row for row in selected if row["platform"] not in queues)
        return {"candidates": diversified[:50], "warnings": list(dict.fromkeys(warnings)), "platform_status": platform_status}
