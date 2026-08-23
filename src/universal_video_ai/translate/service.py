# src/universal_video_ai/translate/service.py
from __future__ import annotations

from typing import List, Optional
import asyncio
import logging
from dataclasses import dataclass

from .backend import TranslateBackend  # type: ignore
from .exceptions import TranslationBackendUnavailable, TranslationFailed  # type: ignore
from universal_video_ai.segment import TranscriptSegment

__all__ = ["TranslateService"]

_logger = logging.getLogger(__name__)


@dataclass
class TranslateService:
    """Service layer for translation operations."""

    backend: Optional[TranslateBackend] = None
    cache: Optional[object] = None
    logger: Optional[logging.Logger] = None
    max_concurrency: int = 5
    batch_checkpoint_size: int = 80

    def __post_init__(self) -> None:
        if self.logger is None:
            self.logger = _logger
        self.logger.debug("TranslateService initialized backend=%s",
                          type(self.backend).__name__ if self.backend else None)

    def _cache_key(self, source_lang: str, target_lang: str, text: str) -> str:
        provider = getattr(self.backend, "provider", type(self.backend).__name__)
        if self.cache is None:
            return "\0".join((str(provider), source_lang, target_lang, text))
        return self.cache.make_key(
            "translate-v2", str(provider), source_lang, target_lang, text
        )

    async def translate(self, text: str, source_lang: str, target_lang: str) -> str:
        """
        Translate text with caching.

        :raises TranslationBackendUnavailable: if no backend is configured.
        :raises TranslationFailed: if translation fails.
        """
        if self.backend is None:
            self.logger.error("TranslateService.translate: no backend configured")
            raise TranslationBackendUnavailable("No TranslateBackend configured")

        # Check cache first
        if self.cache:
            cache_key = self._cache_key(source_lang, target_lang, text)
            cached_result = self.cache.get(cache_key)
            if cached_result is not None:
                self.logger.debug("Translation cache HIT for %s->%s", source_lang, target_lang)
                return cached_result

        self.logger.info("TranslateService.translate: source=%s target=%s", source_lang, target_lang)
        try:
            result = await self.backend.translate(text, source_lang, target_lang)

            # Cache result
            if self.cache:
                cache_key = self._cache_key(source_lang, target_lang, text)
                self.cache.set(cache_key, result, ttl_seconds=86400 * 7)  # 7 days

            return result
        except TranslationFailed:
            raise
        except Exception as exc:
            self.logger.exception("Unexpected error in TranslateService: %s", exc)
            raise TranslationFailed("Translation failed", cause=exc) from exc

    async def translate_segments(
        self, segments: List[TranscriptSegment], source_lang: str, target_lang: str
    ) -> List[TranscriptSegment]:
        """
        Translate a list of timed segments sentence-by-sentence, preserving
        each segment's original start/end timestamps.

        This keeps the localized video's dialogue aligned with the original:
        instead of translating the whole transcript as one blob (losing the
        mapping between "what was said at 0-3s" and "what should be said at
        0-3s in the target language"), each sentence is translated
        independently and keeps its source timing. TTS and subtitle
        generation should consume the result of this method rather than a
        flat translated string.

        Segments are translated concurrently (each still goes through the
        same per-text cache as `translate()`), then re-assembled in order.

        :raises TranslationBackendUnavailable: if no backend is configured.
        :raises TranslationFailed: if any segment's translation fails.
        """
        if self.backend is None:
            self.logger.error("TranslateService.translate_segments: no backend configured")
            raise TranslationBackendUnavailable("No TranslateBackend configured")

        if not segments:
            return []

        self.logger.info(
            "TranslateService.translate_segments: %d segments source=%s target=%s",
            len(segments), source_lang, target_lang,
        )

        batch_method = getattr(self.backend, "translate_batch", None)
        if callable(batch_method):
            self.logger.info(
                "TranslateService: using batched translation for %d segments", len(segments)
            )
            translated_texts: list[Optional[str]] = [None] * len(segments)
            pending: dict[str, tuple[str, list[int]]] = {}
            cache_hits = 0

            for index, segment in enumerate(segments):
                key = self._cache_key(source_lang, target_lang, segment.text)
                cached = self.cache.get(key) if self.cache else None
                if isinstance(cached, str):
                    translated_texts[index] = cached
                    cache_hits += 1
                    continue
                if key not in pending:
                    pending[key] = (segment.text, [])
                pending[key][1].append(index)

            if cache_hits:
                self.logger.info(
                    "TranslateService: resumed %d/%d segment(s) from persistent cache",
                    cache_hits, len(segments),
                )

            pending_items = list(pending.items())
            checkpoint_size = max(1, int(self.batch_checkpoint_size or 1))
            for start in range(0, len(pending_items), checkpoint_size):
                checkpoint = pending_items[start:start + checkpoint_size]
                checkpoint_texts = [item[1][0] for item in checkpoint]
                checkpoint_results = await batch_method(
                    checkpoint_texts, source_lang, target_lang
                )
                if len(checkpoint_results) != len(checkpoint):
                    raise TranslationFailed(
                        "Translation backend returned "
                        f"{len(checkpoint_results)} results for {len(checkpoint)} segments"
                    )
                for (key, (_, indexes)), translated in zip(checkpoint, checkpoint_results):
                    for index in indexes:
                        translated_texts[index] = translated
                    if self.cache:
                        self.cache.set(key, translated, ttl_seconds=86400 * 30)
                self.logger.info(
                    "TranslateService: translation checkpoint %d/%d saved",
                    min(start + len(checkpoint), len(pending_items)),
                    len(pending_items),
                )

            if any(text is None for text in translated_texts):
                raise TranslationFailed("Translation backend left one or more segments untranslated")
            return [
                TranscriptSegment(start=seg.start, end=seg.end, text=translated_text)
                for seg, translated_text in zip(segments, translated_texts)
            ]

        # Remote translation clients have small connection pools and some are
        # not safe under hundreds of simultaneous requests. Keep useful
        # parallelism without flooding the provider.
        semaphore = asyncio.Semaphore(max(1, self.max_concurrency))

        async def translate_one(segment: TranscriptSegment) -> str:
            async with semaphore:
                return await self.translate(segment.text, source_lang, target_lang)

        translated_texts = await asyncio.gather(*(translate_one(seg) for seg in segments))

        return [
            TranscriptSegment(start=seg.start, end=seg.end, text=translated_text)
            for seg, translated_text in zip(segments, translated_texts)
        ]
