# src/universal_video_ai/speech/service.py
from __future__ import annotations

from pathlib import Path
from typing import Optional
import logging
from dataclasses import dataclass

from .backend import SpeechBackend  # type: ignore

__all__ = ["SpeechService"]

_logger = logging.getLogger(__name__)


@dataclass
class SpeechService:
    """Service layer for speech operations.

    Responsibilities:
    - Expose a small, testable API to perform speech tasks.
    - Delegate to an injected SpeechBackend (DI).
    """

    backend: Optional[SpeechBackend] = None
    logger: Optional[logging.Logger] = None

    def __post_init__(self) -> None:
        if self.logger is None:
            self.logger = _logger
        self.logger.debug("SpeechService initialized backend=%s", type(self.backend).__name__ if self.backend is not None else None)

    def transcribe(self, audio_path: Path, language: Optional[str] = None) -> str:
        """Transcribe `audio_path` using the configured backend.

        :raises RuntimeError: if no backend is configured.
        """
        if self.backend is None:
            raise RuntimeError("No SpeechBackend configured in SpeechService")
        self.logger.info("SpeechService.transcribe: audio=%s language=%s", audio_path, language)
        return self.backend.transcribe(audio_path, language=language)