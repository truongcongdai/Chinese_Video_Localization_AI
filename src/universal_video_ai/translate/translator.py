# src/universal_video_ai/translate/translator.py
from __future__ import annotations

from dataclasses import dataclass
import asyncio
import email.utils
import logging
from datetime import datetime, timezone
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

    async def translate(self, text: str, src_lang: Optional[str] = None, dest_lang: Optional[str] = None) -> str:
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

    async def translate(self, text: str, src_lang: Optional[str] = None, dest_lang: Optional[str] = None) -> str:
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
                import httpx  # noqa: F401
            except Exception as exc:
                raise ValueError(
                    "Google provider requested but httpx is not available. Install httpx or choose another provider."
                ) from exc

            class _GoogleTranslator:
                def __init__(self, cfg: TranslatorConfig, logger: logging.Logger) -> None:
                    self.cfg = cfg
                    self.logger = logger
                    self._direct_client = None

                async def _translate_direct(self, text: str, src: Optional[str], dest: str) -> str:
                    """Use Google's lightweight JSON endpoint.

                    googletrans' web UI endpoint is comparatively fragile and
                    was repeatedly timing out even while Google's public
                    translate host remained reachable. This endpoint returns
                    the translated chunks as a small JSON response.
                    """
                    import httpx

                    if self._direct_client is None:
                        timeout = httpx.Timeout(30.0, connect=12.0)
                        limits = httpx.Limits(max_connections=10, max_keepalive_connections=5)
                        self._direct_client = httpx.AsyncClient(timeout=timeout, limits=limits)
                    # POST keeps long subtitle batches out of the URL.  The
                    # previous GET requests were several KB long and were
                    # much more likely to be throttled by Google/proxies.
                    response = await self._direct_client.post(
                        "https://translate.googleapis.com/translate_a/single",
                        data={
                            "client": "gtx",
                            "sl": src or "auto",
                            "tl": dest,
                            "dt": "t",
                            "q": text,
                        },
                    )
                    response.raise_for_status()
                    payload = response.json()
                    chunks = payload[0] if isinstance(payload, list) and payload else []
                    translated = "".join(
                        str(chunk[0]) for chunk in chunks
                        if isinstance(chunk, list) and chunk and chunk[0] is not None
                    )
                    if not translated:
                        raise TranslationError("Google returned an empty translation")
                    return translated

                @staticmethod
                def _retry_delay(exc: Exception, attempt: int) -> float:
                    """Return a provider-friendly delay, honoring Retry-After."""
                    response = getattr(exc, "response", None)
                    value = response.headers.get("Retry-After") if response is not None else None
                    if value:
                        try:
                            return max(0.0, min(float(value), 60.0))
                        except (TypeError, ValueError, OverflowError):
                            try:
                                retry_at = email.utils.parsedate_to_datetime(value)
                                if retry_at.tzinfo is None:
                                    retry_at = retry_at.replace(tzinfo=timezone.utc)
                                return max(0.0, min((retry_at - datetime.now(timezone.utc)).total_seconds(), 60.0))
                            except (TypeError, ValueError, OverflowError):
                                pass
                    # A sub-second retry loop only reinforces HTTP 429.  Use
                    # a bounded exponential delay for all transient failures.
                    return min(2.0 * (2 ** attempt), 30.0)

                async def translate(self, text: str, src_lang: Optional[str] = None, dest_lang: Optional[str] = None) -> str:
                    dest = dest_lang or self.cfg.dest_lang or "en"
                    src = src_lang or self.cfg.src_lang or None
                    last_error: Optional[Exception] = None
                    attempts = 4
                    for attempt in range(attempts):
                        try:
                            return await self._translate_direct(text, src, dest)
                        except Exception as exc:  # pragma: no cover - external service
                            last_error = exc
                            self.logger.warning(
                                "Google translation attempt %d/%d failed: %s",
                                attempt + 1, attempts, type(exc).__name__,
                            )
                            if attempt < attempts - 1:
                                await asyncio.sleep(self._retry_delay(exc, attempt))
                    raise TranslationError(
                        f"Google translation failed after {attempts} attempts: {last_error}"
                    ) from last_error

                async def translate_batch(
                    self, texts: list[str], src_lang: Optional[str] = None,
                    dest_lang: Optional[str] = None,
                ) -> list[str]:
                    """Translate many short segments with a few sequential requests."""
                    if not texts:
                        return []
                    separator = "\n[[[UVAI_SEG_BREAK]]]\n"
                    results: list[str] = []
                    batch: list[str] = []
                    batch_chars = 0

                    async def flush() -> None:
                        nonlocal batch, batch_chars
                        translated = await self.translate(
                            separator.join(batch), src_lang, dest_lang
                        )
                        parts = translated.split("[[[UVAI_SEG_BREAK]]]")
                        if len(parts) != len(batch):
                            raise TranslationError(
                                f"Google changed batch separators ({len(parts)}/{len(batch)})"
                            )
                        results.extend(part.strip() for part in parts)
                        batch = []
                        batch_chars = 0

                    for text in texts:
                        extra = len(text) + (len(separator) if batch else 0)
                        # Conservative batches are less likely to trip the
                        # unauthenticated endpoint's URL/content heuristics.
                        if batch and (len(batch) >= 10 or batch_chars + extra > 1000):
                            await flush()
                        batch.append(text)
                        batch_chars += extra
                    if batch:
                        await flush()
                    return results

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

                async def translate(self, text: str, src_lang: Optional[str] = None, dest_lang: Optional[str] = None) -> str:
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
