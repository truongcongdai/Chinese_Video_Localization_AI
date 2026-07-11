# src/universal_video_ai/tts/service.py
from __future__ import annotations

from pathlib import Path
from typing import Optional
import logging
from dataclasses import dataclass

from .tts import TTS  # type: ignore
from .exceptions import TTSBackendUnavailable, SynthesisError  # type: ignore

__all__ = ["TTSService"]

_logger = logging.getLogger(__name__)


@dataclass
class TTSService:
    """Service layer for TTS operations."""

    backend: Optional[TTS] = None
    cache: Optional[object] = None  # RedisCache
    logger: Optional[logging.Logger] = None

    def __post_init__(self) -> None:
        if self.logger is None:
            self.logger = _logger
        self.logger.debug("TTSService initialized backend=%s", type(self.backend).__name__ if self.backend else None)

    def synthesize(
            self,
            text: str,
            language: str = "vi",
            voice: Optional[str] = None,
            output_path: Optional[Path] = None,
    ) -> Path:
        """
        Synthesize text to speech with caching.

        :raises TTSBackendUnavailable: if no backend is configured.
        :raises SynthesisError: if synthesis fails.
        """
        if self.backend is None:
            self.logger.error("TTSService.synthesize: no backend configured")
            raise TTSBackendUnavailable("No TTS backend configured")

        # Check cache first
        if self.cache:
            cache_key = self.cache.make_key("tts", language, voice or "default", text[:50])
            cached_path_str = self.cache.get(cache_key)
            if cached_path_str and Path(cached_path_str).exists():
                self.logger.debug("TTS cache HIT for %s voice=%s", language, voice)
                return Path(cached_path_str)

        self.logger.info("TTSService.synthesize: language=%s voice=%s", language, voice)
        try:
            # NOTE: language/voice must be forwarded to the backend — the
            # backend is what actually picks which TTS voice to speak with.
            # Previously these were computed for logging/caching only and
            # silently dropped here, so every synthesis request used
            # whatever voice the backend happened to default to regardless
            # of the requested target language.
            result = self.backend.synthesize(text, output_path=output_path, language=language, voice=voice)

            # Cache result
            if self.cache:
                cache_key = self.cache.make_key("tts", language, voice or "default", text[:50])
                self.cache.set(cache_key, str(result), ttl_seconds=86400 * 7)

            return result
        except SynthesisError:
            raise
        except Exception as exc:
            self.logger.exception("Unexpected error in TTSService: %s", exc)
            raise SynthesisError("Synthesis failed", cause=exc) from exc