# src/universal_video_ai/speech/__init__.py
"""
Public API for the speech subsystem.

This module exposes the service layer and exceptions.
Backend implementations are available but not exported by default.
"""

from __future__ import annotations

from .service import SpeechService
from .exceptions import (
    SpeechError,
    SpeechServiceError,
    SpeechBackendUnavailable,
    TranscriptionError,
)

__all__ = [
    "SpeechService",
    "SpeechError",
    "SpeechServiceError",
    "SpeechBackendUnavailable",
    "TranscriptionError",
]