"""Bounded media probing with a validated PCM WAV fast path."""
from __future__ import annotations

import math
from pathlib import Path
import struct
import subprocess
import wave


def _pcm_duration(path: Path) -> float:
    """Trust frame counts only when RIFF and data chunks fit the actual file."""
    size = path.stat().st_size
    with path.open("rb") as stream:
        header = stream.read(12)
        if len(header) != 12 or header[:4] != b"RIFF" or header[8:] != b"WAVE":
            return 0.0
        riff_size = struct.unpack("<I", header[4:8])[0]
        if riff_size == 0xFFFFFFFF or riff_size + 8 > size:
            return 0.0
        limit = riff_size + 8
        while stream.tell() + 8 <= limit:
            chunk, length = struct.unpack("<4sI", stream.read(8))
            if length == 0xFFFFFFFF or stream.tell() + length > limit:
                return 0.0
            if chunk == b"data":
                with wave.open(str(path), "rb") as audio:
                    frame_bytes = audio.getnchannels() * audio.getsampwidth()
                    frames, rate = audio.getnframes(), audio.getframerate()
                    if rate and frames and frames * frame_bytes == length:
                        return frames / rate
                return 0.0
            stream.seek(length + length % 2, 1)
    return 0.0


def media_duration(path: Path) -> float:
    """Return seconds, or zero when neither a safe WAV header nor probing works."""
    try:
        duration = _pcm_duration(Path(path))
        if duration > 0:
            return duration
    except (OSError, EOFError, ValueError, struct.error, wave.Error):
        pass
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            check=True, capture_output=True, text=True, timeout=30,
        )
        duration = float(result.stdout.strip())
        return duration if math.isfinite(duration) and duration > 0 else 0.0
    except (OSError, ValueError, TypeError, subprocess.SubprocessError):
        return 0.0
