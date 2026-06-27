# src/universal_video_ai/speech/service.py
from __future__ import annotations

from pathlib import Path
from typing import Optional
import logging

from .backend import SpeechBackend  # type: ignore

logger = logging.getLogger(__name__)


class SpeechService:
    """Service layer for speech operations.

    The service delegates to an injected SpeechBackend. It intentionally does not
    construct backends itself (DI-first) to avoid heavy import-time side effects.
    """

    def __init__(self, backend: Optional[SpeechBackend] = None, logger: Optional[logging.Logger] = None) -> None:
        self.backend = backend
        self.logger = logger or logger or logging.getLogger(__name__)
        self.logger.debug("SpeechService initialized backend=%s", type(self.backend).__name__ if self.backend is not None else None)

    def transcribe(self, audio_path: Path, language: Optional[str] = None) -> str:
        """Transcribe audio using the configured backend.

        Raises:
            RuntimeError: if no backend is configured.
        """
        if self.backend is None:
            raise RuntimeError("No SpeechBackend configured for SpeechService")
        self.logger.info("SpeechService: transcribing %s (lang=%s)", audio_path, language)
        return self.backend.transcribe(audio_path, language=language)