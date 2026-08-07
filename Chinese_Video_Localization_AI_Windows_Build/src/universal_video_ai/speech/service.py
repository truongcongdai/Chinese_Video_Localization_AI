# src/universal_video_ai/speech/service.py
from __future__ import annotations

from pathlib import Path
from typing import List, Optional
import logging
from dataclasses import dataclass

from .backend import SpeechBackend  # type: ignore
from .exceptions import SpeechBackendUnavailable, TranscriptionError  # type: ignore
from universal_video_ai.segment import TranscriptSegment, UNKNOWN_TIMING

__all__ = ["SpeechService"]

_logger = logging.getLogger(__name__)


@dataclass
class SpeechService:
    """Service layer for speech operations.

    Responsibilities:
    - Expose a small, testable API to perform speech tasks.
    - Delegate to an injected SpeechBackend (DI).
    - Support caching for transcription results.
    """

    backend: Optional[SpeechBackend] = None
    cache: Optional[object] = None  # RedisCache
    logger: Optional[logging.Logger] = None

    def __post_init__(self) -> None:
        if self.logger is None:
            self.logger = _logger
        self.logger.debug("SpeechService initialized backend=%s", type(self.backend).__name__ if self.backend is not None else None)

    def transcribe(self, audio_path: Path, language: Optional[str] = None) -> str:
        """Transcribe `audio_path` using the configured backend with caching.

        :raises SpeechBackendUnavailable: if no backend is configured.
        :raises TranscriptionError: if transcription fails.
        """
        if self.backend is None:
            self.logger.error("SpeechService.transcribe: no backend configured")
            raise SpeechBackendUnavailable("No SpeechBackend configured in SpeechService")

        # Check cache first
        if self.cache:
            cache_key = self.cache.make_key("transcribe", str(audio_path), language or "auto")
            cached_result = self.cache.get(cache_key)
            if cached_result is not None:
                self.logger.debug("SpeechService transcribe cache HIT: audio=%s", audio_path)
                return cached_result

        self.logger.info("SpeechService.transcribe: audio=%s language=%s", audio_path, language)
        try:
            result = self.backend.transcribe(audio_path, language=language)

            # Cache result
            if self.cache:
                cache_key = self.cache.make_key("transcribe", str(audio_path), language or "auto")
                self.cache.set(cache_key, result, ttl_seconds=86400 * 7)  # 7 days

            return result
        except TranscriptionError:
            raise
        except Exception as exc:
            self.logger.exception("Unexpected error in SpeechService: %s", exc)
            raise TranscriptionError("Transcription failed", cause=exc) from exc

    def transcribe_segments(self, audio_path: Path, language: Optional[str] = None) -> List[TranscriptSegment]:
        """Transcribe `audio_path` and return per-sentence timed segments.

        This is what lets the rest of the pipeline (translation, TTS, subtitles,
        on-screen text cover) stay aligned with the original video's timing —
        instead of translating/dubbing one flat blob of text.

        If the configured backend does not implement `transcribe_segments`
        (e.g. NoOp/legacy backends used in tests), this method falls back to
        calling `backend.transcribe()` and wraps the result as a single
        segment with unknown timing (`end == UNKNOWN_TIMING`), so callers can
        still detect "no real per-sentence timing available" and fall back to
        even-split heuristics if they need to.

        :raises SpeechBackendUnavailable: if no backend is configured.
        :raises TranscriptionError: if transcription fails.
        """
        if self.backend is None:
            self.logger.error("SpeechService.transcribe_segments: no backend configured")
            raise SpeechBackendUnavailable("No SpeechBackend configured in SpeechService")

        cache_key = None
        if self.cache:
            cache_key = self.cache.make_key("transcribe_segments", str(audio_path), language or "auto")
            cached_result = self.cache.get(cache_key)
            if cached_result is not None:
                self.logger.debug("SpeechService transcribe_segments cache HIT: audio=%s", audio_path)
                return [TranscriptSegment(**seg) for seg in cached_result]

        self.logger.info("SpeechService.transcribe_segments: audio=%s language=%s", audio_path, language)
        try:
            if hasattr(self.backend, "transcribe_segments"):
                segments = self.backend.transcribe_segments(audio_path, language=language)
            else:
                self.logger.warning(
                    "SpeechBackend %s has no transcribe_segments(); falling back to transcribe() "
                    "with unknown per-sentence timing",
                    type(self.backend).__name__,
                )
                text = self.backend.transcribe(audio_path, language=language)
                segments = [TranscriptSegment(start=0.0, end=UNKNOWN_TIMING, text=text)] if text else []

            if self.cache and cache_key:
                serializable = [
                    {"start": s.start, "end": s.end, "text": s.text} for s in segments
                ]
                self.cache.set(cache_key, serializable, ttl_seconds=86400 * 7)  # 7 days

            return segments
        except TranscriptionError:
            raise
        except Exception as exc:
            self.logger.exception("Unexpected error in SpeechService.transcribe_segments: %s", exc)
            raise TranscriptionError("Segment transcription failed", cause=exc) from exc
