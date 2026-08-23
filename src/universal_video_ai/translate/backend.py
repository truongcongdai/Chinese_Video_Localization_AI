# src/universal_video_ai/translate/backend.py
from __future__ import annotations

from typing import Protocol, Optional
import logging
import os

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

    def __init__(
        self,
        logger: Optional[logging.Logger] = None,
        provider: Optional[str] = None,
        api_key: Optional[str] = None,
    ) -> None:
        self.logger = logger or _logger
        from .translator import TranslatorConfig, TranslatorFactory
        env_provider = (os.getenv("TRANSLATION_PROVIDER") or "").strip().lower()
        # TranslatorBackend is used only when real translation was requested.
        # Keep historical behavior for old .env files that still say "noop";
        # returning untranslated source text here would create a corrupt dub.
        selected_provider = (
            provider.strip().lower()
            if provider
            else env_provider if env_provider in {"google", "deepl"} else "google"
        )
        selected_api_key = api_key or os.getenv("TRANSLATION_API_KEY")
        if selected_provider == "deepl":
            selected_api_key = selected_api_key or os.getenv("DEEPL_API_KEY")
        config = TranslatorConfig(provider=selected_provider, api_key=selected_api_key)
        self._translator = TranslatorFactory.create(config, logger)
        self.provider = selected_provider
        self.logger.info("Translation provider selected: %s", selected_provider)

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
            raise TranslationFailed(f"Translation backend failed: {exc}", cause=exc) from exc

    async def translate_batch(
        self, texts: list[str], source_lang: str, target_lang: str
    ) -> list[str]:
        try:
            method = getattr(self._translator, "translate_batch", None)
            if callable(method):
                return await method(texts, source_lang, target_lang)
            return [
                await self._translator.translate(text, source_lang, target_lang)
                for text in texts
            ]
        except TranslationFailed:
            raise
        except Exception as exc:
            self.logger.exception("TranslatorBackend batch failed: %s", exc)
            raise TranslationFailed(f"Translation backend batch failed: {exc}", cause=exc) from exc
