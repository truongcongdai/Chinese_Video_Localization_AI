# src/universal_video_ai/audio/factory.py
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from .extractor import AudioExtractor, AudioConfig
from .pipeline import AudioPipeline, AudioPipelineConfig
from .demucs import DEMUCS_AVAILABLE

_logger = logging.getLogger(__name__)

__all__ = ["create_audio_pipeline"]


def create_audio_pipeline(
    run_demucs: bool = False,
    run_transcription: bool = False,
    transcription_language: Optional[str] = None,
    demucs_output_dir: Optional[Path] = None,
    audio_config: Optional[AudioConfig] = None,
    logger: Optional[logging.Logger] = None,
) -> AudioPipeline:
    """
    Create an AudioPipeline with auto-detected backends.

    This factory constructs a pipeline with:
    - AudioExtractor (always created)
    - Demucs processor (if run_demucs=True and available; None otherwise)
    - SpeechService with WhisperBackend (if run_transcription=True and whisper available; None otherwise)

    Behavior:
    - If a feature is requested but not available, warnings are logged and the pipeline is still returned
      (with that feature disabled at runtime). This allows the pipeline to be created on dev machines
      even if not all optional backends are installed.
    - If a feature is requested and unavailable, calling the corresponding step will raise an error
      (explicit failure at runtime is preferred over early failure at construction time).

    :param run_demucs: whether to attempt Demucs separation.
    :param run_transcription: whether to attempt transcription via Whisper.
    :param transcription_language: language code to pass to transcriber (ignored if run_transcription=False).
    :param demucs_output_dir: optional base directory for demucs outputs.
    :param audio_config: optional AudioConfig for extraction (sample rate, channels, codec, etc.).
    :param logger: optional logger; if None, module logger is used.
    :return: constructed AudioPipeline.
    """
    logger = logger or _logger

    # Always create extractor
    extractor = AudioExtractor(config=audio_config, logger=logger)

    # Optionally create Demucs processor
    demucs_processor = None
    if run_demucs:
        if not DEMUCS_AVAILABLE:
            logger.warning("Demucs requested but not available; pipeline will fail if demucs step is invoked")
        else:
            # Import DemucsProcessor lazily only if available and requested
            try:
                from .demucs import DemucsProcessor, DemucsConfig
                demucs_processor = DemucsProcessor(logger=logger)
                logger.debug("Created DemucsProcessor for pipeline")
            except Exception as exc:
                logger.warning("Failed to construct DemucsProcessor: %s", exc)

    # Optionally create SpeechService with WhisperBackend
    speech_service = None
    if run_transcription:
        try:
            # Check if whisper is available by attempting to import the backend
            from universal_video_ai.speech.backend import WhisperBackend
            from universal_video_ai.speech.service import SpeechService

            backend = WhisperBackend(logger=logger)
            speech_service = SpeechService(backend=backend, logger=logger)
            logger.debug("Created SpeechService with WhisperBackend for pipeline")
        except Exception as exc:
            logger.warning("Failed to construct SpeechService with WhisperBackend: %s", exc)

    # Create pipeline config
    config = AudioPipelineConfig(
        run_demucs=run_demucs,
        demucs_output_dir=demucs_output_dir,
        run_transcription=run_transcription,
        transcription_language=transcription_language,
    )

    # Create pipeline
    pipeline = AudioPipeline(
        config=config,
        extractor=extractor,
        demucs_processor=demucs_processor,
        speech_service=speech_service,
        logger=logger,
    )

    logger.info("AudioPipeline created via factory: run_demucs=%s run_transcription=%s", run_demucs, run_transcription)
    return pipeline