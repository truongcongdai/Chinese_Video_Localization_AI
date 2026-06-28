from enum import Enum


class Platform(str, Enum):
    UNKNOWN = "unknown"

    YOUTUBE = "youtube"
    TIKTOK = "tiktok"
    DOUYIN = "douyin"
    KUAISHOU = "kuaishou"

    FACEBOOK = "facebook"
    INSTAGRAM = "instagram"

    BILIBILI = "bilibili"
    REDDIT = "reddit"
    TWITTER = "twitter"
    GENERIC = "generic"

    VIMEO = "vimeo"

    OTHER = "other"