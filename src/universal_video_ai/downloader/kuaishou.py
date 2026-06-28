from .platform import Platform
from .ytdlp_downloader import YTDLPDownloader


class KuaishouDownloader(YTDLPDownloader):

    def __init__(self):
        super().__init__(Platform.KUAISHOU)