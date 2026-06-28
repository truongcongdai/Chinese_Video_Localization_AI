# src/universal_video_ai/translate/service.py
from __future__ import annotations

from typing import Optional
import logging
from dataclasses import dataclass

from .backend import TranslateBackend  # type: ignore
from .exceptions import TranslationBackendUnavailable, TranslationFailed  # type: ignore

__all__ = ["TranslateService"]

_logger = logging.getLogger(__name__)


@dataclass
class TranslateService:
    """Service layer for translation operations."""

    backend: Optional[TranslateBackend] = None
    logger: Optional[logging.Logger] = None

    def __post_init__(self) -> None:
        if self.logger is None:
            self.logger = _logger
        self.logger.debug("TranslateService initialized backend=%s", type(self.backend).__name__ if self.backend else None)

    def translate(self, text: str, source_lang: str, target_lang: str) -> str:
        """
        Translate text.

        :raises TranslationBackendUnavailable: if no backend is configured.
        :raises TranslationFailed: if translation fails.
        """
        if self.backend is None:
            self.logger.error("TranslateService.translate: no backend configured")
            raise TranslationBackendUnavailable("No TranslateBackend configured")

        self.logger.info("TranslateService.translate: source=%s target=%s", source_lang, target_lang)
        try:
            return self.backend.translate(text, source_lang, target_lang)
        except TranslationFailed:
            raise
        except Exception as exc:
            self.logger.exception("Unexpected error in TranslateService: %s", exc)
            raise TranslationFailed("Translation failed", cause=exc) from exc