# src/universal_video_ai/speech/backend.py
from __future__ import annotations

from pathlib import Path
from typing import Protocol, Optional
import logging

# Import the local whisper wrapper; it lazy-loads heavy deps inside methods.
from .whisper import WhisperTranscriber, WhisperConfig  # type: ignore

logger = logging.getLogger(__name__)


class SpeechBackend(Protocol):
    """Protocol that speech backends should implement."""

    def transcribe(self, audio_path: Path, language: Optional[str] = None) -> str:
        ...


class WhisperBackend:
    """Adapter that exposes a SpeechBackend API backed by the WhisperTranscriber.

    This adapter keeps the WhisperTranscriber usage behind the backend so callers
    only depend on the SpeechBackend protocol.
    """

    def __init__(self, config: Optional[WhisperConfig] = None, logger: Optional[logging.Logger] = None) -> None:
        self.logger = logger or logger or logging.getLogger(__name__)
        self._transcriber = WhisperTranscriber(config=config, logger=self.logger)

    def transcribe(self, audio_path: Path, language: Optional[str] = None) -> str:
        self.logger.debug("WhisperBackend: transcribe %s lang=%s", audio_path, language)
        return self._transcriber.transcribe(audio_path, language=language)