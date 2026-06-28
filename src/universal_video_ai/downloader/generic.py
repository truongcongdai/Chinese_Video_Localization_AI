from .platform import Platform
from .ytdlp_downloader import YTDLPDownloader


class GenericDownloader(YTDLPDownloader):

    def __init__(self):
        super().__init__(Platform.GENERIC)