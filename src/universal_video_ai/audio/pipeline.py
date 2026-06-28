# src/universal_video_ai/audio/pipeline.py
from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
from typing import Optional

from universal_video_ai.downloader.download_result import DownloadResult
from .audio_result import AudioResult
from .demucs import DemucsOutput
from .extractor import AudioExtractor
from .demucs import DEMUCS_AVAILABLE

# depend on service layer (DI)
from universal_video_ai.speech.service import SpeechService  # type: ignore

__all__ = ["AudioPipelineConfig", "AudioPipelineResult", "AudioPipeline"]

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AudioPipelineConfig:
    run_demucs: bool = False
    demucs_output_dir: Optional[Path] = None
    run_transcription: bool = False
    transcription_language: Optional[str] = None


@dataclass(frozen=True)
class AudioPipelineResult:
    audio_result: AudioResult
    demucs_output: Optional[DemucsOutput] = None
    transcript: Optional[str] = None


class AudioPipeline:
    """Small orchestrator for audio extraction -> optional demucs -> optional transcription.

    Notes:
    - Accepts dependencies via DI (extractor, demucs_processor, speech_service).
    - Does not construct heavy backends itself.
    """

    def __init__(
        self,
        config: Optional[AudioPipelineConfig] = None,
        extractor: Optional[AudioExtractor] = None,
        demucs_processor: Optional[object] = None,
        speech_service: Optional[SpeechService] = None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.config = config or AudioPipelineConfig()
        self.extractor = extractor or AudioExtractor()
        self.demucs_processor = demucs_processor
        self.speech_service = speech_service
        self.logger = logger or _logger

        self.logger.debug(
            "AudioPipeline initialized run_demucs=%s demucs_processor=%s run_transcription=%s speech_service=%s",
            self.config.run_demucs,
            type(self.demucs_processor).__name__ if self.demucs_processor is not None else None,
            self.config.run_transcription,
            type(self.speech_service).__name__ if self.speech_service is not None else None,
        )

    def process(self, download_result: DownloadResult, output_dir: Optional[Path] = None) -> AudioPipelineResult:
        if not download_result.success:
            raise ValueError("Cannot process audio for unsuccessful download_result")

        video_path = download_result.video_path
        self.logger.info("AudioPipeline.process: video=%s", video_path)

        # Extract audio
        audio_result = self.extractor.extract(video_path, output_dir=output_dir)

        demucs_output: Optional[DemucsOutput] = None
        transcript: Optional[str] = None

        # Demucs step (optional)
        if self.config.run_demucs:
            if not DEMUCS_AVAILABLE and self.demucs_processor is None:
                raise RuntimeError("Demucs requested but not available and no demucs_processor injected")
            if self.demucs_processor is None:
                raise RuntimeError("Demucs requested but no demucs_processor was provided")
            self.logger.info("AudioPipeline: running demucs for %s", audio_result.audio_path)
            demucs_output = self.demucs_processor.separate(audio_result.audio_path, output_dir=self.config.demucs_output_dir)
            self.logger.debug("AudioPipeline: demucs_output=%s", demucs_output)

        # Transcription step (optional) via SpeechService
        if self.config.run_transcription:
            if self.speech_service is None:
                raise RuntimeError("Transcription requested but no SpeechService was injected")
            self.logger.info("AudioPipeline: running transcription for %s (lang=%s)",
                             audio_result.audio_path, self.config.transcription_language)
            transcript = self.speech_service.transcribe(audio_result.audio_path, language=self.config.transcription_language)
            self.logger.debug("AudioPipeline: transcript length=%d", len(transcript) if transcript else 0)

        return AudioPipelineResult(audio_result=audio_result, demucs_output=demucs_output, transcript=transcript)