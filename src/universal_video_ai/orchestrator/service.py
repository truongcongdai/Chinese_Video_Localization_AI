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
from universal_video_ai.translate.service import TranslateService
from universal_video_ai.tts.service import TTSService
from universal_video_ai.timeline.service import TimelineService, TimelineConfig
from universal_video_ai.mixer.service import MixerService, MixerConfig, AudioMix
from universal_video_ai.render.renderer import Renderer, RenderConfig

__all__ = ["LocalizationService", "LocalizationConfig", "LocalizationResult"]

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LocalizationConfig:
    """Configuration for end-to-end video localization."""

    # Audio extraction & processing
    run_demucs: bool = False
    run_transcription: bool = False
    transcription_language: Optional[str] = None
    demucs_output_dir: Optional[Path] = None

    # Translation & TTS
    run_translation: bool = False
    target_language: Optional[str] = None
    run_tts: bool = False

    # Subtitles & mixing
    generate_subtitles: bool = False
    mix_audio: bool = False

    # Rendering
    render_video: bool = False
    render_config: Optional[RenderConfig] = None


@dataclass(frozen=True)
class LocalizationResult:
    """Result of end-to-end localization workflow."""

    download_result: DownloadResult
    audio_pipeline_result: AudioPipelineResult
    translated_text: Optional[str] = None
    tts_audio_path: Optional[Path] = None
    subtitle_segments: Optional[object] = None
    mixed_audio_path: Optional[Path] = None
    final_video_path: Optional[Path] = None


