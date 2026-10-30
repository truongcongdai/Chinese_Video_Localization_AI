import errno
from http.cookiejar import MozillaCookieJar
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, unquote, urlparse

import requests

from .base import BaseDownloader
from .download_result import DownloadResult
from .media_validation import validate_video_file
from .platform import Platform
from .ytdlp_downloader import YTDLPDownloader, _cookiefile_for

logger = logging.getLogger(__name__)

_DOWNLOAD_CHUNK_SIZE = 1024 * 1024
_DOWNLOAD_MAX_ATTEMPTS = 6
_DOWNLOAD_RETRY_BASE_SECONDS = 1.0


class DouyinSourceError(RuntimeError):
    """A confirmed source-access block that needs the user's source window."""


def douyin_media_items(payload: object) -> list[dict]:
    """Read exact video identities and playback URLs from normal page responses."""
    pending, found = [payload], {}
    for _ in range(8000):
        if not pending:
            break
        row = pending.pop()
        if isinstance(row, list):
            pending.extend(row[:100])
            continue
        if not isinstance(row, dict):
            continue
        pending.extend(value for value in row.values() if isinstance(value, (dict, list)))
        identity = str(row.get("aweme_id") or row.get("awemeId") or "")
        video = row.get("video")
        if not identity.isdigit() or len(identity) > 24 or not isinstance(video, dict) or row.get("images"):
            continue
        urls = []
        for field in ("play_addr", "play_addr_h264", "playAddr", "playAddrH264"):
            address = video.get(field)
            values = address.get("url_list", address.get("urlList", [])) if isinstance(address, dict) else address if isinstance(address, list) else []
            for value in values:
                url = value.get("src") if isinstance(value, dict) else value
                parsed = urlparse(str(url or ""))
                host = parsed.hostname or ""
                if parsed.scheme in {"http", "https"} and any(host == domain or host.endswith("." + domain) for domain in (
                    "douyinvod.com", "douyin.com", "iesdouyin.com", "amemv.com", "snssdk.com", "bytecdn.cn", "bytecdn.com", "pstatp.com"
                )) and DouyinDownloader._is_plausible_video_url(str(url)):
                    if url not in urls:
                        urls.append(url)
            if urls:
                break  # Backup URLs must describe the same encoding.
        if not urls:
            continue
        author = row.get("author") or {}
        cover = video.get("cover") or {}
        cover_urls = cover.get("url_list", cover.get("urlList", [])) if isinstance(cover, dict) else []
        found[identity] = {"id": identity, "title": str(row.get("desc") or f"douyin_{identity}")[:500],
                           "uploader": str(author.get("nickname") or "") if isinstance(author, dict) else "",
                           "urls": urls[:8], "thumbnail_url": str(cover_urls[0]) if cover_urls else "",
                           "duration": video.get("duration") or 0, "width": video.get("width") or 0,
                           "height": video.get("height") or 0}
    return list(found.values())


def _safe_video_filename(title: str, video_id: str, max_bytes: int = 200) -> str:
    """Return a portable, byte-bounded filename for a Douyin video."""
    cleaned = re.sub(r'[\\/\x00-\x1f\x7f]+', "_", title).strip(" .")
    if not cleaned:
        cleaned = "douyin"

    suffix = f"_{video_id}.mp4"
    byte_budget = max(1, max_bytes - len(suffix.encode("utf-8")))
    encoded = cleaned.encode("utf-8")[:byte_budget]
    # A byte slice may end in the middle of a multi-byte character.
    cleaned = encoded.decode("utf-8", errors="ignore").rstrip(" .") or "douyin"
    return f"{cleaned}{suffix}"


