# src/universal_video_ai/social/__init__.py
from .base import SocialUploader, SocialUploadResult
from .tiktok import TikTokUploader
from .facebook import FacebookUploader
from .youtube import YouTubeUploader

__all__ = [
    "SocialUploader", "SocialUploadResult",
    "TikTokUploader", "FacebookUploader", "YouTubeUploader",
    "get_uploader",
]

_UPLOADERS = {
    "tiktok": TikTokUploader,
    "facebook": FacebookUploader,
    "youtube": YouTubeUploader,
}


def get_uploader(platform: str) -> SocialUploader:
    key = platform.strip().lower()
    if key not in _UPLOADERS:
        raise ValueError(f"Unknown platform: {platform!r}. Options: {list(_UPLOADERS)}")
    return _UPLOADERS[key]()
