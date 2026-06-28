# src/universal_video_ai/speech/exceptions.py
from __future__ import annotations

from typing import Optional
from universal_video_ai.exceptions import UniversalVideoAIError

__all__ = [
    "SpeechError",
    "SpeechServiceError",
    "SpeechBackendUnavailable",
    "TranscriptionError",
]


class SpeechError(UniversalVideoAIError):
    """Base exception for speech-related errors."""


class SpeechServiceError(SpeechError):
    """Raised when the Speech service is misconfigured or unavailable."""


class SpeechBackendUnavailable(SpeechError):
    """Raised when no speech backend is configured in the service."""


class TranscriptionError(SpeechError):
    """Raised when transcription fails."""

    def __init__(self, message: str, cause: Optional[Exception] = None) -> None:
        super().__init__(message)
        self.cause = cause