class DouyinDownloader(BaseDownloader):

    def __init__(self):
        super().__init__(Platform.DOUYIN)
        self._ytdlp_fallback = YTDLPDownloader(Platform.DOUYIN)

    def _extract_video_id(self, url: str) -> Optional[str]:
        """Extract a Douyin video/note ID from short-resolved or full URLs."""
        decoded_url = unquote(url or "")

        path_patterns = [
            r"/video/(\d+)",
            r"/note/(\d+)",
            r"/share/video/(\d+)",
            r"/share/note/(\d+)",
        ]
        for pattern in path_patterns:
            match = re.search(pattern, decoded_url)
            if match:
                return match.group(1)

        parsed = urlparse(decoded_url)
        query = parse_qs(parsed.query)
        id_query_keys = (
            "modal_id",
            "aweme_id",
            "item_id",
            "share_item_id",
            "video_id",
            "note_id",
        )
        for key in id_query_keys:
            for value in query.get(key, []):
                match = re.search(r"\d{10,}", value)
                if match:
                    return match.group(0)

        embedded_match = re.search(
            r"(?:modal_id|aweme_id|item_id|share_item_id|video_id|note_id)[\"':=]+(\d{10,})",
            decoded_url,
        )
        if embedded_match:
            return embedded_match.group(1)

        return None

    def _resolve_short_url(self, url: str) -> str:
        """Resolve short URL to get full URL"""
        headers = {
            'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 14_0 like Mac OS X)',
        }
        try:
            response = requests.get(url, headers=headers, allow_redirects=True, timeout=30)
            return response.url
        except Exception as e:
            logger.error(f"Failed to resolve URL: {e}")
            return url

    @staticmethod
    def _response_total_size(response: requests.Response, offset: int) -> Optional[int]:
        """Return the full object size advertised by a streaming response."""
        content_range = response.headers.get("Content-Range", "")
        match = re.search(r"bytes\s+(?:\d+-\d+|\*)/(\d+)", content_range, re.IGNORECASE)
        if match:
            return int(match.group(1))

        content_length = response.headers.get("Content-Length")
        if content_length and content_length.isdigit():
            return offset + int(content_length) if response.status_code == 206 else int(content_length)
        return None

    @staticmethod
    def _is_plausible_video_url(url: str) -> bool:
        """Reject known placeholder/static URLs returned as play addresses."""
        candidate = str(url or "").strip()
        if not candidate.startswith(("http://", "https://")):
            return False
        lowered = candidate.lower()
        if any(marker in lowered for marker in ("static.bytednsdoc.com", "/nulog", ".png", ".jpg")):
            return False
        query = parse_qs(urlparse(candidate).query)
        for value in query.get("video_id", []):
            decoded = unquote(value).strip().lower()
            if decoded.startswith(("http://", "https://")):
                return False
        return True

    def _download_stream_with_resume(
        self,
        video_url: str,
        output_path: Path,
        headers: dict[str, str],
        *, cookies=None,
    ) -> Optional[int]:
        """Download a large CDN object while preserving a resumable ``.part`` file."""
        part_path = output_path.with_suffix(f"{output_path.suffix}.part")

        # Older versions wrote incomplete data directly to the final name.
        # Preserve it and let the CDN confirm/complement it with a Range request.
        if output_path.exists() and not part_path.exists():
            os.replace(output_path, part_path)

        for attempt in range(1, _DOWNLOAD_MAX_ATTEMPTS + 1):
            offset = part_path.stat().st_size if part_path.exists() else 0
            request_headers = dict(headers)
            request_headers.setdefault("Accept-Encoding", "identity")
            if offset:
                request_headers["Range"] = f"bytes={offset}-"
                logger.info(
                    "Resuming Douyin download at %.2f MB (attempt %d/%d)",
                    offset / (1024 * 1024), attempt, _DOWNLOAD_MAX_ATTEMPTS,
                )

            try:
                with requests.get(
                    video_url,
                    headers=request_headers,
                    stream=True,
                    cookies=cookies,
                    timeout=(30, 120),
                ) as video_response:
                    if video_response.status_code == 416 and offset:
                        total_size = self._response_total_size(video_response, offset)
                        if total_size is not None and total_size == offset:
                            os.replace(part_path, output_path)
                            return offset
                        logger.warning("Douyin CDN rejected stale partial download; restarting")
                        part_path.unlink(missing_ok=True)
                        continue

                    if video_response.status_code not in {200, 206}:
                        logger.error("Douyin video download failed: HTTP %s", video_response.status_code)
                        return None

                    append = bool(offset and video_response.status_code == 206)
                    if offset and not append:
                        logger.warning("Douyin CDN ignored Range; restarting download from byte 0")
                        offset = 0

                    expected_size = self._response_total_size(video_response, offset)
                    mode = "ab" if append else "wb"
                    with open(part_path, mode) as output_file:
                        for chunk in video_response.iter_content(chunk_size=_DOWNLOAD_CHUNK_SIZE):
                            if chunk:
                                output_file.write(chunk)

                received_size = part_path.stat().st_size
                if expected_size is not None and received_size != expected_size:
                    raise requests.exceptions.ChunkedEncodingError(
                        f"incomplete Douyin download: received {received_size} of {expected_size} bytes"
                    )

                os.replace(part_path, output_path)
                return received_size
            except (requests.exceptions.RequestException, OSError) as exc:
                if isinstance(exc, OSError) and exc.errno == errno.ENOSPC:
                    # Disk exhaustion is not a network interruption. Preserve
                    # the .part file and surface the real error immediately;
                    # falling through to yt-dlp would misleadingly ask for
                    # Douyin cookies and can never create free space.
                    raise
                if attempt >= _DOWNLOAD_MAX_ATTEMPTS:
                    logger.error(
                        "Douyin download still incomplete after %d attempts; keeping %s for a later resume: %s",
                        _DOWNLOAD_MAX_ATTEMPTS, part_path, exc,
                    )
                    return None
                delay = min(_DOWNLOAD_RETRY_BASE_SECONDS * (2 ** (attempt - 1)), 10.0)
                logger.warning(
                    "Douyin download interrupted (%s); retrying from the partial file in %.1fs",
                    exc, delay,
                )
                time.sleep(delay)

        return None

    def _download_media_item(self, item: dict, output_dir: Path, original_url: str, *, user_agent: str = "") -> Optional[DownloadResult]:
        headers = {"User-Agent": user_agent or item.get("user_agent") or "Mozilla/5.0", "Referer": original_url}
        cookies = None
        cookiefile = _cookiefile_for(Platform.DOUYIN)
        if cookiefile:
            cookies = MozillaCookieJar(cookiefile)
            cookies.load(ignore_discard=True, ignore_expires=False)
        output_path = output_dir / _safe_video_filename(item["title"], item["id"])
        for url in item["urls"][:3]:
            size = self._download_stream_with_resume(url, output_path, headers, cookies=cookies)
            if size is None:
                continue
            valid, reason = validate_video_file(output_path)
            if not valid:
                output_path.unlink(missing_ok=True)
                logger.warning("Douyin playback response is not valid media: %s", reason)
                continue
            return DownloadResult(success=True, platform=self.platform, original_url=original_url, final_url=original_url,
                                  video_path=output_path, title=item["title"], uploader=item.get("uploader", ""),
                                  duration=float(item.get("duration") or 0) / 1000, width=int(item.get("width") or 0),
                                  height=int(item.get("height") or 0), filesize=size, extension="mp4", description=item["title"],
                                  thumbnail_url=item.get("thumbnail_url", ""), raw_metadata={"aweme_id": item["id"]})
        return None

    def _download_with_source_session(self, video_id: str, output_dir: Path, original_url: str) -> Optional[DownloadResult]:
        from universal_video_ai.reup.source_sessions import source_download_context
        binding = source_download_context()
        if not binding:
            return None
        sessions, owner, directories = binding
        directory = directories["douyin"]
        if sessions.directory(owner, "douyin") != directory:
            raise DouyinSourceError("Tài khoản nguồn Douyin vừa thay đổi. Bấm Thử lại để dùng phiên tài khoản hiện tại.")
        cached = sessions.read_media(owner, video_id)
        if cached:
            result = self._download_media_item(cached, output_dir, original_url)
            if result:
                return result
        blocked = sessions.download_issue(owner)
        if blocked:
            raise DouyinSourceError(blocked)
        from playwright.sync_api import sync_playwright, Error as BrowserError
        from universal_video_ai.reup.discovery import SourceDiscovery
        item = None
        user_agent = ""
        failures = []
        try:
            with sync_playwright() as driver:
                with sessions.browser_context(driver, owner, "douyin", headless=True) as context:
                    signature = sessions.signature(owner, "douyin")
                    page = context.new_page()
                    def response_metadata(response):
                        parsed = urlparse(response.url)
                        if parsed.hostname not in {"www.douyin.com", "www.iesdouyin.com"} or "/aweme/" not in parsed.path:
                            return
                        if "/aweme/detail/" in parsed.path and response.status in {401, 403, 412, 429}:
                            failures.append(response.status)
                        try:
                            payload = response.json()
                            if any(row["id"] == video_id for row in douyin_media_items(payload)) and not SourceDiscovery.verification_visible(page):
                                sessions.save_media(owner, payload, directory=directory, user_agent=user_agent)
                        except (BrowserError, ValueError):
                            pass
                    page.on("response", response_metadata)
                    page.goto(original_url, wait_until="domcontentloaded", timeout=25000)
                    user_agent = page.evaluate("() => navigator.userAgent")
                    for attempt in range(10):
                        if SourceDiscovery.verification_visible(page):
                            raise DouyinSourceError("Douyin yêu cầu xác minh video trong phiên đã lưu. Bấm Kiểm tra video nguồn trên thẻ video và hoàn tất yêu cầu trong cửa sổ đó; không cần xuất cookie.")
                        if self._extract_video_id(page.url) == video_id:
                            payload = page.evaluate("() => [window._ROUTER_DATA, window.__INITIAL_STATE__, window.RENDER_DATA].filter(x => x && typeof x === 'object')")
                            sessions.save_media(owner, payload, directory=directory, user_agent=user_agent)
                        item = sessions.read_media(owner, video_id)
                        if item or (failures and attempt >= 2):
                            break
                        page.wait_for_timeout(800)
                    previous = sessions.state_path(owner, "douyin")
                    origins = json.loads(previous.read_text()).get("origins", []) if previous.is_file() else []
                    sessions.save(owner, "douyin", {"cookies": context.cookies(), "origins": origins}, expected_signature=signature)
        except DouyinSourceError as error:
            sessions.download_issue(owner, str(error))
            raise
        except ValueError as error:
            raise DouyinSourceError("Cửa sổ Douyin đang bận hoặc chưa mở được. Đóng cửa sổ nguồn rồi bấm Thử lại; phiên đã lưu được giữ.") from error
        if sessions.directory(owner, "douyin") != directory:
            raise DouyinSourceError("Tài khoản nguồn Douyin vừa thay đổi. Chưa dùng dữ liệu từ phiên cũ; bấm Thử lại.")
        if item:
            result = self._download_media_item(item, output_dir, original_url, user_agent=user_agent)
            if result:
                return result
        reason = f" (HTTP {failures[0]})" if failures else ""
        message = f"Douyin chưa cho tải video trong phiên hiện tại{reason}. Bấm Kiểm tra video nguồn để xem video có phát được và hoàn tất xác minh nếu được yêu cầu; không cần xuất cookie hoặc đăng nhập lại khi phiên còn hiệu lực."
        sessions.download_issue(owner, message)
        raise DouyinSourceError(message)

    def _download_douyin_scraping(self, video_id: str, output_dir: Path) -> Optional[DownloadResult]:
        """Download Douyin by scraping HTML from iesdouyin.com"""
        logger.info(f"🕷️ Scraping Douyin HTML for video: {video_id}")

        url = f"https://www.iesdouyin.com/share/video/{video_id}/"

        headers = {
            'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 14_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.0 Mobile/15E148 Safari/604.1',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9',
        }

        try:
            # 1. Fetch HTML
            logger.info(f"🌐 Fetching: {url}")
            cookies = None
            cookiefile = _cookiefile_for(Platform.DOUYIN)
            if cookiefile:
                try:
                    cookie_jar = MozillaCookieJar(cookiefile)
                    cookie_jar.load(ignore_discard=True, ignore_expires=True)
                    cookies = cookie_jar
                    logger.info("Using configured Douyin cookies for HTML scraping")
                except Exception as exc:
                    logger.warning("Could not read Douyin cookie file %s: %s", cookiefile, exc)
            response = requests.get(url, headers=headers, cookies=cookies, timeout=30)

            if response.status_code != 200:
                logger.error(f"❌ Failed to fetch HTML: {response.status_code}")
                return None

            html = response.text

            # 2. Extract _ROUTER_DATA
            match = re.search(r'window\._ROUTER_DATA\s*=\s*({.*?})\s*</script>', html, re.DOTALL)
            if not match:
                logger.error("❌ Cannot find _ROUTER_DATA in HTML")
                return None

            # 3. Parse JSON
            data_str = unquote(match.group(1))
            data = json.loads(data_str)

            # 4. Find video URL in loaderData
            loader_data = data.get('loaderData')
            if not loader_data:
                logger.error("❌ loaderData is None")
                return None

            logger.info(f"🔍 loaderData keys: {list(loader_data.keys())}")

            video_url = None
            title = f"douyin_{video_id}"
            uploader = ""
            thumbnail_url = ""
            raw_metadata = {}

            # Duyệt qua TẤT CẢ keys, tìm key có video data
            for key, page_data in loader_data.items():
                if not isinstance(page_data, dict):
                    continue

                # Tìm videoInfoRes trong page_data
                video_info_res = page_data.get('videoInfoRes')
                if not video_info_res:
                    continue

                logger.info(f"✅ Found videoInfoRes in key: {key}")

                item_list = video_info_res.get('item_list', [])
                if not item_list:
                    logger.warning(f"⚠️ item_list is empty in key: {key}")
                    continue

                # Lấy item đầu tiên
                item = item_list[0]
                video_data = item.get('video')
                if not video_data:
                    logger.warning(f"⚠️ video data is None in key: {key}")
                    continue

                # Thử nhiều field khác nhau để lấy video URL
                for field_name in ['play_addr', 'download_addr', 'play_addr_h264']:
                    addr_data = video_data.get(field_name)
                    if addr_data and isinstance(addr_data, dict):
                        url_list = addr_data.get('url_list', [])
                        if url_list and isinstance(url_list, list) and len(url_list) > 0:
                            video_url = next(
                                (
                                    str(candidate)
                                    for candidate in url_list
                                    if self._is_plausible_video_url(str(candidate))
                                ),
                                None,
                            )
                            if not video_url:
                                logger.warning(
                                    "Ignoring implausible %s URL candidate(s) for video %s",
                                    field_name, video_id,
                                )
                                continue
                            # Douyin's own returned URL is the WATERMARKED
                            # stream — it's the same video served from a
                            # "/playwm/" path ("wm" = watermark). Swapping
                            # that for "/play/" fetches the identical video
                            # from Douyin's own CDN with no logo/@handle
                            # burned into the pixels at all. This is the
                            # standard, well-documented way every Douyin
                            # "no watermark" downloader works — it isn't
                            # re-encoding or cropping anything, just
                            # requesting the clean stream Douyin already
                            # serves for in-app playback.
                            if 'playwm' in video_url:
                                video_url = video_url.replace('playwm', 'play')
                                logger.info("🧼 Rewrote play URL to the non-watermarked stream")
                            logger.info(f"🎥 Found video URL in {field_name}: {video_url[:100]}...")
                            break

                if video_url:
                    # Preserve source metadata for the post-render Publishing Pack.
                    title = item.get('desc', title)
                    author = item.get('author') or {}
                    uploader = str(author.get('nickname') or author.get('unique_id') or '')
                    cover = (video_data.get('cover') or video_data.get('dynamic_cover') or {})
                    cover_urls = cover.get('url_list') if isinstance(cover, dict) else []
                    thumbnail_url = str(cover_urls[0]) if cover_urls else ''
                    raw_metadata = {
                        "aweme_id": str(item.get("aweme_id") or video_id),
                        "author": author,
                        "statistics": item.get("statistics") or {},
                        "create_time": item.get("create_time"),
                    }
                    break

            if not video_url:
                logger.error("❌ Cannot find video URL in any loaderData key")
                return None

            # 5. Download video
            output_path = output_dir / _safe_video_filename(title, video_id)
            logger.info(f"📥 Downloading video to: {output_path}")
            
            file_size = self._download_stream_with_resume(video_url, output_path, headers)
            if file_size is None:
                return None
            valid, reason = validate_video_file(output_path)
            if not valid:
                logger.error("Downloaded Douyin response is not a valid video: %s", reason)
                output_path.unlink(missing_ok=True)
                return None
            logger.info(f"✅ Downloaded via scraping: {output_path} ({file_size / (1024 * 1024):.2f} MB)")

            return DownloadResult(
                success=True,
                platform=self.platform,
                original_url=url,
                final_url=url,
                video_path=output_path,
                title=title,
                uploader=uploader,
                duration=0,
                width=0,
                height=0,
                filesize=file_size,
                extension="mp4",
                description=title,
                thumbnail_url=thumbnail_url,
                raw_metadata=raw_metadata,
            )

        except OSError as e:
            if e.errno == errno.ENOSPC:
                raise
            logger.error(f"❌ Scraping error: {e}", exc_info=True)
            return None
        except Exception as e:
            logger.error(f"❌ Scraping error: {e}", exc_info=True)
            return None

    def download(self, url: str, output_dir: Path) -> DownloadResult:
        """Smart download with scraping fallback to yt-dlp"""
        requested_video_id = self._extract_video_id(url)
        logger.info(
            "📥 Downloading Douyin video url=%s requested_video_id=%s output_dir=%s",
            url,
            requested_video_id or "unknown",
            output_dir,
        )

        output_dir.mkdir(parents=True, exist_ok=True)

        if requested_video_id:
            result = self._download_with_source_session(requested_video_id, output_dir, f"https://www.douyin.com/video/{requested_video_id}")
            if result:
                return result

        # Strategy 1: HTML scraping (no login required)
        resolved_url = self._resolve_short_url(url)
        resolved_video_id = self._extract_video_id(resolved_url)
        if requested_video_id and resolved_video_id and requested_video_id != resolved_video_id:
            logger.warning(
                "Douyin redirect changed video id from %s to %s; preserving requested id",
                requested_video_id,
                resolved_video_id,
            )
        video_id = requested_video_id or resolved_video_id

        if video_id:
            logger.info("🎯 Strategy 1: Douyin HTML scraping...")
            result = self._download_douyin_scraping(video_id, output_dir)
            if result and result.success:
                return result

        # Strategy 2: yt-dlp fallback.
        #
        # BUG (seen in production logs): passing the raw resolved URL here
        # sends yt-dlp a `iesdouyin.com/share/video/...` link decorated with
        # long tracking query params (region=, share_sign=, ts=, ...). That
        # URL doesn't match yt-dlp's Douyin extractor regex, so it falls
        # through to the generic extractor and raises
        # `UnsupportedError`. When it *does* occasionally match (after yet
        # another redirect to www.douyin.com/video/{id}), yt-dlp's Douyin
        # extractor then demands fresh cookies, which we don't have.
        #
        # Fix: once we already have the canonical numeric video_id (either
        # from the original URL or by resolving the short link above),
        # build the clean, well-known URL shape
        # (`https://www.douyin.com/video/{id}`) that yt-dlp's Douyin
        # extractor is written to match directly. If we can't extract an id
        # at all, fall back to the original URL untouched.
        logger.info("🎯 Strategy 2: yt-dlp fallback...")
        fallback_url = f"https://www.douyin.com/video/{video_id}" if video_id else url
        if fallback_url != url:
            logger.info(f"🔧 Normalized URL for yt-dlp: {fallback_url}")
        try:
            return self._ytdlp_fallback.download(fallback_url, output_dir)
        except Exception as exc:
            error_message = str(exc).lower()
            if (
                "could not copy chrome cookie database" in error_message
                or ("permission denied" in error_message and "network\\cookies" in error_message)
            ):
                raise RuntimeError(
                    "Không thể đọc cookie Douyin từ Chrome vì Chrome đang mở và khóa cơ sở "
                    "dữ liệu cookie trên Windows. Hãy đóng hoàn toàn mọi tiến trình Chrome rồi "
                    "thử lại, hoặc xuất cookie Netscape vào "
                    "local_data/cookies/douyin.com.cookies.txt."
                ) from exc
            if "fresh cookies" in error_message:
                raise RuntimeError(
                    "Douyin yêu cầu cookie mới. Hãy xuất cookie Netscape từ trình duyệt vào "
                    "local_data/cookies/douyin.com.cookies.txt, hoặc cấu hình "
                    "DOUYIN_COOKIES_FILE / DOUYIN_COOKIES_FROM_BROWSER rồi thử lại."
                ) from exc
            raise
