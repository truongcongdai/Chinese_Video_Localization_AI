from .platform import Platform
from .ytdlp_downloader import YTDLPDownloader


class BilibiliDownloader(YTDLPDownloader):

    def __init__(self):
        super().__init__(Platform.BILIBILI)