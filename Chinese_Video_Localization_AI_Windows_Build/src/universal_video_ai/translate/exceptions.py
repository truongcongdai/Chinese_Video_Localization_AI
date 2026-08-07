# src/universal_video_ai/translate/exceptions.py
from __future__ import annotations

from typing import Optional
from universal_video_ai.exceptions import UniversalVideoAIError

__all__ = [
    "TranslateError",
    "TranslateServiceError",
    "TranslationBackendUnavailable",
    "TranslationFailed",
]


class TranslateError(UniversalVideoAIError):
    """Base exception for translation-related errors."""


class TranslateServiceError(TranslateError):
    """Raised when the Translate service is misconfigured."""


class TranslationBackendUnavailable(TranslateError):
    """Raised when no translation backend is configured."""


class TranslationFailed(TranslateError):
    """Raised when translation fails."""

    def __init__(self, message: str, cause: Optional[Exception] = None) -> None:
        super().__init__(message)
        self.cause = cause