
# src/universal_video_ai/orchestrator/factory.py
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from universal_video_ai.downloader.service import DownloadService
from universal_video_ai.translate.service import TranslateService
from universal_video_ai.translate.backend import TranslatorBackend
from universal_video_ai.tts.service import TTSService
from universal_video_ai.tts.backend import EdgeTTSBackend
from universal_video_ai.timeline.service import TimelineService
from universal_video_ai.mixer.service import MixerService, MixerConfig
from universal_video_ai.render.renderer import Renderer, RenderConfig
from .service import LocalizationService, LocalizationConfig

__all__ = ["create_localization_service"]

_logger = logging.getLogger(__name__)


def create_localization_service(
        run_transcription: bool = False,
        transcription_language: Optional[str] = None,
        run_demucs: bool = False,
        demucs_output_dir: Optional[Path] = None,
        run_translation: bool = False,
        target_language: Optional[str] = None,
        run_tts: bool = False,
        generate_subtitles: bool = False,
        mix_audio: bool = False,
        render_video: bool = False,
        render_config: Optional[RenderConfig] = None,
        logger: Optional[logging.Logger] = None,
) -> LocalizationService:
    """Convenience factory for LocalizationService with auto-detected backends.

    Features:
    - Auto-detects available backends (TranslatorBackend, EdgeTTSBackend)
    - Logs warnings if backends not available but requested
    - Supports full pipeline: transcription → translation → TTS → subtitles → mixing → rendering
    - DI-friendly: all services injected

    :param run_transcription: enable Whisper transcription
    :param transcription_language: source language (default None auto-detect)
    :param run_demucs: enable Demucs audio stem separation
    :param demucs_output_dir: where to save Demucs outputs (optional)
    :param run_translation: enable translation
    :param target_language: target language for translation (default "en")
    :param run_tts: enable EdgeTTS synthesis
    :param generate_subtitles: generate SRT subtitle file
    :param mix_audio: blend original + TTS audio
    :param render_video: merge video + audio + subtitles into final MP4
    :param render_config: custom RenderConfig (defaults to RenderConfig() if None)
    :param logger: custom logger
    :return: LocalizationService configured with all enabled backends
    """
    logger = logger or _logger

    # Downloader (auto-detects platform from URL)
    downloader = DownloadService()

    # Translation service (optional)
    translate_service = None
    if run_translation:
        try:
            translate_backend = TranslatorBackend()
            translate_service = TranslateService(backend=translate_backend, logger=logger)
            logger.info("TranslatorBackend available; translation enabled")
        except Exception as exc:
            logger.warning("TranslatorBackend not available; translation disabled: %s", exc)

    # TTS service (optional)
    tts_service = None
    if run_tts:
        try:
            tts_backend = EdgeTTSBackend(logger=logger)
            tts_service = TTSService(backend=tts_backend, logger=logger)
            logger.info("EdgeTTSBackend available; TTS enabled")
        except Exception as exc:
            logger.warning("EdgeTTSBackend not available; TTS disabled: %s", exc)

    # Timeline service
    timeline = TimelineService(logger=logger)

    # Mixer service
    mixer = MixerService(config=MixerConfig(), logger=logger)

    # Renderer service (optional)
    renderer = Renderer(config=render_config or RenderConfig(), logger=logger) if render_video else None

    # Build config
    config = LocalizationConfig(
        run_demucs=run_demucs,
        run_transcription=run_transcription,
        transcription_language=transcription_language,
        demucs_output_dir=demucs_output_dir,
        run_translation=run_translation,
        target_language=target_language or "en",
        run_tts=run_tts,
        generate_subtitles=generate_subtitles,
        mix_audio=mix_audio,
        render_video=render_video,
        render_config=render_config,
    )

    return LocalizationService(
        downloader=downloader,
        translate_service=translate_service,
        tts_service=tts_service,
        timeline=timeline,
        mixer=mixer,
        renderer=renderer,
        config=config,
        logger=logger,
    )