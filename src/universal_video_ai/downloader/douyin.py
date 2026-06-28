from .platform import Platform
from .ytdlp_downloader import YTDLPDownloader


class DouyinDownloader(YTDLPDownloader):

    def __init__(self):
        super().__init__(Platform.DOUYIN)

    def get_extra_options(self):

        return {

            # sẽ bổ sung cookie sau

        }