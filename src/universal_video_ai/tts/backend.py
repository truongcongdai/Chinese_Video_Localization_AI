# src/universal_video_ai/tts/backend.py
from __future__ import annotations

from pathlib import Path
from typing import Protocol, Optional
import logging

from .tts import EdgeTTS  # type: ignore
from .exceptions import SynthesisError  # type: ignore

__all__ = ["TTSBackend", "EdgeTTSBackend"]

_logger = logging.getLogger(__name__)


class TTSBackend(Protocol):
    """Protocol for TTS backends."""

    def synthesize(self, text: str, output_path: Path) -> Path:
        """Synthesize speech to audio file."""
        ...


class EdgeTTSBackend:
    """Adapter exposing TTSBackend API backed by EdgeTTS."""

    def __init__(self, logger: Optional[logging.Logger] = None) -> None:
        self.logger = logger or _logger
        from .tts import TTSConfig
        config = TTSConfig(provider="edge", voice="en-US-JennyNeural")
        self._tts = EdgeTTS(config=config)

    def synthesize(self, text: str, output_path: Path) -> Path:
        """Delegate to EdgeTTS and convert exceptions to SynthesisError."""
        try:
            self.logger.debug("EdgeTTSBackend.synthesize: output=%s text_len=%d",
                            output_path, len(text))
            # EdgeTTS uses voice configuration, not language parameter
            return self._tts.synthesize(text, output_path)
        except SynthesisError:
            raise
        except Exception as exc:
            self.logger.exception("EdgeTTSBackend failed: %s", exc)
            raise SynthesisError("TTS backend failed to synthesize", cause=exc) from exc