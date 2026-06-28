# src/universal_video_ai/orchestrator/factory.py
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from universal_video_ai.downloader.service import DownloadService
from .service import LocalizationService, LocalizationConfig

_logger = logging.getLogger(__name__)

__all__ = ["create_localization_service"]


def create_localization_service(
    run_demucs: bool = False,
    run_transcription: bool = False,
    transcription_language: Optional[str] = None,
    run_translation: bool = False,
    target_language: Optional[str] = None,
    run_tts: bool = False,
    generate_subtitles: bool = False,
    mix_audio: bool = False,
    demucs_output_dir: Optional[Path] = None,
    logger: Optional[logging.Logger] = None,
) -> LocalizationService:
    """
    Create a LocalizationService with auto-detected backends.

    This factory constructs services for each stage (translation, TTS, etc.)
    only if the corresponding feature is requested and backends are available.

    :param run_demucs: enable audio stem separation
    :param run_transcription: enable speech-to-text
    :param transcription_language: language for transcription (e.g., "en")
    :param run_translation: enable text translation
    :param target_language: language for translation (e.g., "vi")
    :param run_tts: enable text-to-speech synthesis
    :param generate_subtitles: generate subtitle files
    :param mix_audio: mix original + TTS audio
    :param demucs_output_dir: optional directory for demucs outputs
    :param logger: optional logger
    :return: LocalizationService ready to use
    """
    logger = logger or _logger

    # Always create downloader
    downloader = DownloadService()

    # Optionally create TranslateService
    translate_service = None
    if run_translation:
        try:
            from universal_video_ai.translate.backend import TranslatorBackend
            from universal_video_ai.translate.service import TranslateService
            backend = TranslatorBackend(logger=logger)
            translate_service = TranslateService(backend=backend, logger=logger)
            logger.debug("Created TranslateService with TranslatorBackend")
        except Exception as exc:
            logger.warning("Failed to construct TranslateService: %s", exc)

    # Optionally create TTSService
    tts_service = None
    if run_tts:
        try:
            from universal_video_ai.tts.backend import EdgeTTSBackend
            from universal_video_ai.tts.service import TTSService
            backend = EdgeTTSBackend(logger=logger)
            tts_service = TTSService(backend=backend, logger=logger)
            logger.debug("Created TTSService with EdgeTTSBackend")
        except Exception as exc:
            logger.warning("Failed to construct TTSService: %s", exc)

    # Create config
    config = LocalizationConfig(
        run_demucs=run_demucs,
        run_transcription=run_transcription,
        transcription_language=transcription_language,
        run_translation=run_translation,
        target_language=target_language,
        run_tts=run_tts,
        generate_subtitles=generate_subtitles,
        mix_audio=mix_audio,
        demucs_output_dir=demucs_output_dir,
    )

    # Create service
    service = LocalizationService(
        downloader=downloader,
        translate_service=translate_service,
        tts_service=tts_service,
        config=config,
        logger=logger,
    )

    logger.info(
        "LocalizationService created via factory: run_transcription=%s run_translation=%s run_tts=%s",
        run_transcription,
        run_translation,
        run_tts,
    )
    return service