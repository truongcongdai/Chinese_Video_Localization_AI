# src/universal_video_ai/audio/__init__.py
"""
Public API for the audio subsystem.

Exposes extraction, separation, and a factory for easy pipeline creation.
"""

from __future__ import annotations

from .audio_result import AudioResult
from .extractor import AudioExtractor, AudioConfig
from .pipeline import AudioPipeline, AudioPipelineConfig, AudioPipelineResult
from .factory import create_audio_pipeline
from .demucs import DemucsProcessor, DemucsConfig, DemucsOutput, DEMUCS_AVAILABLE
from .background_music import BackgroundMusicConfig, BackgroundMusicLibrary

__all__ = [
    "AudioResult",
    "AudioExtractor",
    "AudioConfig",
    "AudioPipeline",
    "AudioPipelineConfig",
    "AudioPipelineResult",
    "create_audio_pipeline",
    "DemucsProcessor",
    "DemucsConfig",
    "DemucsOutput",
    "DEMUCS_AVAILABLE",
    "BackgroundMusicConfig",
    "BackgroundMusicLibrary",
]
