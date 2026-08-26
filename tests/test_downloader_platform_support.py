import pytest

from universal_video_ai.downloader.bilibili import BilibiliDownloader
from universal_video_ai.downloader.factory import DownloaderFactory
from universal_video_ai.downloader.platform import Platform
from universal_video_ai.downloader.platform_detector import PlatformDetector
from universal_video_ai.downloader.social import (
    DailymotionDownloader, TwitchDownloader, TwitterDownloader,
    VimeoDownloader, VKDownloader, WeiboDownloader, XiaohongshuDownloader,
)
from universal_video_ai.downloader.youtube import YoutubeDownloader
from universal_video_ai.downloader.ytdlp_downloader import _downloaded_filepath


@pytest.mark.parametrize(("url", "platform", "downloader_type"), [
    ("https://youtu.be/abc", Platform.YOUTUBE, YoutubeDownloader),
    ("https://www.bilibili.com/video/BV1xx", Platform.BILIBILI, BilibiliDownloader),
    ("https://b23.tv/abc", Platform.BILIBILI, BilibiliDownloader),
    ("https://x.com/user/status/123", Platform.TWITTER, TwitterDownloader),
    ("https://twitter.com/user/status/123", Platform.TWITTER, TwitterDownloader),
    ("https://vimeo.com/123", Platform.VIMEO, VimeoDownloader),
    ("https://dai.ly/abc", Platform.DAILYMOTION, DailymotionDownloader),
    ("https://clips.twitch.tv/abc", Platform.TWITCH, TwitchDownloader),
    ("https://vkvideo.ru/video1_2", Platform.VK, VKDownloader),
    ("https://weibo.com/tv/show/abc", Platform.WEIBO, WeiboDownloader),
    ("https://www.xiaohongshu.com/explore/abc", Platform.XIAOHONGSHU, XiaohongshuDownloader),
])
def test_platform_detection_is_wired_to_downloader(url, platform, downloader_type):
    assert PlatformDetector().detect(url) == platform
    assert isinstance(DownloaderFactory.create(platform), downloader_type)


def test_unknown_yt_dlp_site_keeps_generic_fallback():
    assert PlatformDetector().detect("https://example-video-host.test/watch/1") == Platform.GENERIC


def test_merged_download_uses_actual_filepath(tmp_path):
    merged = tmp_path / "clip.mkv"
    merged.write_bytes(b"video")

    class FakeYDL:
        @staticmethod
        def prepare_filename(info):
            return str(tmp_path / "clip.webm")

    assert _downloaded_filepath(FakeYDL(), {"filepath": str(merged)}) == merged
