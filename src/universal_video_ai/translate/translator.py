# src/universal_video_ai/translate/translator.py
from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Optional, Protocol

__all__ = [
    "Translator",
    "TranslatorConfig",
    "NoOpTranslator",
    "TranslatorFactory",
    "TranslationError",
]

_logger = logging.getLogger(__name__)


class TranslationError(Exception):
    """Raised when translation fails due to backend or input errors."""


@dataclass(frozen=True)
class TranslatorConfig:
    """
    Configuration for translator factories / implementations.

    Attributes:
        provider: short name of provider backend (e.g., "noop", "google", "deepl", ...)
        api_key: optional API key for remote providers
        src_lang: optional source language hint (e.g., "en")
        dest_lang: optional default destination language (e.g., "vi")
    """
    provider: str = "noop"
    api_key: Optional[str] = None
    src_lang: Optional[str] = None
    dest_lang: Optional[str] = None


class Translator(Protocol):
    """
    Translator interface.

    Implementations must provide `translate`.
    """

    def translate(self, text: str, src_lang: Optional[str] = None, dest_lang: Optional[str] = None) -> str:
        """
        Translate `text` from src_lang to dest_lang.

        :param text: input text to translate
        :param src_lang: optional source language hint (e.g., "en")
        :param dest_lang: target language code (e.g., "vi")
        :return: translated text
        :raises TranslationError: on failure
        """
        ...


class NoOpTranslator:
    """
    A trivial translator that returns input text unchanged.

    Useful as a safe default implementation and for tests.
    """

    def __init__(self, config: Optional[TranslatorConfig] = None, logger: Optional[logging.Logger] = None) -> None:
        self.config = config or TranslatorConfig()
        self.logger = logger or _logger
        self.logger.debug("NoOpTranslator initialized with config=%s", self.config)

    def translate(self, text: str, src_lang: Optional[str] = None, dest_lang: Optional[str] = None) -> str:
        """
        Return the input text unchanged.

        This method intentionally does minimal work so it is safe to call in pipelines
        where a real translator is not available yet.
        """
        if not isinstance(text, str):
            raise TranslationError("text must be a string")

        # If a default dest_lang is configured, we still return the original text.
        self.logger.debug(
            "NoOpTranslator.translate called (src=%s dest=%s) length=%d",
            src_lang or self.config.src_lang,
            dest_lang or self.config.dest_lang,
            len(text) if text is not None else 0,
        )
        return text


class TranslatorFactory:
    """
    Factory for creating translator implementations.

    Example:
        config = TranslatorConfig(provider="noop")
        translator = TranslatorFactory.create(config)
        translated = translator.translate("hello", dest_lang="vi")
    """

    @staticmethod
    def create(config: Optional[TranslatorConfig] = None, logger: Optional[logging.Logger] = None) -> Translator:
        """
        Create a Translator instance based on config.provider.

        Currently supported providers:
        - "noop" (default): returns input text unchanged.

        Future providers can be added without changing this public API.

        :param config: TranslatorConfig (if None, default config used)
        :param logger: optional logger
        :return: Translator implementation
        :raises ValueError: if provider is unknown
        """
        cfg = config or TranslatorConfig()
        log = logger or _logger
        provider = (cfg.provider or "noop").lower().strip()

        log.debug("TranslatorFactory.create provider=%s", provider)

        if provider == "noop":
            return NoOpTranslator(config=cfg, logger=log)

        # Placeholder: attempt dynamic backends here (optional). If not available, raise descriptive error.
        if provider == "google":
            try:
                # Try to import googletrans lazily
                from googletrans import Translator as GoogleTrans  # type: ignore
            except Exception as exc:
                raise ValueError(
                    "Google provider requested but googletrans is not available. Install googletrans or choose another provider."
                ) from exc

            class _GoogleTranslator:
                def __init__(self, cfg: TranslatorConfig, logger: logging.Logger) -> None:
                    self.cfg = cfg
                    self.logger = logger
                    self.client = GoogleTrans()

                def translate(self, text: str, src_lang: Optional[str] = None, dest_lang: Optional[str] = None) -> str:
                    dest = dest_lang or self.cfg.dest_lang or "en"
                    src = src_lang or self.cfg.src_lang or None
                    try:
                        res = self.client.translate(text, src=src, dest=dest)
                        return getattr(res, "text", str(res))
                    except Exception as exc:  # pragma: no cover - depends on external lib
                        raise TranslationError(f"Google translation failed: {exc}") from exc

            return _GoogleTranslator(cfg, log)

        if provider == "deepl":
            try:
                # Try to import deepl lazily
                import deepl  # type: ignore
            except Exception as exc:
                raise ValueError(
                    "DeepL provider requested but deepl is not available. Install deepl or choose another provider."
                ) from exc

            if not cfg.api_key:
                raise ValueError("DeepL provider requires api_key in TranslatorConfig")

            class _DeepLTranslator:
                def __init__(self, cfg: TranslatorConfig, logger: logging.Logger) -> None:
                    self.cfg = cfg
                    self.logger = logger
                    self.client = deepl.Translator(cfg.api_key)

                def translate(self, text: str, src_lang: Optional[str] = None, dest_lang: Optional[str] = None) -> str:
                    dest = dest_lang or self.cfg.dest_lang or "en-US"
                    src = src_lang or self.cfg.src_lang or None
                    try:
                        result = self.client.translate_text(
                            text,
                            source_lang=src,
                            target_lang=dest,
                        )
                        return result.text
                    except Exception as exc:  # pragma: no cover - depends on external lib
                        raise TranslationError(f"DeepL translation failed: {exc}") from exc

            return _DeepLTranslator(cfg, log)

        raise ValueError(f"Unknown translation provider: {cfg.provider!r}")