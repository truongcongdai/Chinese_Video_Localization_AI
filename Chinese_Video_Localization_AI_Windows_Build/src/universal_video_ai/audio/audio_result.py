# src/universal_video_ai/audio/audio_result.py
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class AudioResult:
    """Result of an audio extraction operation.

    Attributes:
        success: True if extraction succeeded
        audio_path: Path to the produced audio file (WAV)
        duration: duration in seconds (0.0 if unknown)
        sample_rate: sample rate in Hz
        channels: number of channels
        bitrate: bitrate in bits/sec if known else None
        format: container/codec format string (e.g. "wav")
        filesize: extracted file size in bytes
    """

    success: bool
    audio_path: Path
    duration: float
    sample_rate: int
    channels: int
    bitrate: Optional[int]
    format: str
    filesize: int