import json
import logging
import re
from pathlib import Path
from typing import Optional
from urllib.parse import unquote

import requests

from .base import BaseDownloader
from .download_result import DownloadResult
from .platform import Platform
from .ytdlp_downloader import YTDLPDownloader

logger = logging.getLogger(__name__)


class KuaishouDownloader(BaseDownloader):

    def __init__(self):
        super().__init__(Platform.KUAISHOU)
        self._ytdlp_fallback = YTDLPDownloader(Platform.KUAISHOU)

    def _extract_video_id(self, url: str) -> Optional[str]:
        """Extract video ID from URL"""
        match = re.search(r'/short-video/(\w+)', url)
        if match:
            return match.group(1)

        match = re.search(r'/video/(\w+)', url)
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

    def _download_kuaishou_scraping(self, video_id: str, output_dir: Path) -> Optional[DownloadResult]:
        """Download Kuaishou by scraping HTML"""
        logger.info(f"🕷️ Scraping Kuaishou HTML for video: {video_id}")

        url = f"https://www.kuaishou.com/short-video/{video_id}"

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

            # 2. Try to extract video data from different patterns
            video_url = None
            title = f"kuaishou_{video_id}"

            # Pattern 1: Look for __INITIAL_STATE__ or similar JSON data
            patterns = [
                r'__INITIAL_STATE__\s*=\s*({.*?});',
                r'window\.__INITIAL_STATE__\s*=\s*({.*?});',
                r'data\s*=\s*({.*?});',
            ]

            for pattern in patterns:
                match = re.search(pattern, html, re.DOTALL)
                if match:
                    try:
                        data_str = match.group(1)
                        data = json.loads(data_str)

                        # Try to find video URL in the JSON structure
                        def find_video_url(obj):
                            if isinstance(obj, dict):
                                for key, value in obj.items():
                                    if key in ['url', 'playUrl', 'src', 'videoUrl'] and isinstance(value, str):
                                        if value.startswith('http') and ('mp4' in value or 'm3u8' in value):
                                            return value
                                    result = find_video_url(value)
                                    if result:
                                        return result
                            elif isinstance(obj, list):
                                for item in obj:
                                    result = find_video_url(item)
                                    if result:
                                        return result
                            return None

                        video_url = find_video_url(data)
                        if video_url:
                            logger.info(f"🎥 Found video URL via pattern: {pattern[:30]}...")
                            break

                    except json.JSONDecodeError:
                        continue

            # Pattern 2: Look for direct video URLs in HTML
            if not video_url:
                url_patterns = [
                    r'"url":"(https?://[^"]+\.mp4[^"]*)"',
                    r'"playUrl":"(https?://[^"]+)"',
                    r'videoUrl":"(https?://[^"]+)"',
                ]
                for pattern in url_patterns:
                    matches = re.findall(pattern, html)
                    if matches:
                        video_url = unquote(matches[0])
                        logger.info(f"🎥 Found video URL via regex: {video_url[:100]}...")
                        break

            if not video_url:
                logger.error("❌ Cannot find video URL in HTML")
                return None

            # 3. Download video
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
        logger.info(f"📥 Downloading Kuaishou video from: {url}")

        output_dir.mkdir(parents=True, exist_ok=True)

        # Strategy 1: HTML scraping (no login required)
        resolved_url = self._resolve_short_url(url)
        video_id = self._extract_video_id(resolved_url)

        if video_id:
            logger.info("🎯 Strategy 1: Kuaishou HTML scraping...")
            result = self._download_kuaishou_scraping(video_id, output_dir)
            if result and result.success:
                return result

        # Strategy 2: yt-dlp fallback
        logger.info("🎯 Strategy 2: yt-dlp fallback...")
        return self._ytdlp_fallback.download(url, output_dir)