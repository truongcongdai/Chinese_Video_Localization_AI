# src/universal_video_ai/audio/exceptions.py
"""
Exceptions used by audio extraction module.
"""

from __future__ import annotations

from typing import Optional


class AudioExtractionError(RuntimeError):
    """Raised when audio extraction via ffmpeg fails.

    Attributes:
        message: human friendly error message
        cause: optional underlying exception
    """

    def __init__(self, message: str, cause: Optional[Exception] = None) -> None:
        super().__init__(message)
        self.cause = cause


class FFprobeError(RuntimeError):
    """Raised when ffprobe probing fails in an unexpected way."""

    def __init__(self, message: str, cause: Optional[Exception] = None) -> None:
        super().__init__(message)
        self.cause = cause