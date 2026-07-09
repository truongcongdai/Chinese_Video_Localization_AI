import json
import logging
import re
import time
from pathlib import Path
from typing import Optional
from urllib.parse import unquote

import requests

from .base import BaseDownloader
from .download_result import DownloadResult
from .platform import Platform
from .ytdlp_downloader import YTDLPDownloader

logger = logging.getLogger(__name__)


class DouyinDownloader(BaseDownloader):

    def __init__(self):
        super().__init__(Platform.DOUYIN)
        self._ytdlp_fallback = YTDLPDownloader(Platform.DOUYIN)

    def _extract_video_id(self, url: str) -> Optional[str]:
        """Extract video ID from URL"""
        match = re.search(r'/video/(\d+)', url)
        if match:
            return match.group(1)

        match = re.search(r'/note/(\d+)', url)
        if match:
            return match.group(1)

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
            output_path = output_dir / f"{title}.mp4"
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
        video_id = self._extract_video_id(resolved_url)

        if video_id:
            logger.info("🎯 Strategy 1: Douyin HTML scraping...")
            result = self._download_douyin_scraping(video_id, output_dir)
            if result and result.success:
                return result

        # Strategy 2: yt-dlp fallback
        logger.info("🎯 Strategy 2: yt-dlp fallback...")
        return self._ytdlp_fallback.download(url, output_dir)