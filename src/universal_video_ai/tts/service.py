# src/universal_video_ai/tts/service.py
from __future__ import annotations

from pathlib import Path
from typing import Optional
import logging
from dataclasses import dataclass

from .backend import TTSBackend  # type: ignore
from .exceptions import TTSBackendUnavailable, SynthesisError  # type: ignore

__all__ = ["TTSService"]

_logger = logging.getLogger(__name__)


@dataclass
class TTSService:
    """Service layer for text-to-speech operations."""

    backend: Optional[TTSBackend] = None
    logger: Optional[logging.Logger] = None

    def __post_init__(self) -> None:
        if self.logger is None:
            self.logger = _logger
        self.logger.debug("TTSService initialized backend=%s", type(self.backend).__name__ if self.backend else None)

    def synthesize(self, text: str, output_path: Path, language: str = "en") -> Path:
        """
        Synthesize text to speech.

        :param text: text to synthesize
        :param output_path: where to save audio
        :param language: language code (default "en")
        :raises TTSBackendUnavailable: if no backend is configured.
        :raises SynthesisError: if synthesis fails.
        :return: output_path
        """
        if self.backend is None:
            self.logger.error("TTSService.synthesize: no backend configured")
            raise TTSBackendUnavailable("No TTSBackend configured")

        output_path = Path(output_path).resolve()
        self.logger.info("TTSService.synthesize: language=%s output=%s", language, output_path)
        try:
            return self.backend.synthesize(text, output_path, language=language)
        except SynthesisError:
            raise
        except Exception as exc:
            self.logger.exception("Unexpected error in TTSService: %s", exc)
            raise SynthesisError("Speech synthesis failed", cause=exc) from exc