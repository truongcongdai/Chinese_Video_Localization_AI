# src/universal_video_ai/speech/backend.py
from __future__ import annotations

from pathlib import Path
from typing import Protocol, Optional
import logging

# WhisperTranscriber is a local wrapper that lazy-loads heavy deps inside its methods.
# We import it here only to provide a concrete adapter; this import is safe because
# WhisperTranscriber itself does not import heavy libs at module import time.
from .whisper import WhisperTranscriber, WhisperConfig  # type: ignore

__all__ = ["SpeechBackend", "WhisperBackend"]

_logger = logging.getLogger(__name__)


class SpeechBackend(Protocol):
    """Protocol for speech backends used by the SpeechService."""

    def transcribe(self, audio_path: Path, language: Optional[str] = None) -> str:
        """Return transcript text for the given audio file."""
        ...


class WhisperBackend:
    """Adapter exposing SpeechBackend API backed by `WhisperTranscriber`.

    This adapter isolates the Whisper-specific wrapper behind a backend interface
    so callers depend on SpeechBackend only.
    """

    def __init__(self, config: Optional[WhisperConfig] = None, logger: Optional[logging.Logger] = None) -> None:
        """
        :param config: configuration passed to the underlying WhisperTranscriber.
        :param logger: optional logger; if omitted a module logger is used.
        """
        self.logger = logger or _logger
        self._transcriber = WhisperTranscriber(config=config, logger=self.logger)

    def transcribe(self, audio_path: Path, language: Optional[str] = None) -> str:
        """Delegate transcription to the underlying WhisperTranscriber."""
        self.logger.debug("WhisperBackend.transcribe: audio=%s language=%s", audio_path, language)
        return self._transcriber.transcribe(audio_path, language=language)