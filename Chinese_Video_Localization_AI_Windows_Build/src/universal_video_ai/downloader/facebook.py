from .platform import Platform
from .ytdlp_downloader import YTDLPDownloader


class FacebookDownloader(YTDLPDownloader):

    def __init__(self):
        super().__init__(Platform.FACEBOOK)

    def get_extra_options(self):

        return {

            # cookie sau

        }