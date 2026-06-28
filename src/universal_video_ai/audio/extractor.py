# src/universal_video_ai/audio/extractor.py
from __future__ import annotations

import logging
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from universal_video_ai.config import TEMP_DIR

from .audio_result import AudioResult
from .exceptions import AudioExtractionError
from .ffprobe import FFprobe

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AudioConfig:
    """Configuration for audio extraction.

    Attributes:
        sample_rate: sample rate in Hz to request from ffmpeg (e.g. 44100)
        channels: number of audio channels (1 mono, 2 stereo)
        codec: audio codec name to use (pcm_s16le for high-quality WAV)
        output_format: container/extension (defaults to 'wav')
        ffmpeg_timeout: seconds to wait for ffmpeg to finish
    """
    sample_rate: int = 44100
    channels: int = 1
    codec: str = "pcm_s16le"
    output_format: str = "wav"
    ffmpeg_timeout: int = 300


class AudioExtractor:
    """Extract high-quality WAV audio from downloaded video files using ffmpeg."""

    def __init__(self, config: Optional[AudioConfig] = None, logger: Optional[logging.Logger] = None) -> None:
        """
        Initialize extractor.

        Args:
            config: AudioConfig to control sample rate, channels, codec.
            logger: optional logger; if None, module logger is used.
        """
        self.config = config or AudioConfig()
        self.logger = logger or _logger

        # available utilities
        self._ffmpeg_available = shutil.which("ffmpeg") is not None
        self._ffprobe = FFprobe()

        # default output dir under TEMP_DIR/audio
        self._default_dir = (TEMP_DIR / "audio").resolve()
        self._default_dir.mkdir(parents=True, exist_ok=True)

        self.logger.debug(
            "AudioExtractor initialized ffmpeg=%s ffprobe=%s default_dir=%s config=%s",
            self._ffmpeg_available, self._ffprobe.available, self._default_dir, self.config
        )

    def get_output_path(self, video_path: Path, output_dir: Optional[Path] = None) -> Path:
        """Compute output audio path based on video_path and output_dir."""
        video_path = video_path.resolve()
        base = (output_dir or self._default_dir).resolve()
        base.mkdir(parents=True, exist_ok=True)
        return base / f"{video_path.stem}.{self.config.output_format}"

    def _build_ffmpeg_command(self, video_path: Path, audio_path: Path) -> list[str]:
        """Build ffmpeg command list to extract high-quality audio."""
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "error",
            "-i", str(video_path),
            "-vn",
            "-acodec", self.config.codec,
            "-ar", str(self.config.sample_rate),
            "-ac", str(self.config.channels),
            "-y",
            str(audio_path),
        ]
        return cmd

    def extract(self, video_path: Path, output_dir: Optional[Path] = None) -> AudioResult:
        """Extract audio from `video_path` and return AudioResult.

        Raises:
            FileNotFoundError: if input missing or not a file.
            AudioExtractionError: on ffmpeg failure or validation error.
        """
        video_path = Path(video_path).resolve()

        if not video_path.exists():
            self.logger.error("Input video not found: %s", video_path)
            raise FileNotFoundError(f"Input video not found: {video_path}")
        if not video_path.is_file():
            self.logger.error("Input path is not a file: %s", video_path)
            raise FileNotFoundError(f"Input path is not a file: {video_path}")

        if not self._ffmpeg_available:
            self.logger.error("ffmpeg not available in PATH")
            raise AudioExtractionError("ffmpeg not available in PATH")

        audio_path = self.get_output_path(video_path, output_dir)

        cmd = self._build_ffmpeg_command(video_path, audio_path)
        self.logger.info("Extracting audio: %s -> %s", video_path, audio_path)
        self.logger.debug("ffmpeg cmd: %s", " ".join(cmd))

        start_ts = time.time()
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=self.config.ffmpeg_timeout)
        except FileNotFoundError as exc:
            self.logger.exception("ffmpeg not found at runtime: %s", exc)
            raise AudioExtractionError("ffmpeg not found at runtime", cause=exc) from exc
        except subprocess.TimeoutExpired as exc:
            self.logger.error("ffmpeg timed out for %s", video_path)
            raise AudioExtractionError("ffmpeg timed out", cause=exc) from exc

        if proc.returncode != 0:
            stderr = proc.stderr or proc.stdout or ""
            self.logger.error("ffmpeg extraction failed: %s", stderr)
            raise AudioExtractionError(f"ffmpeg extraction failed: {stderr}")

        # Validate output file: exists and > 0 bytes
        try:
            if not audio_path.exists():
                self.logger.error("ffmpeg completed but output missing: %s", audio_path)
                raise AudioExtractionError(f"ffmpeg did not produce output file: {audio_path}")

            filesize = int(audio_path.stat().st_size)
        except Exception as exc:
            self.logger.exception("Failed to stat output file: %s", exc)
            raise AudioExtractionError("Failed to stat output file", cause=exc) from exc

        if filesize <= 0:
            self.logger.error("Extracted audio file is empty: %s", audio_path)
            raise AudioExtractionError("Extracted audio file is empty")

        # Best-effort probe using ffprobe
        sample_rate = self.config.sample_rate
        channels = self.config.channels
        duration = 0.0
        bitrate = None
        fmt = self.config.output_format

        if self._ffprobe.available:
            try:
                probe = self._ffprobe.probe(audio_path)
                if probe is not None:
                    sample_rate = probe.sample_rate or sample_rate
                    channels = probe.channels or channels
                    duration = probe.duration or duration
                    bitrate = probe.bit_rate
                    fmt = probe.format_name or fmt
            except Exception as exc:
                # Do not fail extraction for ffprobe errors; log and continue with fallback values.
                self.logger.warning("ffprobe probing failed for %s: %s", audio_path, exc)

        elapsed = time.time() - start_ts
        self.logger.info("Audio extraction successful: %s (size=%d bytes, sample_rate=%d, channels=%d, duration=%.2fs) (ffmpeg took %.2fs)",
                         audio_path, filesize, sample_rate, channels, duration, elapsed)

        return AudioResult(
            success=True,
            audio_path=audio_path,
            duration=duration,
            sample_rate=sample_rate,
            channels=channels,
            bitrate=bitrate,
            format=fmt,
            filesize=filesize,
        )