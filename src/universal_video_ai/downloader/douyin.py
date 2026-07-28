import json
import logging
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
            response = requests.get(url, headers=headers, timeout=30)

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
                            video_url = url_list[0]
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
                    # Try to get title from desc
                    title = item.get('desc', title)
                    break

            if not video_url:
                logger.error("❌ Cannot find video URL in any loaderData key")
                return None

            # 5. Download video
            output_path = output_dir / _safe_video_filename(title, video_id)
            logger.info(f"📥 Downloading video to: {output_path}")
            
            video_response = requests.get(video_url, headers=headers, stream=True, timeout=120)

            if video_response.status_code != 200:
                logger.error(f"❌ Video download failed: {video_response.status_code}")
                return None

            with open(output_path, 'wb') as f:
                for chunk in video_response.iter_content(chunk_size=8192):
                    f.write(chunk)

            file_size = output_path.stat().st_size
            logger.info(f"✅ Downloaded via scraping: {output_path} ({file_size / (1024 * 1024):.2f} MB)")

            return DownloadResult(
                success=True,
                platform=self.platform,
                original_url=url,
                final_url=url,
                video_path=output_path,
                title=title,
                uploader="",
                duration=0,
                width=0,
                height=0,
                filesize=file_size,
                extension="mp4",
            )

        except Exception as e:
            logger.error(f"❌ Scraping error: {e}", exc_info=True)
            return None

    def download(self, url: str, output_dir: Path) -> DownloadResult:
        """Smart download with scraping fallback to yt-dlp"""
        logger.info(f"📥 Downloading Douyin video from: {url}")

        output_dir.mkdir(parents=True, exist_ok=True)

        # Strategy 1: HTML scraping (no login required)
        resolved_url = self._resolve_short_url(url)
        video_id = self._extract_video_id(resolved_url) or self._extract_video_id(url)

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
        return self._ytdlp_fallback.download(fallback_url, output_dir)
