import asyncio
import json
import logging
import re
import shutil
import subprocess
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


def _run_coro_sync(coro):
    """Run an async coroutine to completion from synchronous code, whether
    or not we're already inside a running event loop (e.g. called from the
    web app's async job handler). `asyncio.run()` alone raises
    "cannot be called from a running event loop" in that second case, so
    when one is already running we hand the coroutine to a fresh thread
    with its own loop instead."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        # No loop running on this thread — the simple, common case.
        return asyncio.run(coro)

    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(asyncio.run, coro)
        return future.result()


def _check_js_runtime_available() -> bool:
    """
    douyin-tiktok-scraper computes Douyin's "X-Bogus" anti-bot signature by
    running a real X-Bogus.js file through `execjs`, which in turn needs an
    actual JS engine on the system — normally Node.js. If Node isn't
    installed (or isn't on PATH), execjs silently falls back to whatever
    other "runtime" it can find (e.g. Windows' old JScript engine), which
    can't run modern JS syntax and fails with a confusing
    `SyntaxError: Expected ';'` deep inside the library — not obviously a
    "Node is missing" error at all. We check up front so we can log
    something actionable instead.
    """
    node_path = shutil.which("node")
    if not node_path:
        return False
    try:
        result = subprocess.run([node_path, "--version"], capture_output=True, timeout=5, text=True)
        return result.returncode == 0
    except Exception:
        return False


_JS_RUNTIME_AVAILABLE = _check_js_runtime_available()
if not _JS_RUNTIME_AVAILABLE:
    logger.warning(
        "⚠️ No working Node.js found on PATH — Strategy 1 (douyin-tiktok-scraper) needs Node "
        "to compute Douyin's X-Bogus signature and will fail with a cryptic execjs SyntaxError "
        "without it. Install Node.js LTS (https://nodejs.org/) and make sure `node --version` "
        "works in the same terminal/environment that runs this app, then restart it."
    )


def _log_cookie_file_status() -> None:
    from .ytdlp_downloader import _resolve_cookiefile
    path = _resolve_cookiefile()
    if path:
        logger.info(f"🍪 Using Douyin cookies file: {path}")
    else:
        logger.warning(
            "⚠️ No Douyin cookies file found (checked DOUYIN_COOKIES_FILE env var and "
            "cookies/douyin.txt). yt-dlp and the HTML-scraping fallback are both likely to be "
            "blocked ('Fresh cookies needed' / WAF challenge) without one — export cookies "
            "from a logged-in douyin.com browser session (e.g. with the \"Get cookies.txt "
            "LOCALLY\" extension) and save them to cookies/douyin.txt. See README_WEB.md."
        )


_log_cookie_file_status()

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


def _load_cookie_header() -> Optional[str]:
    """
    Load the same Netscape-format cookies.txt used by yt-dlp
    (see ytdlp_downloader._resolve_cookiefile) and turn it into a plain
    `Cookie:` header string for our own raw `requests` calls below.

    Douyin increasingly rejects anonymous/cookie-less requests (both
    yt-dlp's extractor and our own HTML scraping hit this) — exporting
    cookies from a logged-in browser session is the standard workaround.
    Returns None if no cookie file is configured/found, in which case we
    just proceed cookie-less as before.
    """
    import http.cookiejar
    from .ytdlp_downloader import _resolve_cookiefile

    path = _resolve_cookiefile()
    if not path:
        return None
    try:
        jar = http.cookiejar.MozillaCookieJar(path)
        jar.load(ignore_discard=True, ignore_expires=True)
        return "; ".join(f"{c.name}={c.value}" for c in jar)
    except Exception as exc:
        logger.warning(f"⚠️ Could not parse cookies file {path}: {exc}")
        return None


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
        cookie_header = _load_cookie_header()
        if cookie_header:
            headers['Cookie'] = cookie_header

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
            # get_douyin_video_data is an async coroutine function — calling
            # it directly (without awaiting) just returns a coroutine
            # object, which is why `result.get(...)` below used to blow up
            # with "'coroutine' object has no attribute 'get'". This module
            # is sync, so we run it to completion via the helper above
            # instead of making the whole downloader chain async.
            result = _run_coro_sync(scraper.get_douyin_video_data(url))
            
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
        if DOUYIN_SCRAPER_AVAILABLE and not _JS_RUNTIME_AVAILABLE:
            logger.warning(
                "⏭️ Skipping Strategy 1 (douyin-tiktok-scraper): no working Node.js on PATH, "
                "so its X-Bogus signing would just fail after retrying for ~20s. See the "
                "warning logged at startup for how to fix this."
            )
        elif DOUYIN_SCRAPER_AVAILABLE:
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
        # The short link can redirect to either https://www.douyin.com/video/{id}
        # (which yt-dlp's Douyin extractor matches directly) or
        # https://www.iesdouyin.com/share/video/{id}/... (a mobile/share
        # variant Douyin serves depending on session state) — the latter was
        # falling through to yt-dlp's generic extractor ("Unsupported URL")
        # instead of the Douyin one. Rebuilding the canonical URL from the
        # video id we already extracted sidesteps that entirely.
        canonical_url = f"https://www.douyin.com/video/{video_id}" if video_id else resolved_url
        logger.info(f"🎯 Strategy 4: yt-dlp with resolved URL ({canonical_url})...")
        return self._ytdlp_fallback.download(canonical_url, output_dir)