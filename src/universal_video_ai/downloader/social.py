"""Explicit yt-dlp adapters for supported social/video hosts."""

from .platform import Platform
from .ytdlp_downloader import YTDLPDownloader


class TwitterDownloader(YTDLPDownloader):
    def __init__(self):
        super().__init__(Platform.TWITTER)


class VimeoDownloader(YTDLPDownloader):
    def __init__(self):
        super().__init__(Platform.VIMEO)


class DailymotionDownloader(YTDLPDownloader):
    def __init__(self):
        super().__init__(Platform.DAILYMOTION)


class TwitchDownloader(YTDLPDownloader):
    def __init__(self):
        super().__init__(Platform.TWITCH)


class VKDownloader(YTDLPDownloader):
    def __init__(self):
        super().__init__(Platform.VK)


class WeiboDownloader(YTDLPDownloader):
    def __init__(self):
        super().__init__(Platform.WEIBO)


class XiaohongshuDownloader(YTDLPDownloader):
    def __init__(self):
        super().__init__(Platform.XIAOHONGSHU)
