# src/universal_video_ai/orchestrator/factory.py
from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, Optional, Tuple

from universal_video_ai.downloader.service import DownloadService
from universal_video_ai.translate.service import TranslateService
from universal_video_ai.translate.backend import TranslatorBackend
from universal_video_ai.tts.service import TTSService
from universal_video_ai.tts.backend import EdgeTTSBackend
from universal_video_ai.timeline.service import TimelineService
from universal_video_ai.mixer.service import MixerService, MixerConfig
from universal_video_ai.render.renderer import Renderer, RenderConfig
from universal_video_ai.render.text_detector import OnScreenTextDetector
from universal_video_ai.render import ocr_language_map
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
        tts_voice: Optional[str] = None,
        generate_subtitles: bool = False,
        mix_audio: bool = False,
        render_video: bool = False,
        render_config: Optional[RenderConfig] = None,
        enable_text_cover: bool = True,
        ocr_languages: Tuple[str, ...] = ocr_language_map.AUTO_OCR_SENTINEL,
        text_cover_samples_per_segment: int = 2,
        watermark_exclude_regions_fractional: Tuple[Tuple[float, float, float, float], ...] = (
            (0.65, 0.00, 1.00, 0.35),
            (0.80, 0.72, 1.0, 1.0),
        ),
        logger: Optional[logging.Logger] = None,
        progress_callback: Optional[Callable[[int, str], None]] = None,
) -> LocalizationService:
    """Convenience factory for LocalizationService with auto-detected backends.

    Features:
    - Auto-detects available backends (TranslatorBackend, EdgeTTSBackend)
    - Logs warnings if backends not available but requested
    - Supports full pipeline: transcription → translation → TTS → subtitles → mixing → rendering
    - DI-friendly: all services injected

    :param run_transcription: enable Whisper transcription (needed to get real
        per-sentence timestamps; without it, translation/TTS/subtitles fall
        back to whole-transcript, unaligned behavior)
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
    :param enable_text_cover: detect burned-in on-screen text (e.g. Chinese
        subtitles) per sentence via OCR and overlay the translated text in
        its place, timed to that sentence's window. Requires `easyocr`
        (pip install easyocr) and real per-sentence timing (run_transcription
        with a Whisper-like backend); silently skipped otherwise.
    :param ocr_languages: easyocr language codes to detect. Defaults to the
        "auto" sentinel, which picks the language pack automatically from
        whatever spoken language Whisper detects in the audio (see
        `render.ocr_language_map`) instead of assuming Chinese. Pass an
        explicit tuple (e.g. ("ch_sim", "en")) to pin a specific language.
    :param text_cover_samples_per_segment: frames sampled per sentence for OCR
    :param watermark_exclude_regions_fractional: static screen area(s), as
        (x0,y0,x1,y1) fractions of the frame, to ignore when detecting the
        subtitle band — for a platform watermark that's present in nearly
        every frame. Should match render_config's watermark_box_fractional.
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

    # On-screen text detector (optional, best-effort — requires easyocr).
    # When ocr_languages is the "auto" sentinel, the actual easyocr language
    # pack can't be chosen yet (it depends on the spoken-audio language
    # Whisper hasn't detected yet at this point) — so we deliberately leave
    # text_detector=None here and let LocalizationService build one lazily,
    # AFTER transcription, once it knows what language to actually pass.
    # For an explicit (non-auto) language tuple, building it eagerly here
    # still works exactly as before and fails fast if easyocr is missing.
    text_detector = None
    if enable_text_cover and tuple(ocr_languages) != ocr_language_map.AUTO_OCR_SENTINEL:
        try:
            text_detector = OnScreenTextDetector(languages=ocr_languages, logger=logger)
            logger.info("OnScreenTextDetector available; text-cover enabled")
        except Exception as exc:
            logger.warning("OnScreenTextDetector not available; text-cover disabled: %s", exc)
    elif enable_text_cover:
        logger.info(
            "ocr_languages='auto': OnScreenTextDetector will be created lazily per-video "
            "once the spoken audio language is detected."
        )

    # Build config
    config = LocalizationConfig(
        run_demucs=run_demucs,
        run_transcription=run_transcription,
        transcription_language=transcription_language,
        demucs_output_dir=demucs_output_dir,
        run_translation=run_translation,
        target_language=target_language or "en",
        run_tts=run_tts,
        tts_voice=tts_voice,
        generate_subtitles=generate_subtitles,
        mix_audio=mix_audio,
        render_video=render_video,
        render_config=render_config,
        enable_text_cover=enable_text_cover,
        ocr_languages=tuple(ocr_languages),
        text_cover_samples_per_segment=text_cover_samples_per_segment,
        watermark_exclude_regions_fractional=watermark_exclude_regions_fractional,
    )

    return LocalizationService(
        downloader=downloader,
        translate_service=translate_service,
        tts_service=tts_service,
        timeline=timeline,
        mixer=mixer,
        renderer=renderer,
        text_detector=text_detector,
        config=config,
        logger=logger,
        progress_callback=progress_callback,
    )
