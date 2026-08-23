# src/universal_video_ai/translate/translator.py
from __future__ import annotations

from dataclasses import dataclass
import asyncio
from email.utils import parsedate_to_datetime
import logging
import os
import time
from typing import Optional, Protocol

__all__ = [
    "Translator",
    "TranslatorConfig",
    "NoOpTranslator",
    "TranslatorFactory",
    "TranslationError",
    "TranslationRateLimitError",
]

_logger = logging.getLogger(__name__)


class TranslationError(Exception):
    """Raised when translation fails due to backend or input errors."""


class TranslationRateLimitError(TranslationError):
    """Raised when a remote translation provider rejects requests by quota."""

    def __init__(
        self,
        provider: str,
        retry_after: Optional[float] = None,
        status_code: int = 429,
    ) -> None:
        self.provider = provider
        self.retry_after = retry_after
        self.status_code = status_code
        wait_hint = (
            f" Retry after about {max(1, round(retry_after))} seconds."
            if retry_after is not None
            else " Retry later or configure an API-backed translation provider."
        )
        super().__init__(
            f"{provider} translation rate limited or blocked by anti-bot "
            f"(HTTP {status_code}).{wait_hint}"
        )


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
                    self._request_lock = asyncio.Lock()
                    self._last_request_at = 0.0
                    self._minimum_request_interval = 1.0

                @staticmethod
                def _retry_after_seconds(response: object) -> Optional[float]:
                    header = getattr(response, "headers", {}).get("Retry-After")
                    if not header:
                        return None
                    try:
                        return max(0.0, float(header))
                    except (TypeError, ValueError):
                        try:
                            retry_at = parsedate_to_datetime(str(header))
                            return max(0.0, retry_at.timestamp() - time.time())
                        except (TypeError, ValueError, OverflowError):
                            return None

                @staticmethod
                def _is_antibot_redirect(response: object) -> bool:
                    if getattr(response, "status_code", None) not in (301, 302, 303, 307, 308):
                        return False
                    location = str(getattr(response, "headers", {}).get("Location", "")).lower()
                    return "google.com/sorry" in location or "/sorry/" in location

                async def _wait_for_request_slot(self) -> None:
                    elapsed = time.monotonic() - self._last_request_at
                    delay = self._minimum_request_interval - elapsed
                    if delay > 0:
                        await asyncio.sleep(delay)

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
                    async with self._request_lock:
                        await self._wait_for_request_slot()
                        # POST keeps subtitle text out of logs and avoids huge,
                        # percent-encoded query strings for CJK batch payloads.
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
                        self._last_request_at = time.monotonic()
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

                async def translate(self, text: str, src_lang: Optional[str] = None, dest_lang: Optional[str] = None) -> str:
                    import httpx

                    dest = dest_lang or self.cfg.dest_lang or "en"
                    src = src_lang or self.cfg.src_lang or None
                    last_error: Optional[Exception] = None
                    for attempt in range(3):
                        try:
                            return await self._translate_direct(text, src, dest)
                        except httpx.HTTPStatusError as exc:  # pragma: no cover - external service
                            last_error = exc
                            status = exc.response.status_code
                            if self._is_antibot_redirect(exc.response):
                                # Google's web endpoint redirects automated
                                # clients to a CAPTCHA page after a request
                                # quota is crossed. Following or immediately
                                # retrying that redirect cannot succeed.
                                raise TranslationRateLimitError(
                                    "Google",
                                    retry_after=3600.0,
                                    status_code=status,
                                ) from exc
                            if status == 429:
                                retry_after = self._retry_after_seconds(exc.response)
                                if attempt >= 2:
                                    raise TranslationRateLimitError("Google", retry_after) from exc
                                delay = retry_after if retry_after is not None else 5.0 * (3 ** attempt)
                                delay = min(60.0, max(1.0, delay))
                                self.logger.warning(
                                    "Google translation rate limited (attempt %d/3); waiting %.1fs",
                                    attempt + 1, delay,
                                )
                                await asyncio.sleep(delay)
                                continue

                            if status < 500 and status not in (408, 425):
                                raise TranslationError(
                                    f"Google translation rejected the request (HTTP {status})"
                                ) from exc
                            self.logger.warning(
                                "Google translation attempt %d/3 failed (HTTP %d)",
                                attempt + 1, status,
                            )
                            if attempt < 2:
                                await asyncio.sleep(0.5 * (2 ** attempt))
                        except (httpx.TimeoutException, httpx.NetworkError) as exc:  # pragma: no cover
                            last_error = exc
                            self.logger.warning(
                                "Google translation attempt %d/3 failed: %s",
                                attempt + 1, type(exc).__name__,
                            )
                            if attempt < 2:
                                await asyncio.sleep(0.5 * (2 ** attempt))
                        except TranslationError:
                            raise
                        except Exception as exc:  # pragma: no cover - external service
                            last_error = exc
                            self.logger.warning(
                                "Google translation attempt %d/3 failed: %s",
                                attempt + 1, type(exc).__name__,
                            )
                            if attempt < 2:
                                await asyncio.sleep(0.5 * (2 ** attempt))
                    raise TranslationError(
                        f"Google translation failed after 3 attempts ({type(last_error).__name__})"
                    ) from last_error

                async def translate_batch(
                    self, texts: list[str], src_lang: Optional[str] = None,
                    dest_lang: Optional[str] = None,
                ) -> list[str]:
                    """Translate many short segments with a few sequential requests.

                    Google's unofficial endpoint occasionally rewrites or drops
                    one separator even though the translation itself succeeds.
                    A separator mismatch must therefore degrade to smaller
                    requests, not fail an entire long-running localization job.
                    """
                    if not texts:
                        return []
                    separator = "\n[[[UVAI_SEG_BREAK]]]\n"
                    separator_token = "[[[UVAI_SEG_BREAK]]]"
                    try:
                        max_segments = max(
                            1, int(os.getenv("GOOGLE_TRANSLATION_BATCH_SEGMENTS", "80"))
                        )
                    except ValueError:
                        max_segments = 80
                    try:
                        max_chars = max(
                            500, int(os.getenv("GOOGLE_TRANSLATION_BATCH_CHARS", "4500"))
                        )
                    except ValueError:
                        max_chars = 4500
                    results: list[str] = []
                    batch: list[str] = []
                    batch_chars = 0

                    async def translate_group(group: list[str]) -> list[str]:
                        translated = await self.translate(
                            separator.join(group), src_lang, dest_lang
                        )
                        if len(group) == 1:
                            return [translated.strip()]

                        parts = translated.split(separator_token)
                        if len(parts) == len(group):
                            return [part.strip() for part in parts]

                        # Preserve ordering while recursively isolating the
                        # request whose marker Google modified. In the worst
                        # case this reaches safe one-segment requests; normally
                        # only one extra split is needed.
                        self.logger.warning(
                            "Google changed batch separators (%d/%d); retrying as smaller batches",
                            len(parts), len(group),
                        )
                        midpoint = len(group) // 2
                        left = await translate_group(group[:midpoint])
                        right = await translate_group(group[midpoint:])
                        return left + right

                    async def flush() -> None:
                        nonlocal batch, batch_chars
                        results.extend(await translate_group(batch))
                        batch = []
                        batch_chars = 0

                    for text in texts:
                        extra = len(text) + (len(separator) if batch else 0)
                        if batch and (len(batch) >= max_segments or batch_chars + extra > max_chars):
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
                    if src == "auto":
                        src = None
                    try:
                        result = await asyncio.to_thread(
                            self.client.translate_text,
                            text,
                            source_lang=src,
                            target_lang=dest,
                        )
                        return result.text
                    except Exception as exc:  # pragma: no cover - depends on external lib
                        raise TranslationError(f"DeepL translation failed: {exc}") from exc

                async def translate_batch(
                    self,
                    texts: list[str],
                    src_lang: Optional[str] = None,
                    dest_lang: Optional[str] = None,
                ) -> list[str]:
                    if not texts:
                        return []
                    dest = dest_lang or self.cfg.dest_lang or "en-US"
                    src = src_lang or self.cfg.src_lang or None
                    if src == "auto":
                        src = None
                    try:
                        results = await asyncio.to_thread(
                            self.client.translate_text,
                            texts,
                            source_lang=src,
                            target_lang=dest,
                        )
                        return [result.text for result in results]
                    except Exception as exc:  # pragma: no cover - depends on external lib
                        raise TranslationError(f"DeepL batch translation failed: {exc}") from exc

            return _DeepLTranslator(cfg, log)

        raise ValueError(f"Unknown translation provider: {cfg.provider!r}")
