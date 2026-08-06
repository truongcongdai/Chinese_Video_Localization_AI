from __future__ import annotations

from urllib.parse import urlparse
import re

from .platform import Platform


class PlatformDetector:
    """
    Detect which platform a URL belongs to.
    """

    DOMAIN_MAPPING = {

        "youtube.com": Platform.YOUTUBE,
        "youtu.be": Platform.YOUTUBE,

        "douyin.com": Platform.DOUYIN,
        "v.douyin.com": Platform.DOUYIN,

        "kuaishou.com": Platform.KUAISHOU,

        "tiktok.com": Platform.TIKTOK,
        "vm.tiktok.com": Platform.TIKTOK,
        "vt.tiktok.com": Platform.TIKTOK,

        "facebook.com": Platform.FACEBOOK,
        "fb.watch": Platform.FACEBOOK,

        "instagram.com": Platform.INSTAGRAM,

        "bilibili.com": Platform.BILIBILI,
        "b23.tv": Platform.BILIBILI,

        "reddit.com": Platform.REDDIT,
        "redd.it": Platform.REDDIT,
    }

    def detect(self, url: str) -> Platform:
        url_match = re.search(r"https?://[^\s<>'\"]+", url or "")
        if url_match:
            url = url_match.group(0).rstrip(".,;:!?)]}】》”’\"'")

        hostname = urlparse(url).hostname

        if not hostname:
            return Platform.GENERIC

        hostname = hostname.lower()

        for domain, platform in self.DOMAIN_MAPPING.items():

            if hostname == domain or hostname.endswith("." + domain):

                return platform

        return Platform.GENERIC