# src/universal_video_ai/tts/exceptions.py
from __future__ import annotations

from typing import Optional
from universal_video_ai.exceptions import UniversalVideoAIError

__all__ = [
    "TTSError",
    "TTSServiceError",
    "TTSBackendUnavailable",
    "SynthesisError",
]


class TTSError(UniversalVideoAIError):
    """Base exception for TTS-related errors."""


class TTSServiceError(TTSError):
    """Raised when the TTS service is misconfigured."""


class TTSBackendUnavailable(TTSError):
    """Raised when no TTS backend is configured."""


class SynthesisError(TTSError):
    """Raised when speech synthesis fails."""

    def __init__(self, message: str, cause: Optional[Exception] = None) -> None:
        super().__init__(message)
        self.cause = cause