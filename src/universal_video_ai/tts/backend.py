# src/universal_video_ai/tts/backend.py
from __future__ import annotations

from pathlib import Path
from typing import Protocol, Optional
import logging

from .tts import EdgeTTS, voice_for_language  # type: ignore
from .exceptions import SynthesisError  # type: ignore

__all__ = ["TTSBackend", "EdgeTTSBackend"]

_logger = logging.getLogger(__name__)


class TTSBackend(Protocol):
    """Protocol for TTS backends."""

    def synthesize(self, text: str, output_path: Path, language: str = "en", voice: Optional[str] = None) -> Path:
        """Synthesize speech to audio file."""
        ...


class EdgeTTSBackend:
    """Adapter exposing TTSBackend API backed by EdgeTTS.

    Selects a voice matching the requested `language` for each call rather
    than always using a single hardcoded voice. Using a voice whose locale
    doesn't match the text's language (e.g. an English voice reading
    Vietnamese text) is the most common cause of edge-tts failing with
    `NoAudioReceived`, so this mapping matters for correctness as well as
    reliability.
    """

    def __init__(self, logger: Optional[logging.Logger] = None, default_voice: str = "en-US-JennyNeural") -> None:
        self.logger = logger or _logger
        from .tts import TTSConfig
        self.default_voice = default_voice
        config = TTSConfig(provider="edge", voice=default_voice)
        self._tts = EdgeTTS(config=config, logger=self.logger)

    def synthesize(self, text: str, output_path: Path, language: str = "en", voice: Optional[str] = None) -> Path:
        """Delegate to EdgeTTS and convert exceptions to SynthesisError.

        :param language: target language of `text` (e.g. "vi"). Used to pick
            a matching Edge voice unless `voice` is explicitly given.
        :param voice: explicit voice override; takes precedence over the
            language-based lookup.
        """
        preset = voice or voice_for_language(language, fallback=self.default_voice)
        parts = preset.split("|")
        effective_voice = parts[0]
        options = dict(part.split("=", 1) for part in parts[1:] if "=" in part)
        primary_language = (language or "").strip().lower().split("-")[0]
        if primary_language == "vi":
            options.setdefault("rate", "-6%")
            options.setdefault("pitch", "+2Hz")
        try:
            self.logger.debug(
                "EdgeTTSBackend.synthesize: output=%s text_len=%d language=%s voice=%s",
                output_path, len(text), language, effective_voice,
            )
            return self._tts.synthesize(
                text, output_path, voice=effective_voice,
                rate=options.get("rate"), pitch=options.get("pitch"),
            )
        except SynthesisError:
            raise
        except Exception as exc:
            self.logger.exception("EdgeTTSBackend failed: %s", exc)
            raise SynthesisError("TTS backend failed to synthesize", cause=exc) from exc
