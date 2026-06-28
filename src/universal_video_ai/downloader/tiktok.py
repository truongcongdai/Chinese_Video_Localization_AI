from .platform import Platform
from .ytdlp_downloader import YTDLPDownloader


class TikTokDownloader(YTDLPDownloader):

    def __init__(self):
        super().__init__(Platform.TIKTOK)