from .platform import Platform
from .ytdlp_downloader import YTDLPDownloader


class YoutubeDownloader(YTDLPDownloader):

    def __init__(self):
        super().__init__(Platform.YOUTUBE)