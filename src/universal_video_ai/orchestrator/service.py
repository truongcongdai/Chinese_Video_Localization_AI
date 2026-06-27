# src/universal_video_ai/orchestrator/service.py
from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
from typing import Optional

from universal_video_ai.downloader.service import DownloadService
from universal_video_ai.downloader.download_result import DownloadResult
from universal_video_ai.audio.factory import create_audio_pipeline
from universal_video_ai.audio.pipeline import AudioPipelineResult

__all__ = ["LocalizationService", "LocalizationConfig", "LocalizationResult"]

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LocalizationConfig:
    """Configuration for end-to-end video localization."""

    run_demucs: bool = False
    run_transcription: bool = False
    transcription_language: Optional[str] = None
    demucs_output_dir: Optional[Path] = None


@dataclass(frozen=True)
class LocalizationResult:
    """Result of end-to-end localization workflow."""

    download_result: DownloadResult
    audio_pipeline_result: AudioPipelineResult


class LocalizationService:
    """Orchestrator that chains download and audio processing into one call.

    Workflow:
    1. Download video using DownloadService.
    2. Extract/process audio using AudioPipeline.
    3. Return aggregated result.
    """

    def __init__(
            self,
            downloader: Optional[DownloadService] = None,
            config: Optional[LocalizationConfig] = None,
            logger: Optional[logging.Logger] = None,
    ) -> None:
        self.downloader = downloader or DownloadService()
        self.config = config or LocalizationConfig()
        self.logger = logger or _logger

        self.logger.debug(
            "LocalizationService initialized run_demucs=%s run_transcription=%s",
            self.config.run_demucs,
            self.config.run_transcription,
        )

    def localize(self, url: str, output_dir: Path) -> LocalizationResult:
        """Execute full video localization workflow.

        Steps:
        1. Download video from URL.
        2. Extract audio and optionally separate stems/transcribe.
        3. Return aggregated result.

        :param url: video URL to download.
        :param output_dir: directory where to save downloaded video and audio artifacts.
        :raises ValueError: if download fails or processing fails.
        :return: LocalizationResult
        """
        output_dir = Path(output_dir).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)

        self.logger.info("LocalizationService.localize: url=%s output_dir=%s", url, output_dir)

        # Step 1: Download video
        self.logger.info("LocalizationService: downloading video from %s", url)
        download_result = self.downloader.download(url, output_dir)

        if not download_result.success:
            raise ValueError(f"Download failed for {url}")

        self.logger.info("LocalizationService: download successful: %s", download_result.video_path)

        # Step 2: Process audio
        self.logger.info("LocalizationService: processing audio with run_demucs=%s run_transcription=%s",
                         self.config.run_demucs, self.config.run_transcription)

        pipeline = create_audio_pipeline(
            run_demucs=self.config.run_demucs,
            run_transcription=self.config.run_transcription,
            transcription_language=self.config.transcription_language,
            demucs_output_dir=self.config.demucs_output_dir,
            logger=self.logger,
        )

        audio_result = pipeline.process(download_result, output_dir=output_dir / "audio")

        self.logger.info("LocalizationService: audio processing complete")

        return LocalizationResult(
            download_result=download_result,
            audio_pipeline_result=audio_result,
        )