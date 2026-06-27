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

logger = logging.getLogger(__name__)


class _DemucsLike(Protocol):
    """Protocol representing the Demucs processor interface we depend on."""

    def separate(self, audio_path: Path, output_dir: Optional[Path] = None) -> DemucsOutput:
        ...


@dataclass(frozen=True)
class AudioPipelineConfig:
    """Configuration for the audio pipeline.

    Attributes:
        run_demucs: whether to run Demucs separation after extraction.
        demucs_output_dir: optional base directory for demucs outputs. If None, Demucs decides defaults.
    """
    run_demucs: bool = False
    demucs_output_dir: Optional[Path] = None


@dataclass(frozen=True)
class AudioPipelineResult:
    """Aggregate result returned by AudioPipeline."""
    audio_result: AudioResult
    demucs_output: Optional[DemucsOutput] = None


class AudioPipeline:
    """Orchestrator that extracts audio from a downloaded video and optionally separates stems.

    Design notes:
    - Keeps responsibilities small: orchestrate existing components, do not replace them.
    - Uses dependency injection for testability (pass extractor and demucs_processor).
    """

    def __init__(
        self,
        config: Optional[AudioPipelineConfig] = None,
        extractor: Optional[AudioExtractor] = None,
        demucs_processor: Optional[_DemucsLike] = None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.config = config or AudioPipelineConfig()
        self.extractor = extractor or AudioExtractor()
        self.demucs_processor = demucs_processor
        self.logger = logger or logger or logging.getLogger(__name__)

        self.logger.debug(
            "AudioPipeline initialized run_demucs=%s demucs_processor=%s",
            self.config.run_demucs,
            type(self.demucs_processor).__name__ if self.demucs_processor is not None else None,
        )

    def process(self, download_result: DownloadResult, output_dir: Optional[Path] = None) -> AudioPipelineResult:
        """Perform full audio processing for a downloaded video.

        Steps:
        1. Validate download result.
        2. Extract audio using provided AudioExtractor.
        3. Optionally run Demucs if configured and available.

        :param download_result: DownloadResult from the downloader.
        :param output_dir: optional directory to place extracted audio (overrides extractor defaults).
        :raises ValueError: if input download_result indicates failure.
        :raises RuntimeError: when Demucs requested but not available or not provided.
        :return: AudioPipelineResult
        """
        if not download_result.success:
            raise ValueError("Cannot process audio for unsuccessful download_result")

        video_path = download_result.video_path
        self.logger.info("AudioPipeline: processing video %s", video_path)

        # Extract audio
        audio_result = self.extractor.extract(video_path, output_dir=output_dir)

        demucs_output: Optional[DemucsOutput] = None

        # Optionally run Demucs
        if self.config.run_demucs:
            # Validate availability
            if not DEMUCS_AVAILABLE and self.demucs_processor is None:
                # Prefer a clear error rather than silently skipping when user explicitly requested Demucs.
                raise RuntimeError("Demucs requested but not available in environment and no demucs_processor provided")
            if self.demucs_processor is None:
                # If DEMUCS_AVAILABLE True, user may still not have provided a processor — construct default
                # but constructing a real DemucsProcessor could import heavy deps; prefer explicit injection.
                raise RuntimeError("Demucs requested but no demucs_processor was injected; provide one via DI")

            self.logger.info("AudioPipeline: running Demucs separation for %s", audio_result.audio_path)
            demucs_output = self.demucs_processor.separate(audio_result.audio_path, output_dir=self.config.demucs_output_dir)
            self.logger.debug("AudioPipeline: demucs produced %s", demucs_output)

        return AudioPipelineResult(audio_result=audio_result, demucs_output=demucs_output)


__all__ = [
    "AudioPipelineConfig",
    "AudioPipelineResult",
    "AudioPipeline",
]