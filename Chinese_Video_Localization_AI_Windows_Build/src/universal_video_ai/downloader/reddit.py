from .platform import Platform
from .ytdlp_downloader import YTDLPDownloader


class RedditDownloader(YTDLPDownloader):

    def __init__(self):
        super().__init__(Platform.REDDIT)