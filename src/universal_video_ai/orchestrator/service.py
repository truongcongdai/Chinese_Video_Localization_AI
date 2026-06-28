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
from universal_video_ai.timeline.service import TimelineService, TimelineConfig
from universal_video_ai.mixer.service import MixerService, MixerConfig, AudioMix
from universal_video_ai.render.renderer import Renderer, RenderConfig

__all__ = ["LocalizationService", "LocalizationConfig", "LocalizationResult"]

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LocalizationConfig:
    """Configuration for end-to-end video localization."""

    run_demucs: bool = False
    run_transcription: bool = False
    transcription_language: Optional[str] = None
    demucs_output_dir: Optional[Path] = None
    generate_subtitles: bool = False
    mix_audio: bool = False


@dataclass(frozen=True)
class LocalizationResult:
    """Result of end-to-end localization workflow."""

    download_result: DownloadResult
    audio_pipeline_result: AudioPipelineResult
    subtitle_segments: Optional[object] = None  # TimelineSegment list or None
    mixed_audio_path: Optional[Path] = None
    final_video_path: Optional[Path] = None


class LocalizationService:
    """Orchestrator that chains: download → audio processing → timeline → mixer → render.

    Workflow:
    1. Download video using DownloadService.
    2. Extract/process audio using AudioPipeline (extraction → demucs → transcription).
    3. Generate subtitles from transcript using TimelineService.
    4. Mix audio streams using MixerService (if needed).
    5. Render final video using Renderer.
    """

    def __init__(
            self,
            downloader: Optional[DownloadService] = None,
            timeline: Optional[TimelineService] = None,
            mixer: Optional[MixerService] = None,
            renderer: Optional[Renderer] = None,
            config: Optional[LocalizationConfig] = None,
            logger: Optional[logging.Logger] = None,
    ) -> None:
        self.downloader = downloader or DownloadService()
        self.timeline = timeline or TimelineService()
        self.mixer = mixer or MixerService()
        self.renderer = renderer or Renderer()
        self.config = config or LocalizationConfig()
        self.logger = logger or _logger

        self.logger.debug(
            "LocalizationService initialized run_demucs=%s run_transcription=%s generate_subtitles=%s mix_audio=%s",
            self.config.run_demucs,
            self.config.run_transcription,
            self.config.generate_subtitles,
            self.config.mix_audio,
        )

    def localize(self, url: str, output_dir: Path) -> LocalizationResult:
        """Execute full video localization workflow.

        Steps:
        1. Download video from URL.
        2. Extract audio and optionally separate stems/transcribe.
        3. Generate subtitles from transcript.
        4. Mix audio streams.
        5. Render final video with subtitles + audio.

        :param url: video URL to download.
        :param output_dir: directory where to save all artifacts.
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

        # Step 3: Generate subtitles (if transcription happened and requested)
        subtitle_segments = None
        if self.config.generate_subtitles and audio_result.transcript:
            self.logger.info("LocalizationService: generating subtitles from transcript")
            subtitle_segments = self.timeline.align_transcript(audio_result.transcript,
                                                               audio_result.audio_result.duration)
            self.logger.info("LocalizationService: generated %d subtitle segments", len(subtitle_segments))

        # Step 4: Mix audio (if needed)
        mixed_audio_path: Optional[Path] = None
        if self.config.mix_audio and audio_result.audio_result:
            self.logger.info("LocalizationService: mixing audio streams")
            mixed_audio_path = output_dir / "audio_mixed.wav"
            self.mixer.mix(AudioMix(primary_audio=audio_result.audio_result.audio_path), mixed_audio_path)
            self.logger.info("LocalizationService: mixed audio saved to %s", mixed_audio_path)

        # Step 5: Render final video (placeholder for now)
        # In a real scenario, this would combine the video with new audio + subtitles
        final_video_path: Optional[Path] = None
        # (Renderer integration would happen here if subtitle rendering is needed)

        return LocalizationResult(
            download_result=download_result,
            audio_pipeline_result=audio_result,
            subtitle_segments=subtitle_segments,
            mixed_audio_path=mixed_audio_path,
            final_video_path=final_video_path,
        )