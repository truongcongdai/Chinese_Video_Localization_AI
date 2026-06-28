# src/universal_video_ai/mixer/service.py
from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
from typing import Optional, List
import subprocess
import shutil

__all__ = ["MixerService", "MixerConfig", "AudioMix"]

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AudioMix:
    """Specification for mixing audio streams."""

    primary_audio: Path  # original audio
    secondary_audio: Optional[Path] = None  # TTS/translation
    mix_level: float = 0.7  # volume of primary (0-1), secondary gets 1-mix_level


@dataclass
class MixerConfig:
    """Configuration for mixer service."""

    output_format: str = "wav"
    sample_rate: int = 44100


class MixerService:
    """Service for mixing audio streams.

    Responsibilities:
    - Combine original audio with translated/TTS audio.
    - Adjust volume levels.
    - Output mixed audio.
    """

    def __init__(self, config: Optional[MixerConfig] = None, logger: Optional[logging.Logger] = None) -> None:
        self.config = config or MixerConfig()
        self.logger = logger or _logger
        self._ffmpeg_available = shutil.which("ffmpeg") is not None
        self.logger.debug("MixerService initialized output_format=%s sample_rate=%s ffmpeg=%s",
                          self.config.output_format, self.config.sample_rate, self._ffmpeg_available)

    def mix(self, mix_spec: AudioMix, output_path: Path) -> Path:
        """
        Mix audio streams and write output.

        If secondary_audio is None, simply returns the primary audio path.
        Otherwise, uses FFmpeg to mix the two with specified levels.

        :param mix_spec: AudioMix specification
        :param output_path: where to save mixed audio
        :return: output_path
        """
        output_path = Path(output_path).resolve()

        # If no secondary audio, just copy primary
        if mix_spec.secondary_audio is None:
            self.logger.info("MixerService.mix: no secondary audio, returning primary only")
            return mix_spec.primary_audio

        if not self._ffmpeg_available:
            self.logger.error("FFmpeg not available; cannot mix audio")
            raise RuntimeError("FFmpeg not available in PATH")

        self.logger.info("MixerService.mix: mixing %s + %s -> %s (mix_level=%.2f)",
                         mix_spec.primary_audio, mix_spec.secondary_audio, output_path, mix_spec.mix_level)

        # Build FFmpeg command to mix two audio streams
        # Filter: amix=inputs=2:duration=first (mix two inputs, use duration of first)
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "error",
            "-i", str(mix_spec.primary_audio),
            "-i", str(mix_spec.secondary_audio),
            "-filter_complex",
            f"[0:a]volume={mix_spec.mix_level}[a0];[1:a]volume={1 - mix_spec.mix_level}[a1];[a0][a1]amix=inputs=2:duration=first[out]",
            "-map", "[out]",
            "-ar", str(self.config.sample_rate),
            "-y",
            str(output_path),
        ]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=600)
            if result.returncode != 0:
                stderr = result.stderr or result.stdout or "unknown error"
                self.logger.error("FFmpeg mix failed: %s", stderr)
                raise RuntimeError(f"FFmpeg mix failed: {stderr}")
            self.logger.info("MixerService.mix: success")
            return output_path
        except subprocess.TimeoutExpired:
            self.logger.error("FFmpeg mix timed out")
            raise RuntimeError("FFmpeg mix timed out")
        except Exception as exc:
            self.logger.exception("Unexpected error during mix: %s", exc)
            raise RuntimeError(f"Audio mix failed: {exc}") from exc