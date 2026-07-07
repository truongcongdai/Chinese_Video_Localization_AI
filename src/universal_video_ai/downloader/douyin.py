from .platform import Platform
from .ytdlp_downloader import YTDLPDownloader


class DouyinDownloader(YTDLPDownloader):

    def __init__(self):
        super().__init__(Platform.DOUYIN)

    def get_extra_options(self):
        return {
            "http_headers": {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            },
            "extractor_args": {
                "douyin": {
                    "web": ["api", "web"],
                }
            },
            "nocheckcertificate": True,
        }