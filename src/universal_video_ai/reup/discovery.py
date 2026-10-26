"""Metadata-only source discovery through existing platform capabilities."""
from __future__ import annotations

from dataclasses import dataclass, asdict
import logging
import os
import re
from urllib.parse import urlparse, parse_qs, quote

from universal_video_ai.downloader.channel import ChannelListingService

__all__ = ["Candidate", "source_identity", "SourceDiscovery"]
_logger = logging.getLogger(__name__)
_HOSTS = {"youtube.com": "youtube", "www.youtube.com": "youtube", "m.youtube.com": "youtube",
          "youtu.be": "youtube", "www.bilibili.com": "bilibili", "bilibili.com": "bilibili",
          "space.bilibili.com": "bilibili", "www.douyin.com": "douyin", "douyin.com": "douyin"}


def source_identity(url: str) -> tuple[str, str, str]:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in {None, 443}:
        raise ValueError("Use a direct HTTPS video URL from YouTube, Bilibili or Douyin.")
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
        match = re.fullmatch(r"/video/(BV[A-Za-z0-9]+|av\d+)/?", parsed.path)
        if not match:
            raise ValueError("Bilibili source must identify one video.")
        video_id = match[1]
        # Different parts of a multipart video are different source items.
        part = parse_qs(parsed.query).get("p", ["1"])[0]
        if not part.isdigit() or int(part) < 1:
            raise ValueError("Invalid Bilibili part.")
        canonical = f"https://www.bilibili.com/video/{video_id}?p={int(part)}"
        video_id += f":p{int(part)}"
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

    def to_dict(self) -> dict:
        return asdict(self)


class SourceDiscovery:
    def __init__(self, listing: ChannelListingService | None = None) -> None:
        self.listing = listing or ChannelListingService(hard_limit=30)

    def search(self, platform: str, topic: str, limit: int = 10) -> list[Candidate]:
        limit = min(20, max(1, limit))
        if platform == "douyin":
            return self._douyin_search(topic, limit)
        if platform not in {"youtube", "bilibili"}:
            raise ValueError("Unsupported search platform.")
        import yt_dlp
        prefix = "ytsearch" if platform == "youtube" else "bilisearch"
        options = {"quiet": True, "skip_download": True, "extract_flat": True, "playlistend": limit,
                   "socket_timeout": 20, "retries": 1, "ignoreerrors": False}
        cookie = os.getenv(f"{platform.upper()}_COOKIES_FILE")
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
                result.append(Candidate(canonical, name, identity, str(entry.get("title") or ""), float(entry.get("duration") or 0)))
            except ValueError:
                continue
        return result[:limit]

    def _douyin_search(self, topic: str, limit: int) -> list[Candidate]:
        """Read visible search results; stop when login/captcha is required."""
        from playwright.sync_api import sync_playwright
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                context = browser.new_context(locale="zh-CN")
                cookie = os.getenv("DOUYIN_COOKIES_FILE")
                if cookie:
                    context.add_cookies(self.listing._playwright_cookies(cookie))
                page = context.new_page()
                page.goto(f"https://www.douyin.com/search/{quote(topic, safe='')}?type=video", wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(1500)
                result = {}
                for _ in range(3):
                    if page.locator('iframe[src*="captcha"], [class*="captcha_verify"]').count():
                        raise ValueError("Douyin requires verification; refresh cookies or use a configured source channel.")
                    links = page.locator('a[href*="/video/"]')
                    for i in range(min(links.count(), 100)):
                        link = links.nth(i)
                        url = link.get_attribute("href") or ""
                        if url.startswith("/"):
                            url = "https://www.douyin.com" + url
                        try:
                            platform, identity, canonical = source_identity(url)
                            result[identity] = Candidate(canonical, platform, identity, link.inner_text()[:300])
                        except ValueError:
                            continue
                    if len(result) >= limit:
                        break
                    page.mouse.wheel(0, 1600)
                    page.wait_for_timeout(800)
                if not result:
                    raise ValueError("No visible Douyin results; login, layout or platform restrictions may require source URLs.")
                return list(result.values())[:limit]
            finally:
                browser.close()

    def scan(self, profile: dict) -> dict:
        candidates, warnings = {}, []
        explicit_sources: set[str] = set()
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
                _logger.warning("Source channel discovery failed", exc_info=True)
                warnings.append("A configured source channel could not be scanned; check its login and availability.")
        for platform in profile.get("platforms", []):
            for topic in (profile.get("keywords") or [profile.get("topic", "")])[:3]:
                try:
                    for candidate in self.search(platform, topic):
                        candidates[(candidate.platform, candidate.video_id)] = candidate
                except Exception:
                    _logger.warning("%s source search unavailable", platform, exc_info=True)
                    warnings.append(f"{platform} search unavailable; source URLs remain usable.")
        terms = [t.casefold() for t in profile.get("keywords", []) if t]
        exclude = [t.casefold() for t in profile.get("exclude_keywords", []) if t]
        selected = []
        for candidate in candidates.values():
            title = candidate.title.casefold()
            if any(term in title for term in exclude):
                continue
            # Direct video URLs are explicit selections; discovery results must match.
            if terms and candidate.url not in explicit_sources and not any(term in title for term in terms):
                continue
            if candidate.duration and not profile.get("min_seconds", 0) <= candidate.duration <= profile.get("max_seconds", 7200):
                continue
            selected.append(candidate.to_dict())
        return {"candidates": selected[:50], "warnings": warnings}