class LocalizationService:
    """Orchestrator: download → audio → transcribe → translate → TTS → subtitles → mix → render.

    Workflow:
    1. Download video.
    2. Extract/process audio (demucs, transcription).
    3. Translate transcript to target language.
    4. Synthesize translated text to speech (TTS).
    5. Generate subtitles.
    6. Mix original + TTS audio.
    7. Render final video (video + mixed audio + subtitles).
    """

    def __init__(
            self,
            downloader: Optional[DownloadService] = None,
            translate_service: Optional[TranslateService] = None,
            tts_service: Optional[TTSService] = None,
            timeline: Optional[TimelineService] = None,
            mixer: Optional[MixerService] = None,
            renderer: Optional[Renderer] = None,
            config: Optional[LocalizationConfig] = None,
            logger: Optional[logging.Logger] = None,
    ) -> None:
        self.downloader = downloader or DownloadService()
        self.translate_service = translate_service
        self.tts_service = tts_service
        self.timeline = timeline or TimelineService()
        self.mixer = mixer or MixerService()
        self.renderer = renderer or Renderer()
        self.config = config or LocalizationConfig()
        self.logger = logger or _logger

        self.logger.debug(
            "LocalizationService initialized run_transcription=%s run_translation=%s run_tts=%s run_render=%s",
            self.config.run_transcription,
            self.config.run_translation,
            self.config.run_tts,
            self.config.render_video,
        )

    async def localize(self, url: str, output_dir: Path) -> LocalizationResult:
        """Execute full video localization workflow.

        :param url: video URL to download.
        :param output_dir: directory where to save all artifacts.
        :raises ValueError: if download fails or processing fails.
        :return: LocalizationResult
        """
        output_dir = Path(output_dir).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)

        self.logger.info("LocalizationService.localize: url=%s output_dir=%s", url, output_dir)

        # Step 1: Download video
        self.logger.info("LocalizationService: downloading video")
        download_result = self.downloader.download(url, output_dir)

        if not download_result.success:
            raise ValueError(f"Download failed for {url}")

        self.logger.info("LocalizationService: download successful: %s", download_result.video_path)

        # Step 2: Process audio (extract → demucs → transcribe)
        self.logger.info("LocalizationService: processing audio")
        pipeline = create_audio_pipeline(
            run_demucs=self.config.run_demucs,
            run_transcription=self.config.run_transcription,
            transcription_language=self.config.transcription_language,
            demucs_output_dir=self.config.demucs_output_dir,
            logger=self.logger,
        )
        audio_result = pipeline.process(download_result, output_dir=output_dir / "audio")
        self.logger.info("LocalizationService: audio processing complete")

        translated_text: Optional[str] = None
        tts_audio_path: Optional[Path] = None

        # Step 3: Translate transcript
        if self.config.run_translation and audio_result.transcript:
            if self.translate_service is None:
                self.logger.warning("Translation requested but no TranslateService injected; skipping")
            else:
                self.logger.info("LocalizationService: translating to %s", self.config.target_language)
                try:
                    translated_text = await self.translate_service.translate(
                        audio_result.transcript,
                        source_lang=self.config.transcription_language or "en",
                        target_lang=self.config.target_language or "en",
                    )
                    self.logger.info("LocalizationService: translation complete (length=%d)", len(translated_text))
                except Exception as exc:
                    self.logger.error("Translation failed: %s", exc)
                    translated_text = None

        # Step 4: Synthesize TTS from translated text
        if self.config.run_tts and translated_text:
            if self.tts_service is None:
                self.logger.warning("TTS requested but no TTSService injected; skipping")
            else:
                self.logger.info("LocalizationService: synthesizing TTS")
                try:
                    tts_audio_path = output_dir / "tts_audio.wav"
                    self.tts_service.synthesize(
                        translated_text,
                        output_path=tts_audio_path,
                        language=self.config.target_language or "en",
                    )
                    self.logger.info("LocalizationService: TTS complete: %s", tts_audio_path)
                except Exception as exc:
                    self.logger.error("TTS synthesis failed: %s", exc)
                    tts_audio_path = None

        # Step 5: Generate subtitles
        subtitle_segments = None
        subtitles_path: Optional[Path] = None
        if self.config.generate_subtitles and audio_result.transcript:
            self.logger.info("LocalizationService: generating subtitles")
            subtitle_segments = self.timeline.align_transcript(
                audio_result.transcript,
                audio_result.audio_result.duration
            )
            self.logger.info("LocalizationService: generated %d subtitle segments", len(subtitle_segments))

            # Write subtitles to SRT file
            subtitles_path = output_dir / "subtitles.srt"
            srt_content = self.timeline.generate_srt(subtitle_segments)
            subtitles_path.write_text(srt_content, encoding="utf-8")
            self.logger.info("LocalizationService: subtitles written to %s", subtitles_path)

        # Step 6: Mix audio (original + TTS)
        mixed_audio_path: Optional[Path] = None
        if self.config.mix_audio and tts_audio_path:
            self.logger.info("LocalizationService: mixing audio streams")
            mixed_audio_path = output_dir / "audio_mixed.wav"
            self.mixer.mix(
                AudioMix(primary_audio=audio_result.audio_result.audio_path, secondary_audio=tts_audio_path),
                mixed_audio_path
            )
            self.logger.info("LocalizationService: audio mix complete: %s", mixed_audio_path)

        # Step 7: Render final video (merge video + audio + optional subtitles)
        final_video_path: Optional[Path] = None
        if self.config.render_video and download_result.video_path:
            if self.renderer is None:
                self.logger.warning("Rendering requested but no Renderer available; skipping")
            else:
                self.logger.info("LocalizationService: rendering final video")
                try:
                    # Use mixed audio if available, otherwise use TTS audio, otherwise use original audio
                    audio_for_render = mixed_audio_path or tts_audio_path or audio_result.audio_result.audio_path
                    render_config = self.config.render_config or RenderConfig()

                    final_video_path = output_dir / "output_final.mp4"
                    self.renderer.render(
                        video_path=download_result.video_path,
                        audio_path=audio_for_render,
                        subtitles=subtitles_path,
                        output_path=final_video_path,
                    )
                    self.logger.info("LocalizationService: render complete: %s", final_video_path)
                except Exception as exc:
                    self.logger.error("Rendering failed: %s", exc)
                    final_video_path = None

        return LocalizationResult(
            download_result=download_result,
            audio_pipeline_result=audio_result,
            translated_text=translated_text,
            tts_audio_path=tts_audio_path,
            subtitle_segments=subtitle_segments,
            mixed_audio_path=mixed_audio_path,
            final_video_path=final_video_path,
        )