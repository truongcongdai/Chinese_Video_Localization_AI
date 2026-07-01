# src/universal_video_ai/tts/__init__.py
"""
Public API for the TTS subsystem.

Exposes the service layer and exceptions.
"""

from __future__ import annotations

from .service import TTSService
from .exceptions import (
    TTSError,
    TTSServiceError,
    TTSBackendUnavailable,
    SynthesisError,
)
from .tts import (
    TTS,
    TTSConfig,
    TTSFactory,
    NoOpTTS,
    EdgeTTS,
)

__all__ = [
    "TTSService",
    "TTSError",
    "TTSServiceError",
    "TTSBackendUnavailable",
    "SynthesisError",
]