from __future__ import annotations

from .platform import Platform

from .youtube import YoutubeDownloader
from .douyin import DouyinDownloader
from .kuaishou import KuaishouDownloader
from .facebook import FacebookDownloader
from .instagram import InstagramDownloader
from .tiktok import TikTokDownloader
from .reddit import RedditDownloader
from .bilibili import BilibiliDownloader
from .generic import GenericDownloader
from .social import (
    DailymotionDownloader,
    TwitchDownloader,
    TwitterDownloader,
    VimeoDownloader,
    VKDownloader,
    WeiboDownloader,
    XiaohongshuDownloader,
)

from .base import BaseDownloader


class DownloaderFactory:

    """
    Factory responsible for creating
    the correct downloader instance.
    """

    _MAPPING = {

        Platform.YOUTUBE: YoutubeDownloader,

        Platform.DOUYIN: DouyinDownloader,

        Platform.KUAISHOU: KuaishouDownloader,

        Platform.TIKTOK: TikTokDownloader,

        Platform.FACEBOOK: FacebookDownloader,

        Platform.INSTAGRAM: InstagramDownloader,

        Platform.BILIBILI: BilibiliDownloader,

        Platform.REDDIT: RedditDownloader,

        Platform.TWITTER: TwitterDownloader,

        Platform.VIMEO: VimeoDownloader,

        Platform.DAILYMOTION: DailymotionDownloader,

        Platform.TWITCH: TwitchDownloader,

        Platform.VK: VKDownloader,

        Platform.WEIBO: WeiboDownloader,

        Platform.XIAOHONGSHU: XiaohongshuDownloader,

        Platform.GENERIC: GenericDownloader,

    }

    @classmethod
    def create(
        cls,
        platform: Platform,
    ) -> BaseDownloader:

        downloader_cls = cls._MAPPING.get(
            platform,
            GenericDownloader,
        )

        return downloader_cls()
