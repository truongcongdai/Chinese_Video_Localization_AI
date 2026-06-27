from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
from typing import Optional, Protocol

from universal_video_ai.downloader.download_result import DownloadResult
from .audio_result import AudioResult
from .demucs import DemucsOutput
from .extractor import AudioExtractor
from . import DEMUCS_AVAILABLE

_module_logger = logging.getLogger(__name__)


class _DemucsLike(Protocol):
    """Protocol representing the Demucs processor interface we depend on."""

    def separate(self, audio_path: Path, output_dir: Optional[Path] = None) -> DemucsOutput:
        ...


class _TranscriberLike(Protocol):
    """Protocol representing a transcription backend."""

    def transcribe(self, audio_path: Path, language: Optional[str] = None) -> str:
        ...


@dataclass(frozen=True)
class AudioPipelineConfig:
    """Configuration for the audio pipeline.

    Attributes:
        run_demucs: whether to run Demucs separation after extraction.
        demucs_output_dir: optional base directory for demucs outputs. If None, Demucs decides defaults.
        run_transcription: whether to run speech transcription after extraction.
        transcription_language: optional language code to pass to the transcriber.
    """
    run_demucs: bool = False
    demucs_output_dir: Optional[Path] = None
    run_transcription: bool = False
    transcription_language: Optional[str] = None


@dataclass(frozen=True)
class AudioPipelineResult:
    """Aggregate result returned by AudioPipeline."""
    audio_result: AudioResult
    demucs_output: Optional[DemucsOutput] = None
    transcript: Optional[str] = None


class AudioPipeline:
    """Orchestrator that extracts audio from a downloaded video and optionally separates stems and transcribes.

    Design notes:
    - Keeps responsibilities small: orchestrate existing components, do not replace them.
    - Uses dependency injection for testability (pass extractor, demucs_processor, transcriber).
    """

    def __init__(
        self,
        config: Optional[AudioPipelineConfig] = None,
        extractor: Optional[AudioExtractor] = None,
        demucs_processor: Optional[_DemucsLike] = None,
        transcriber: Optional[_TranscriberLike] = None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.config = config or AudioPipelineConfig()
        self.extractor = extractor or AudioExtractor()
        self.demucs_processor = demucs_processor
        self.transcriber = transcriber
        self.logger = logger or _module_logger

        self.logger.debug(
            "AudioPipeline initialized run_demucs=%s demucs_processor=%s run_transcription=%s transcriber=%s",
            self.config.run_demucs,
            type(self.demucs_processor).__name__ if self.demucs_processor is not None else None,
            self.config.run_transcription,
            type(self.transcriber).__name__ if self.transcriber is not None else None,
        )

    def process(self, download_result: DownloadResult, output_dir: Optional[Path] = None) -> AudioPipelineResult:
        """Perform full audio processing for a downloaded video.

        Steps:
        1. Validate download result.
        2. Extract audio using provided AudioExtractor.
        3. Optionally run Demucs if configured and available.
        4. Optionally run transcription if configured and transcriber injected.

        :param download_result: DownloadResult from the downloader.
        :param output_dir: optional directory to place extracted audio (overrides extractor defaults).
        :raises ValueError: if input download_result indicates failure.
        :raises RuntimeError: when Demucs/transcription requested but not available or not provided.
        :return: AudioPipelineResult
        """
        if not download_result.success:
            raise ValueError("Cannot process audio for unsuccessful download_result")

        video_path = download_result.video_path
        self.logger.info("AudioPipeline: processing video %s", video_path)

        # Extract audio
        audio_result = self.extractor.extract(video_path, output_dir=output_dir)

        demucs_output: Optional[DemucsOutput] = None
        transcript: Optional[str] = None

        # Optionally run Demucs
        if self.config.run_demucs:
            if not DEMUCS_AVAILABLE and self.demucs_processor is None:
                raise RuntimeError("Demucs requested but not available in environment and no demucs_processor provided")
            if self.demucs_processor is None:
                raise RuntimeError("Demucs requested but no demucs_processor was injected; provide one via DI")
            self.logger.info("AudioPipeline: running Demucs separation for %s", audio_result.audio_path)
            demucs_output = self.demucs_processor.separate(audio_result.audio_path, output_dir=self.config.demucs_output_dir)
            self.logger.debug("AudioPipeline: demucs produced %s", demucs_output)

        # Optionally run transcription
        if self.config.run_transcription:
            if self.transcriber is None:
                raise RuntimeError(
                    "Transcription requested (run_transcription=True) but no transcriber was injected. "
                    "Provide a transcriber via DI (e.g., a WhisperTranscriber instance) to avoid importing heavy dependencies at module import time."
                )
            self.logger.info("AudioPipeline: running transcription for %s (lang=%s)",
                             audio_result.audio_path, self.config.transcription_language)
            transcript = self.transcriber.transcribe(audio_result.audio_path, language=self.config.transcription_language)
            self.logger.debug("AudioPipeline: transcription length=%s", len(transcript) if transcript is not None else 0)

        return AudioPipelineResult(audio_result=audio_result, demucs_output=demucs_output, transcript=transcript)


__all__ = [
    "AudioPipelineConfig",
    "AudioPipelineResult",
    "AudioPipeline",
]