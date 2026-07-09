# src/universal_video_ai/translate/backend.py
from __future__ import annotations

from typing import Protocol, Optional
import logging

from .translator import Translator  # type: ignore
from .exceptions import TranslationFailed  # type: ignore

__all__ = ["TranslateBackend", "TranslatorBackend"]

_logger = logging.getLogger(__name__)


class TranslateBackend(Protocol):
    """Protocol for translation backends."""

    async def translate(self, text: str, source_lang: str, target_lang: str) -> str:
        """Translate text from source to target language."""
        ...


class TranslatorBackend:
    """Adapter exposing TranslateBackend API backed by Translator."""

    def __init__(self, logger: Optional[logging.Logger] = None) -> None:
        self.logger = logger or _logger
        from .translator import TranslatorConfig, TranslatorFactory
        config = TranslatorConfig(provider="google")
        self._translator = TranslatorFactory.create(config, logger)

    async def translate(self, text: str, source_lang: str, target_lang: str) -> str:
        """Delegate to Translator and convert exceptions to TranslationFailed."""
        try:
            self.logger.debug("TranslatorBackend.translate: source=%s target=%s text_len=%d",
                            source_lang, target_lang, len(text))
            result = await self._translator.translate(text, source_lang, target_lang)
            return result
        except TranslationFailed:
            raise
        except Exception as exc:
            self.logger.exception("TranslatorBackend failed: %s", exc)
            raise TranslationFailed("Translation backend failed", cause=exc) from exc