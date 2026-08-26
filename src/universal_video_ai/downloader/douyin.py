import errno
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
from .platform import Platform
from .ytdlp_downloader import YTDLPDownloader

logger = logging.getLogger(__name__)

_DOWNLOAD_CHUNK_SIZE = 1024 * 1024
_DOWNLOAD_MAX_ATTEMPTS = 6
_DOWNLOAD_RETRY_BASE_SECONDS = 1.0


def _clean_playback_url(video_data: dict) -> tuple[Optional[str], Optional[str]]:
    """Return a playback stream and never Douyin's watermarked save stream."""
    for field_name in ("play_addr_h264", "play_addr", "play_addr_265"):
        address = video_data.get(field_name)
        if not isinstance(address, dict):
            continue
        for candidate in address.get("url_list") or ():
            if isinstance(candidate, str) and candidate.startswith(("http://", "https://")):
                return candidate.replace("/playwm/", "/play/"), field_name
    return None, None


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

    def _download_stream_with_resume(
        self,
        video_url: str,
        output_path: Path,
        headers: dict[str, str],
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

    def _download_douyin_api(self, video_id: str, output_dir: Path) -> Optional[DownloadResult]:
        """Download Douyin using official API (no cookies required)"""
        logger.info(f"📱 Using Douyin API for video: {video_id}")

        # Use Android app user agent to access the official API
        headers = {
            'User-Agent': 'com.ss.android.ugc.aweme/280102 (Linux; U; Android 12; vi_VN; Pixel 6; Build/SQ3A.220605.009; Cronet/TTNetVersion:6d4c8c0d 2021-08-20 QuicVersion:0144d359 2021-07-28)'
        }

        api_url = f"https://aweme.snssdk.com/aweme/v1/feed/?aweme_id={video_id}"

        try:
            logger.info(f"🌐 Fetching from API: {api_url}")
            response = requests.get(api_url, headers=headers, timeout=30)

            if response.status_code != 200:
                logger.error(f"❌ API request failed: {response.status_code}")
                return None

            data = json.loads(response.text)
            aweme_list = data.get('aweme_list', [])

            if not aweme_list:
                logger.error("❌ No videos found in API response")
                return None

            # Find the matching video
            aweme = None
            for item in aweme_list:
                if str(item.get('aweme_id')) == video_id:
                    aweme = item
                    break

            if not aweme:
                logger.warning(f"⚠️ Video {video_id} not found in list, using first item")
                aweme = aweme_list[0]

            video_data = aweme.get('video')
            if not video_data:
                logger.error("❌ No video data in API response")
                return None

            # Try to get video URL from different fields
            video_url, field_name = _clean_playback_url(video_data)
            # Never fall back to download_addr: it is commonly the app's
            # watermarked "save video" rendition.
            for field_name in (() if video_url else ('play_addr_h264', 'play_addr', 'play_addr_265')):
                addr_data = video_data.get(field_name)
                if addr_data and isinstance(addr_data, dict):
                    url_list = addr_data.get('url_list', [])
                    if url_list and isinstance(url_list, list) and len(url_list) > 0:
                        video_url = url_list[0]
                        logger.info(f"🎥 Found video URL in {field_name}: {video_url[:100]}...")
                        break

            if video_url:
                video_url = video_url.replace('/playwm/', '/play/')

            if not video_url:
                logger.error("❌ Cannot find video URL in API response")
                return None

            # Extract metadata
            title = aweme.get('desc', f"douyin_{video_id}")
            author = aweme.get('author') or {}
            uploader = str(author.get('nickname') or author.get('unique_id') or '')
            
            cover = (video_data.get('cover') or video_data.get('dynamic_cover') or {})
            cover_urls = cover.get('url_list') if isinstance(cover, dict) else []
            thumbnail_url = str(cover_urls[0]) if cover_urls else ''

            raw_metadata = {
                "aweme_id": str(aweme.get("aweme_id") or video_id),
                "author": author,
                "statistics": aweme.get("statistics") or {},
                "create_time": aweme.get("create_time"),
            }

            # Download video
            output_path = output_dir / _safe_video_filename(title, video_id)
            logger.info(f"📥 Downloading video to: {output_path}")

            download_headers = {
                'User-Agent': headers['User-Agent'],
                'Referer': 'https://www.douyin.com/',
            }

            file_size = self._download_stream_with_resume(video_url, output_path, download_headers)
            if file_size is None:
                return None

            logger.info(f"✅ Downloaded via API: {output_path} ({file_size / (1024 * 1024):.2f} MB)")

            return DownloadResult(
                success=True,
                platform=self.platform,
                original_url=api_url,
                final_url=api_url,
                video_path=output_path,
                title=title,
                uploader=uploader,
                duration=aweme.get('duration', 0) / 1000 if aweme.get('duration') else 0,
                width=video_data.get('width', 0),
                height=video_data.get('height', 0),
                filesize=file_size,
                extension="mp4",
                description=title,
                thumbnail_url=thumbnail_url,
                raw_metadata=raw_metadata,
            )

        except OSError as e:
            if e.errno == errno.ENOSPC:
                raise
            logger.error(f"❌ API download error: {e}", exc_info=True)
            return None
        except Exception as e:
            logger.error(f"❌ API download error: {e}", exc_info=True)
            return None

    def download(self, url: str, output_dir: Path) -> DownloadResult:
        """Smart download with API fallback to yt-dlp"""
        requested_video_id = self._extract_video_id(url)
        logger.info(
            "📥 Downloading Douyin video url=%s requested_video_id=%s output_dir=%s",
            url,
            requested_video_id or "unknown",
            output_dir,
        )

        output_dir.mkdir(parents=True, exist_ok=True)

        # Strategy 1: Official API (no login required)
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
            logger.info("🎯 Strategy 1: Douyin official API...")
            result = self._download_douyin_api(video_id, output_dir)
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
        return self._ytdlp_fallback.download(fallback_url, output_dir)
