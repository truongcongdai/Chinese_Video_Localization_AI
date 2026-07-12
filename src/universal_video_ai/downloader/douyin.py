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

try:
    from douyin_tiktok_scraper.scraper import Scraper
    DOUYIN_SCRAPER_AVAILABLE = True
    logger.info("✅ douyin-tiktok-scraper imported successfully")
except ImportError as e:
    DOUYIN_SCRAPER_AVAILABLE = False
    logger.warning(f"⚠️ douyin-tiktok-scraper import failed: {e}")
except Exception as e:
    DOUYIN_SCRAPER_AVAILABLE = False
    logger.warning(f"⚠️ douyin-tiktok-scraper import error: {e}")


class DouyinDownloader(BaseDownloader):

    def __init__(self):
        super().__init__(Platform.DOUYIN)
        self._ytdlp_fallback = YTDLPDownloader(Platform.DOUYIN)
        
        # Override yt-dlp options for Douyin
        self._ytdlp_fallback.get_extra_options = lambda: {
            "http_headers": {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Referer": "https://www.douyin.com/",
            },
            "nocheckcertificate": True,
            "ignoreerrors": True,
            "extractor_args": {
                "douyin": {
                    "webpage_check": False,
                }
            },
            "cookiefile": None,
            "extract_flat": False,
            "quiet": False,
        }

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
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Referer': 'https://www.douyin.com/',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'same-site',
            'Sec-Fetch-User': '?1',
            'Cache-Control': 'max-age=0',
        }

        try:
            # 1. Fetch HTML
            logger.info(f"🌐 Fetching: {url}")
            response = requests.get(url, headers=headers, timeout=30, allow_redirects=True)

            if response.status_code != 200:
                logger.error(f"❌ Failed to fetch HTML: {response.status_code}")
                return None

            html = response.text
            
            # Check if we got WAF challenge page
            if 'WAFJS' in html or 'waf' in html.lower() or len(html) < 5000:
                logger.warning("⚠️ WAF challenge detected, scraping method unavailable")
                return None

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

    def _download_with_scraper(self, url: str, output_dir: Path) -> Optional[DownloadResult]:
        """Download using douyin-tiktok-scraper library"""
        if not DOUYIN_SCRAPER_AVAILABLE:
            logger.warning("⚠️ douyin-tiktok-scraper not installed")
            return None

        try:
            logger.info("🎯 Using douyin-tiktok-scraper library...")
            scraper = Scraper()
            result = scraper.get_douyin_video_data(url)
            
            if not result:
                logger.error("❌ No data returned from douyin-tiktok-scraper")
                return None
            
            video_url = result.get('video_url')
            if not video_url:
                logger.error("❌ No video URL in scraper response")
                return None
            
            title = result.get('desc', f"douyin_video_{int(time.time())}")
            output_path = output_dir / f"{title}.mp4"
            
            logger.info(f"📥 Downloading video from scraper to: {output_path}")
            
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Referer': 'https://www.douyin.com/',
            }
            
            video_response = requests.get(video_url, headers=headers, stream=True, timeout=120)
            
            if video_response.status_code != 200:
                logger.error(f"❌ Video download failed: {video_response.status_code}")
                return None
            
            with open(output_path, 'wb') as f:
                for chunk in video_response.iter_content(chunk_size=8192):
                    f.write(chunk)
            
            file_size = output_path.stat().st_size
            logger.info(f"✅ Downloaded via scraper: {output_path} ({file_size / (1024 * 1024):.2f} MB)")
            
            return DownloadResult(
                success=True,
                platform=self.platform,
                original_url=url,
                final_url=url,
                video_path=output_path,
                title=title,
                uploader=result.get('author', {}).get('nickname', ''),
                duration=0,
                width=0,
                height=0,
                filesize=file_size,
                extension="mp4",
            )
            
        except Exception as e:
            logger.error(f"❌ Scraper error: {e}", exc_info=True)
            return None

    def download(self, url: str, output_dir: Path) -> DownloadResult:
        """Smart download with multiple strategies"""
        logger.info(f"📥 Downloading Douyin video from: {url}")

        output_dir.mkdir(parents=True, exist_ok=True)

        # Strategy 1: Try douyin-tiktok-scraper library first (best WAF handling)
        if DOUYIN_SCRAPER_AVAILABLE:
            logger.info("🎯 Strategy 1: douyin-tiktok-scraper library...")
            result = self._download_with_scraper(url, output_dir)
            if result and result.success:
                return result

        # Strategy 2: Try yt-dlp with the original URL
        logger.info("🎯 Strategy 2: yt-dlp with original URL...")
        try:
            result = self._ytdlp_fallback.download(url, output_dir)
            if result and result.success:
                return result
        except Exception as e:
            logger.warning(f"⚠️ yt-dlp failed: {e}")

        # Strategy 3: HTML scraping (no login required)
        resolved_url = self._resolve_short_url(url)
        video_id = self._extract_video_id(resolved_url)

        if video_id:
            logger.info("🎯 Strategy 3: Douyin HTML scraping...")
            result = self._download_douyin_scraping(video_id, output_dir)
            if result and result.success:
                return result

        # Strategy 4: yt-dlp with resolved URL
        logger.info("🎯 Strategy 4: yt-dlp with resolved URL...")
        return self._ytdlp_fallback.download(resolved_url, output_dir)