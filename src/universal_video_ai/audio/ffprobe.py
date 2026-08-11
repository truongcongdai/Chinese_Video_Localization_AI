# src/universal_video_ai/audio/ffprobe.py
from __future__ import annotations

import json
import logging
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Dict, Any

from .exceptions import FFprobeError

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FFprobeResult:
    """Structured ffprobe result (partial)."""
    duration: float
    sample_rate: int
    channels: int
    bit_rate: Optional[int]
    format_name: str


class FFprobe:
    """Helper wrapper around ffprobe binary to extract audio metadata.

    This class is best-effort: if ffprobe is missing or fails, methods return None
    or raise only FFprobeError depending on context. Callers should be robust.

    Usage:
        probe = FFprobe()
        info = probe.probe(path)
        if info:
            # use info.sample_rate, etc.
    """

    def __init__(self) -> None:
        self._available = shutil.which("ffprobe") is not None
        _logger.debug("FFprobe available=%s", self._available)

    @property
    def available(self) -> bool:
        """Return True if ffprobe is available in PATH."""
        return self._available

    def probe(self, audio_path: Path) -> Optional[FFprobeResult]:
        """Probe the audio file and return structured metadata.

        Returns None if ffprobe is unavailable or probing fails in a non-fatal way.
        Raises FFprobeError only when parsing fails unexpectedly.
        """
        if not self._available:
            _logger.debug("ffprobe not available; probe skipped for %s", audio_path)
            return None

        cmd = [
            "ffprobe",
            "-v", "error",
            "-show_entries", "format=duration:stream=sample_rate,channels,bit_rate",
            "-of", "json",
            str(audio_path),
        ]
        _logger.debug("Running ffprobe: %s", " ".join(cmd))
        try:
            # Read bytes explicitly. Windows otherwise decodes subprocess
            # output with the active ANSI code page, which can crash the
            # reader thread when FFmpeg includes a UTF-8 path in diagnostics.
            proc = subprocess.run(cmd, capture_output=True, text=False, check=False, timeout=30)
        except FileNotFoundError as exc:
            _logger.warning("ffprobe not found at runtime: %s", exc)
            return None
        except subprocess.TimeoutExpired as exc:
            _logger.warning("ffprobe timed out for %s: %s", audio_path, exc)
            return None
        except Exception as exc:
            _logger.exception("Unexpected error running ffprobe for %s: %s", audio_path, exc)
            return None

        stdout = (
            proc.stdout.decode("utf-8", errors="replace")
            if isinstance(proc.stdout, bytes)
            else str(proc.stdout or "")
        )
        stderr = (
            proc.stderr.decode("utf-8", errors="replace")
            if isinstance(proc.stderr, bytes)
            else str(proc.stderr or "")
        )

        if proc.returncode != 0:
            _logger.warning("ffprobe returned non-zero for %s: %s", audio_path, stderr or stdout)
            return None

        try:
            payload: Dict[str, Any] = json.loads(stdout or "{}")
        except Exception as exc:
            _logger.exception("Failed to parse ffprobe output for %s: %s", audio_path, exc)
            raise FFprobeError("Failed to parse ffprobe output", exc)

        # safe extraction with sensible fallbacks
        streams = payload.get("streams") or []
        format_obj = payload.get("format") or {}

        duration = float(format_obj.get("duration") or 0.0)
        # pick first stream as audio stream candidate
        sample_rate = None
        channels = None
        bit_rate = None
        if streams:
            s0 = streams[0]
            try:
                sample_rate = int(s0.get("sample_rate")) if s0.get("sample_rate") else None
            except Exception:
                sample_rate = None
            try:
                channels = int(s0.get("channels")) if s0.get("channels") else None
            except Exception:
                channels = None
            try:
                bit_rate = int(s0.get("bit_rate") or format_obj.get("bit_rate")) if (s0.get("bit_rate") or format_obj.get("bit_rate")) else None
            except Exception:
                bit_rate = None

        # fallback to format bit_rate if needed
        format_name = format_obj.get("format_name") or ""

        # Choose sensible default fallbacks
        sr = sample_rate if sample_rate is not None else 0
        ch = channels if channels is not None else 0

        return FFprobeResult(
            duration=duration,
            sample_rate=sr,
            channels=ch,
            bit_rate=bit_rate,
            format_name=format_name,
        )
