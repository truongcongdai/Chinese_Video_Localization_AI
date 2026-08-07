# src/universal_video_ai/render/quality.py
"""
Video quality presets for rendering.
"""

from __future__ import annotations

from enum import Enum
from dataclasses import dataclass

__all__ = ["VideoQuality", "QualityPreset"]


class VideoQuality(Enum):
    """Video quality presets."""
    LOW = "480p"
    MEDIUM = "720p"
    HIGH = "1080p"


@dataclass(frozen=True)
class QualityPreset:
    """Quality preset configuration."""
    quality: VideoQuality
    bitrate_kbps: int  # Video bitrate in kbps
    fps: int  # Frames per second
    max_height: int  # Maximum height in pixels

    @staticmethod
    def get_preset(quality: VideoQuality) -> QualityPreset:
        """Get preset for a quality level."""
        presets = {
            VideoQuality.LOW: QualityPreset(
                quality=VideoQuality.LOW,
                bitrate_kbps=800,
                fps=24,
                max_height=480,
            ),
            VideoQuality.MEDIUM: QualityPreset(
                quality=VideoQuality.MEDIUM,
                bitrate_kbps=2500,
                fps=30,
                max_height=720,
            ),
            VideoQuality.HIGH: QualityPreset(
                quality=VideoQuality.HIGH,
                bitrate_kbps=6000,
                fps=30,
                max_height=1080,
            ),
        }
        return presets.get(quality, presets[VideoQuality.MEDIUM])