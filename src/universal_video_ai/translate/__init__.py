# src/universal_video_ai/translate/__init__.py
"""
Public API for the translate subsystem.

Exposes the service layer and exceptions.
"""

from __future__ import annotations

from .service import TranslateService
from .exceptions import (
    TranslateError,
    TranslateServiceError,
    TranslationBackendUnavailable,
    TranslationFailed,
)

__all__ = [
    "TranslateService",
    "TranslateError",
    "TranslateServiceError",
    "TranslationBackendUnavailable",
    "TranslationFailed",
]