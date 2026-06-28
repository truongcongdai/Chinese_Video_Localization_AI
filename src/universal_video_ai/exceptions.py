class UniversalVideoAIError(Exception):
    """Base exception."""


class DownloadError(UniversalVideoAIError):
    """Download failed."""


class UnsupportedPlatform(UniversalVideoAIError):
    """Platform is not supported."""