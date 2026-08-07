# src/universal_video_ai/orchestrator/service.py
from __future__ import annotations

from dataclasses import dataclass, field, replace
import asyncio
import logging
import os
import subprocess
import wave
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from universal_video_ai.downloader.service import DownloadService
from universal_video_ai.downloader.download_result import DownloadResult
from universal_video_ai.downloader.platform import Platform
from universal_video_ai.downloader.rate_limiter import get_rate_limiter
from universal_video_ai.audio.factory import create_audio_pipeline
from universal_video_ai.audio.pipeline import AudioPipelineResult
from universal_video_ai.audio.audio_result import AudioResult
from universal_video_ai.audio.background_music import BackgroundMusicLibrary
from universal_video_ai.segment import TranscriptSegment
from universal_video_ai.translate.service import TranslateService
from universal_video_ai.translate.adapt import SegmentAdapter
from universal_video_ai.translate.speech_fit import SpeechFitConfig, fit_translated_segments
from universal_video_ai.tts.service import TTSService
from universal_video_ai.timeline.service import TimelineService, TimelineConfig, TimelineSegment
from universal_video_ai.mixer.service import (
    MixerService, MixerConfig, AudioMix, TimedAudioClip, DubbedBackgroundMix,
    DubbedSourceBackgroundMix,
)
from universal_video_ai.render.animated_subtitles import SubtitleEffect
from universal_video_ai.render.renderer import Renderer, RenderConfig, TextOverlay
from universal_video_ai.render.text_detector import OnScreenTextDetector, TextRegion
from universal_video_ai.render import ocr_language_map

__all__ = [
    "LocalizationService", "LocalizationConfig", "LocalizationResult",
    "PreparedLocalization", "prepared_localization_to_dict", "prepared_localization_from_dict",
]

_logger = logging.getLogger(__name__)

# Pipeline-level concurrency: network download and FFmpeg can overlap with the
# single CPU-heavy Whisper stage. Running several Whisper-small inferences at
# once on a 16 GB CPU host causes thread oversubscription and swapping.
_DOWNLOAD_SLOTS = asyncio.Semaphore(max(1, int(os.getenv("DOWNLOAD_CONCURRENCY", "10"))))
_TRANSCRIPTION_SLOTS = asyncio.Semaphore(max(1, int(os.getenv("TRANSCRIPTION_CONCURRENCY", "5"))))
_RENDER_SLOTS = asyncio.Semaphore(max(1, int(os.getenv("RENDER_CONCURRENCY", "2"))))
_TTS_SLOTS = asyncio.Semaphore(max(1, int(os.getenv("TTS_CONCURRENCY", "8"))))


@dataclass(frozen=True)
class LocalizationConfig:
    """Configuration for end-to-end video localization."""

    # Audio extraction & processing
    run_demucs: bool = False
    run_transcription: bool = False
    transcription_language: Optional[str] = None
    transcription_model: Optional[str] = None
    demucs_output_dir: Optional[Path] = None

    # Translation & TTS
    run_translation: bool = False
    target_language: Optional[str] = None
    run_tts: bool = False
    # Explicit Edge-TTS voice id (e.g. "vi-VN-NamMinhNeural") to use instead
    # of the target language's default voice — see tts.voices.VOICE_OPTIONS
    # for the curated male/female choices the web UI offers. None = use
    # tts.voice_for_language()'s default for target_language, unchanged
    # from before this option existed.
    tts_voice: Optional[str] = None
    # Fit each generated WAV to its real timeline slot instead of letting the
    # mixer truncate words at the nominal subtitle end.  The clip duration is
    # measured after synthesis; modest atempo compression is preferred, then
    # the clip is allowed to extend rather than being cut.
    tts_fit_real_duration: bool = True
    tts_max_speed_ratio: float = 1.70
    tts_neighbor_gap_seconds: float = 0.025

    # Subtitles & mixing
    generate_subtitles: bool = False
    mix_audio: bool = False
    # When True (default) and per-sentence text_overlays were successfully
    # built (OCR detected + covered the original on-screen text and drew
    # the translation in its place), the separate burned .srt subtitle
    # track is skipped for the final render. Burning both at once put two
    # different translated-text renderings on screen simultaneously (the
    # in-place overlay AND a second copy at the bottom via the .srt file),
    # which is confusing/cluttered — text_overlays alone already shows the
    # translation. Set to False to keep burning the .srt as a bottom
    # caption in addition to the overlays (e.g. as an accessibility backup).
    skip_srt_when_text_overlays_present: bool = True
    # Volume weight (0-1) given to the ORIGINAL audio when mixing with the
    # dubbed track; the dub gets `1 - dub_mix_level_primary`. Low by design:
    # the dubbed voice needs to be clearly audible over the original
    # dialogue, with the original kept only as low ambience/music under it.
    dub_mix_level_primary: float = 0.18
    # Copyright-safe mode never mixes the downloaded source audio into the
    # localized render. This avoids re-introducing source music after TTS.
    replace_source_audio: bool = False
    replacement_music_volume: float = 0.16
    source_effects_volume: float = 1.0
    # Place translated captions inside the OCR cover boxes by default, so the
    # new subtitle sits centered in the white box that covers the original
    # hard subtitle.
    place_subtitles_in_text_cover_boxes: bool = True

    # Rendering
    render_video: bool = False
    render_config: Optional[RenderConfig] = None

    # On-screen text cover (e.g. burned-in Chinese subtitles): detect the
    # region via OCR and overlay the translated sentence in its place,
    # timed to the same window as the original sentence. Best-effort:
    # requires `easyocr` to be installed and requires per-sentence timing
    # (i.e. `run_transcription` using a backend that provides real
    # timestamps, such as Whisper). Silently skipped otherwise.
    enable_text_cover: bool = True
    # Easyocr language pack(s) for detecting burned-in on-screen text.
    # Default is the AUTO_OCR_SENTINEL ("auto"): pick automatically from
    # whatever spoken language Whisper detected in the audio (see
    # render.ocr_language_map), instead of assuming every source video is
    # Chinese. Pass an explicit tuple (e.g. ("ch_sim", "en")) to pin it.
    ocr_languages: Tuple[str, ...] = ocr_language_map.AUTO_OCR_SENTINEL
    # Static screen area(s) to ignore when detecting the burned-in subtitle,
    # as (x0, y0, x1, y1) fractions (0.0-1.0) of the frame — for a platform
    # watermark (logo/@username/reup title) that's present in nearly every
    # frame and would otherwise confuse subtitle-band detection. Should
    # match RenderConfig.watermark_box_fractional used for the actual cover.
    watermark_exclude_regions_fractional: Tuple[Tuple[float, float, float, float], ...] = (
        # Persistent banners/ads in the upper-right are especially likely
        # to beat real subtitles in the OCR density vote because they are
        # present in every sampled frame. Exclude only that corner, leaving
        # upper-centre captions detectable.
        (0.65, 0.00, 1.00, 0.35),
        # Common Douyin/TikTok watermark/account area.
        (0.80, 0.72, 1.0, 1.0),
    )
    text_cover_samples_per_segment: int = 4  # Increased from 2 for better OCR detection
    # If the source video has burned-in subtitles whose visual timing differs
    # from ASR/audio timing, estimate a single OCR-based offset and shift the
    # per-segment timeline before translation/TTS/render. This keeps dubbed
    # audio, translated subtitles, and cover boxes aligned to what viewers see.
    align_to_burned_subtitles: bool = True
    max_subtitle_alignment_offset: float = 12.0
    # Offsets below this are usually decoder/ASR boundary noise rather than a
    # separate hard-subtitle layer delay.
    min_subtitle_alignment_offset: float = 0.03
    # Minimum confidence required for subtitle offset detection to be applied.
    # Prevents applying unreliable offsets that would misalign subtitles.
    min_subtitle_alignment_confidence: float = 0.40
    # Small offsets are shifted on the TTS clock too. Larger offsets stay
    # visual-only to avoid moving dubbed speech away from the real audio.
    max_audio_sync_offset: float = 1.0
    # Prefer detecting each burned-in subtitle cue's own visible start/end
    # time over applying a single whole-video offset. This is the batch-safe
    # path for videos whose first source subtitle starts at e.g. 0.4s and
    # whose later cues have natural gaps that should be preserved exactly.
    use_source_subtitle_timing: bool = True
    source_subtitle_timing_search_radius: float = 2.5
    source_subtitle_timing_step: float = 0.2
    source_subtitle_timing_min_coverage: float = 0.60
    # Validate that source timing roughly matches visual timing before using it.
    # If the first N source segments can't find any visual presence within
    # search_radius, disable source timing and use visual-only detection.
    validate_source_timing_match: bool = True
    source_timing_validation_segments: int = 3
    # Optional visual-only padding for burned-subtitle cover/subtitle windows.
    # Defaults to 0 so generated subtitle timestamps stay exactly on the
    # detected source-subtitle timeline. Non-zero values are only for users
    # who explicitly prefer wider cover boxes over exact subtitle timing.
    visual_subtitle_timing_padding: float = 0.0
    # Global offset to apply to all subtitle timestamps when OCR alignment
    # is disabled or not working. Positive values shift subtitles later,
    # negative values shift them earlier. Use this when Whisper timestamps
    # are consistently off from the actual audio/subtitle timing.
    global_subtitle_offset: float = 0.0
    auto_blur_static_text: bool = True
    static_text_blur_samples: int = 6
    # Duration-aware subtitle/dub guardrail. This runs after base translation
    # and optional LLM adaptation so every segment is checked against its
    # original timestamp before subtitles and TTS are generated.
    speech_fit: SpeechFitConfig = field(default_factory=SpeechFitConfig)


@dataclass(frozen=True)
class LocalizationResult:
    """Result of end-to-end localization workflow."""

    download_result: DownloadResult
    audio_pipeline_result: AudioPipelineResult
    translated_text: Optional[str] = None
    source_segments: Optional[List[TranscriptSegment]] = None
    # Per-sentence translated segments with the ORIGINAL sentence's start/end
    # timestamps preserved. This is what keeps dubbing/subtitles/text-cover
    # aligned with the source video; `translated_text` is kept only for
    # backward compatibility with callers that just want the flat string.
    translated_segments: Optional[List[TranscriptSegment]] = None
    # Audio-clock copy used for TTS placement. `translated_segments` is the
    # viewer/subtitle clock and may be shifted to match burned-in subtitle
    # frames, while this stays anchored to the original spoken audio.
    tts_segments: Optional[List[TranscriptSegment]] = None
    tts_audio_path: Optional[Path] = None
    subtitle_segments: Optional[List[TimelineSegment]] = None
    mixed_audio_path: Optional[Path] = None
    text_overlays: Optional[List[TextOverlay]] = None
    final_video_path: Optional[Path] = None


@dataclass(frozen=True)
class PreparedLocalization:
    """Everything `_finalize()` needs to pick up where `_prepare()` left
    off: the downloaded video, the processed/transcribed audio, and the
    machine translation — but before TTS/subtitles/render have happened.
    This is the hand-off point for an optional "let a person edit the
    translated text before rendering" step."""

    download_result: DownloadResult
    audio_result: AudioPipelineResult
    source_segments: List[TranscriptSegment]
    translated_segments: Optional[List[TranscriptSegment]]
    tts_segments: Optional[List[TranscriptSegment]]
    translated_text: Optional[str]
    target_language: str
    output_dir: Path


def prepared_localization_to_dict(prepared: PreparedLocalization) -> Dict[str, Any]:
    """
    JSON-serializable snapshot of a `PreparedLocalization`, for persisting
    across the gap between "translation is ready, waiting on a person to
    review it" and "they clicked render" — which, in the web app, are two
    separate HTTP requests (and the process may even have restarted served
    a DB-backed job queue). Round-trip with `prepared_localization_from_dict`.

    Only the fields `_finalize()` actually reads are kept (video path;
    audio path/duration; detected language; the segments) — this is a
    deliberately narrow snapshot, not a generic object dump.
    """
    audio_result = prepared.audio_result.audio_result
    return {
        "video_path": str(prepared.download_result.video_path),
        "audio_path": str(audio_result.audio_path),
        "audio_duration": audio_result.duration,
        "detected_language": prepared.audio_result.detected_language,
        "target_language": prepared.target_language,
        "output_dir": str(prepared.output_dir),
        "source_segments": [
            {"start": s.start, "end": s.end, "text": s.text} for s in prepared.source_segments
        ],
        "translated_segments": [
            {"start": s.start, "end": s.end, "text": s.text} for s in (prepared.translated_segments or [])
        ],
        "tts_segments": [
            {"start": s.start, "end": s.end, "text": s.text} for s in (prepared.tts_segments or [])
        ],
        "translated_text": prepared.translated_text,
    }


def prepared_localization_from_dict(data: Dict[str, Any]) -> PreparedLocalization:
    """Inverse of `prepared_localization_to_dict`. Reconstructs minimal-but-
    sufficient `DownloadResult`/`AudioPipelineResult` stand-ins — only the
    fields `_finalize()` reads are populated with real values; everything
    else gets an inert placeholder, since nothing downstream of `_finalize`
    reads them."""
    download_result = DownloadResult(
        success=True, platform=Platform.OTHER, original_url="", final_url="",
        video_path=Path(data["video_path"]),
    )
    audio_result = AudioResult(
        success=True, audio_path=Path(data["audio_path"]), duration=data["audio_duration"],
        sample_rate=0, channels=0, bitrate=None, format="wav", filesize=0,
    )
    audio_pipeline_result = AudioPipelineResult(
        audio_result=audio_result,
        transcript=None,
        segments=None,
        detected_language=data.get("detected_language"),
    )
    source_segments = [
        TranscriptSegment(start=s["start"], end=s["end"], text=s["text"])
        for s in data.get("source_segments", [])
    ]
    translated_segments = [
        TranscriptSegment(start=s["start"], end=s["end"], text=s["text"])
        for s in data.get("translated_segments", [])
    ] or None
    tts_segments = [
        TranscriptSegment(start=s["start"], end=s["end"], text=s["text"])
        for s in data.get("tts_segments", [])
    ] or None
    return PreparedLocalization(
        download_result=download_result,
        audio_result=audio_pipeline_result,
        source_segments=source_segments,
        translated_segments=translated_segments,
        tts_segments=tts_segments,
        translated_text=data.get("translated_text"),
        target_language=data["target_language"],
        output_dir=Path(data["output_dir"]),
    )


class LocalizationService:
    """Orchestrator: download → audio → transcribe → translate → TTS → subtitles → mix → render.

    Workflow:
    1. Download video.
    2. Extract/process audio (demucs, transcription) — transcription produces
       per-sentence `TranscriptSegment`s with real start/end timestamps
       whenever the backend supports it (e.g. Whisper).
    3. Translate each sentence individually, keeping its original timing
       (`TranslateService.translate_segments`), instead of translating the
       whole transcript as one blob. This is what lets "what was said at
       0-3s in the source" map to "what is said at 0-3s in the dub".
    4. Synthesize each translated sentence to speech separately and assemble
       them onto a single track anchored at their original timestamps
       (`MixerService.build_dubbed_track`), time-stretching each clip to fit
       its slot so the dub doesn't drift out of sync over a long video.
    5. Generate subtitles directly from the translated segments' real
       timestamps (`TimelineService.from_segments`) instead of guessing by
       evenly splitting the text.
    6. Optionally detect burned-in on-screen text per sentence (OCR) and
       build a translated overlay that covers it, shown only during that
       sentence's window.
    7. Mix original + dubbed audio.
    8. Render final video (video + mixed/dubbed audio + subtitles + overlays).

    When per-sentence timing isn't available (e.g. a non-Whisper backend that
    only returns flat text), the service falls back to the previous
    whole-transcript behavior so it still produces a (less perfectly
    aligned) result rather than failing outright.
    """

    def __init__(
            self,
            downloader: Optional[DownloadService] = None,
            translate_service: Optional[TranslateService] = None,
            segment_adapter: Optional[SegmentAdapter] = None,
            tts_service: Optional[TTSService] = None,
            timeline: Optional[TimelineService] = None,
            mixer: Optional[MixerService] = None,
            background_music_library: Optional[BackgroundMusicLibrary] = None,
            renderer: Optional[Renderer] = None,
            text_detector: Optional[OnScreenTextDetector] = None,
            config: Optional[LocalizationConfig] = None,
            logger: Optional[logging.Logger] = None,
            progress_callback: Optional[Callable[[int, str], None]] = None,
            cancellation_checker: Optional[Callable[[], bool]] = None,
            user_id: Optional[int] = None,
            use_download_cache: bool = True,
    ) -> None:
        self.downloader = downloader or DownloadService(user_id=user_id, use_cache=use_download_cache)
        self.translate_service = translate_service
        self.segment_adapter = segment_adapter
        self.tts_service = tts_service
        self.timeline = timeline or TimelineService()
        self.mixer = mixer or MixerService()
        self.background_music_library = background_music_library
        self.renderer = renderer or Renderer()
        self.text_detector = text_detector
        self.config = config or LocalizationConfig()
        self.logger = logger or _logger
        self.progress_callback = progress_callback
        self.cancellation_checker = cancellation_checker
        self.user_id = user_id
        self._last_subtitle_alignment_estimate = None
        self._used_source_subtitle_timing = False

        self.logger.debug(
            "LocalizationService initialized run_transcription=%s run_translation=%s run_tts=%s run_render=%s "
            "enable_text_cover=%s",
            self.config.run_transcription,
            self.config.run_translation,
            self.config.run_tts,
            self.config.render_video,
            self.config.enable_text_cover,
        )

    def _progress(self, percent: int, message: str) -> None:
        self._raise_if_cancelled()
        if self.progress_callback:
            try:
                self.progress_callback(max(0, min(100, percent)), message)
            except Exception:
                self.logger.exception("Progress callback failed")

    def _raise_if_cancelled(self) -> None:
        if self.cancellation_checker and self.cancellation_checker():
            raise RuntimeError("Job cancelled by user")

    async def localize(self, url: str, output_dir: Path, target_language: Optional[str] = None) -> LocalizationResult:
        """Execute full video localization workflow, start to finish, with
        no pause for review. For a workflow that stops after translation so
        a person can edit the translated text first, use
        `prepare_for_review()` + `finalize_from_review()` instead — this
        method is just `_prepare()` immediately followed by `_finalize()`.

        :param url: video URL to download.
        :param output_dir: directory where to save all artifacts.
        :param target_language: optional override for this call's target
            language (e.g. a Telegram bot picking the language per-request).
            Falls back to `self.config.target_language` when omitted.
        :raises ValueError: if download fails or processing fails.
        :return: LocalizationResult
        """
        prepared = await self._prepare(url, output_dir, target_language)
        return await self._finalize(prepared)

    async def prepare_for_review(
        self, url: str, output_dir: Path, target_language: Optional[str] = None
    ) -> "PreparedLocalization":
        """
        Run just the download + transcribe + translate steps and stop there,
        returning everything needed to either inspect/edit the translated
        text or continue on to `finalize_from_review()`.

        Use this (instead of `localize()`) when the caller wants a chance to
        review/edit the translated sentences before TTS + render actually
        happen — e.g. the web UI's optional "chỉnh sửa phụ đề trước khi
        render" step.
        """
        return await self._prepare(url, output_dir, target_language)

    async def finalize_from_review(
        self,
        prepared: "PreparedLocalization",
        edited_segments: Optional[List[TranscriptSegment]] = None,
    ) -> LocalizationResult:
        """
        Resume a `prepare_for_review()` call through to a finished video.

        :param prepared: whatever `prepare_for_review()` returned earlier
            (or reconstructed via `prepared_localization_from_dict` after a
            round-trip through storage — see that function's docstring).
        :param edited_segments: the (possibly user-edited) translated
            segments to actually render with. None uses `prepared`'s
            original machine translation unchanged.
        """
        return await self._finalize(prepared, edited_segments)

    async def _prepare(
        self, url: str, output_dir: Path, target_language: Optional[str] = None
    ) -> "PreparedLocalization":
        """Steps 1-3: download the source video, extract/transcribe its
        audio, and translate the transcript. See `localize()`.

        Optimized with parallel processing where possible:
        - Download runs immediately with rate limiting
        - Audio processing starts as soon as download completes
        - Translation can start as soon as transcript is available
        """
        output_dir = Path(output_dir).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)

        effective_target_language = target_language or self.config.target_language or "en"

        self.logger.info(
            "LocalizationService._prepare: url=%s output_dir=%s target_language=%s",
            url, output_dir, effective_target_language,
        )

        # Step 1: Download video (or use uploaded file directly)
        if url.startswith("file://"):
            # Handle uploaded video file - skip download
            self._progress(5, "Đang chuẩn bị file video...")
            self.logger.info("LocalizationService: using uploaded file (file:// protocol)")
            from universal_video_ai.downloader.download_result import DownloadResult
            from universal_video_ai.downloader.platform import Platform

            # Extract file path from file:// URL
            file_path_str = url[7:]  # Remove "file://" prefix
            video_path = Path(file_path_str)

            if not video_path.exists():
                raise ValueError(f"Uploaded file not found: {video_path}")

            # Create DownloadResult directly from file
            file_size = video_path.stat().st_size
            file_ext = video_path.suffix.lower()
            file_stem = video_path.stem

            download_result = DownloadResult(
                success=True,
                platform=Platform.GENERIC,
                original_url=url,
                final_url=url,
                video_path=video_path,
                title=file_stem,
                filesize=file_size,
                extension=file_ext[1:] if file_ext else "mp4",  # Remove dot
            )

            self.logger.info("LocalizationService: uploaded file ready: %s (size=%d bytes)", video_path, file_size)
            self._progress(15, "Đã chuẩn bị file video ✓")
        else:
            # Regular download from URL
            self._progress(5, "Đang tải video nguồn...")
            self.logger.info(
                "LocalizationService: downloading video url=%s output_dir=%s",
                url,
                output_dir,
            )

            # Apply rate limiting before download
            rate_limiter = get_rate_limiter()
            await rate_limiter.acquire(self.user_id)

            try:
                async with _DOWNLOAD_SLOTS:
                    download_result = await asyncio.to_thread(self.downloader.download, url, output_dir)
            finally:
                rate_limiter.release()

            if not download_result.success:
                raise ValueError(f"Download failed for {url}")

            self.logger.info(
                "LocalizationService: download successful requested_url=%s final_url=%s path=%s",
                url,
                getattr(download_result, "final_url", None),
                download_result.video_path,
            )
            self._progress(15, "Đã tải video ✓")

        # Step 2: Process audio (extract → demucs → transcribe)
        self._progress(20, "Đang tách âm thanh...")
        self.logger.info("LocalizationService: processing audio")
        pipeline = create_audio_pipeline(
            run_demucs=self.config.run_demucs,
            run_transcription=self.config.run_transcription,
            transcription_language=self.config.transcription_language,
            transcription_model=self.config.transcription_model,
            demucs_output_dir=self.config.demucs_output_dir,
            logger=self.logger,
        )

        # Run audio processing (this is the CPU-heavy bottleneck)
        async with _TRANSCRIPTION_SLOTS:
            audio_result = await asyncio.to_thread(
                pipeline.process, download_result, output_dir=output_dir / "audio"
            )
        self.logger.info("LocalizationService: audio processing complete")
        self._progress(40, "Đã nhận diện lời nói ✓")

        audio_source_segments: List[TranscriptSegment] = [
            s for s in (audio_result.segments or []) if s.has_timing
        ]

        self._last_subtitle_alignment_estimate = None
        self._used_source_subtitle_timing = False
        visual_source_segments = self._align_source_segments_to_burned_subtitles(
            video_path=download_result.video_path,
            source_segments=audio_source_segments,
            detected_language=audio_result.detected_language,
            audio_duration=audio_result.audio_result.duration,
        )

        translated_text: Optional[str] = None
        translated_segments: Optional[List[TranscriptSegment]] = None
        tts_segments: Optional[List[TranscriptSegment]] = None

        # Step 3: Translate transcript — prefer segment-level translation so
        # every sentence keeps the timestamp it had in the source video.
        if self.config.run_translation and audio_result.transcript:
            self._progress(45, "Đang dịch nội dung...")
            if self.translate_service is None:
                self.logger.warning("Translation requested but no TranslateService injected; skipping")
            else:
                # IMPORTANT: when transcription_language wasn't pinned (the
                # normal case — Whisper auto-detects the spoken language),
                # do NOT fall back to a hardcoded "en". The source video is
                # very often Chinese (or another non-English language), and
                # telling the translator "source=en" for non-English text
                # produces garbage/incorrect translations even though the
                # HTTP call itself "succeeds". "auto" lets the translation
                # backend detect the actual language of each sentence.
                source_lang = self.config.transcription_language or "auto"
                target_lang = effective_target_language
                try:
                    if audio_source_segments:
                        self.logger.info(
                            "LocalizationService: translating %d timed segments to %s",
                            len(audio_source_segments), target_lang,
                        )
                        translated_segments = await self.translate_service.translate_segments(
                            audio_source_segments, source_lang=source_lang, target_lang=target_lang
                        )
                        if self.segment_adapter is not None:
                            adapter_config = getattr(self.segment_adapter, "config", None)
                            adapter_provider = getattr(adapter_config, "provider", "LLM")
                            adapter_model = getattr(adapter_config, "model", "")
                            adapter_label = f"{adapter_provider} {adapter_model}".strip()
                            self.logger.info("LocalizationService: adapting translation with %s", adapter_label)
                            self._progress(54, f"Đang tối ưu bản dịch bằng {adapter_label}...")
                            translated_segments = await self.segment_adapter.adapt_segments(
                                audio_source_segments,
                                translated_segments,
                                source_lang=source_lang,
                                target_lang=target_lang,
                            )
                        translated_segments = fit_translated_segments(
                            translated_segments,
                            self.config.speech_fit,
                        )
                        audio_clock_translated_segments = translated_segments
                        # First align to visual timeline (burned-in subtitles)
                        translated_segments = self._retime_segments(
                            translated_segments,
                            visual_source_segments,
                        )
                        # Keep TTS anchored to the spoken-audio clock. Only
                        # small, whole-video subtitle offsets are safe to
                        # apply to voice; large hard-subtitle delays are
                        # visual-only and should not move dubbed speech away
                        # from the original dialogue.
                        if self._used_source_subtitle_timing:
                            tts_segments = translated_segments
                        else:
                            tts_segments = self._maybe_apply_audio_sync_offset(
                                audio_clock_translated_segments,
                                audio_result.audio_result.duration,
                            )
                        translated_text = " ".join(s.text for s in translated_segments if s.text)
                    else:
                        # Fallback: no real per-sentence timing available (e.g. a
                        # non-Whisper backend). Translate the whole transcript as
                        # one blob, same as before.
                        self.logger.info(
                            "LocalizationService: no timed segments available; translating whole transcript to %s",
                            target_lang,
                        )
                        translated_text = await self.translate_service.translate(
                            audio_result.transcript, source_lang=source_lang, target_lang=target_lang
                        )
                    self.logger.info(
                        "LocalizationService: translation complete (length=%d)",
                        len(translated_text) if translated_text else 0,
                    )
                    self._progress(58, "Đã dịch nội dung ✓")
                except Exception as exc:
                    self.logger.error("Translation failed: %s", exc)
                    # A localized video with untranslated source-language
                    # subtitles is a corrupt result, not a successful fallback.
                    # Fail the job so it can be retried once the provider is
                    # reachable instead of silently rendering the wrong output.
                    raise

        return PreparedLocalization(
            download_result=download_result,
            audio_result=audio_result,
            source_segments=visual_source_segments,
            translated_segments=translated_segments,
            tts_segments=tts_segments,
            translated_text=translated_text,
            target_language=effective_target_language,
            output_dir=output_dir,
        )

    @staticmethod
    def _fill_missing_subtitle_windows(
        source_segments: List[TranscriptSegment],
        windows: List[Optional[Any]],
        audio_duration: float,
        min_gap: float = 0.03,
        min_duration: float = 0.12,
    ) -> List[Optional[Any]]:
        """Fill OCR-missed cue windows from neighbouring visual timing anchors.

        Mixing OCR-adjusted cues with untouched ASR cues creates two clocks in
        the same output: detected captions/voice follow the burned subtitles,
        while missed cues appear too early.  Once visual timing has sufficient
        coverage, interpolate the visual offset for missed cues so subtitles,
        overlays and TTS all use one monotonic clock.
        """
        if not source_segments or len(source_segments) != len(windows):
            return windows
        anchors = [idx for idx, window in enumerate(windows) if window is not None]
        if not anchors:
            return windows

        def offset_at(index: int) -> float:
            previous = max((i for i in anchors if i < index), default=None)
            following = min((i for i in anchors if i > index), default=None)
            if previous is None:
                anchor = following if following is not None else anchors[0]
                return float(windows[anchor].start - source_segments[anchor].start)
            if following is None:
                return float(windows[previous].start - source_segments[previous].start)
            left_offset = float(windows[previous].start - source_segments[previous].start)
            right_offset = float(windows[following].start - source_segments[following].start)
            span = max(1, following - previous)
            weight = (index - previous) / span
            return left_offset + ((right_offset - left_offset) * weight)

        resolved: List[Optional[Any]] = list(windows)
        window_type = type(next(w for w in windows if w is not None))
        for idx, (segment, window) in enumerate(zip(source_segments, resolved)):
            if window is not None:
                continue
            offset = offset_at(idx)
            start = max(0.0, min(audio_duration, segment.start + offset))
            end = max(start + min_duration, min(audio_duration, segment.end + offset))
            if end <= start:
                continue
            resolved[idx] = window_type(
                start=round(start, 3),
                end=round(end, 3),
                confidence=0.25,
            )

        # Enforce a single monotonic non-overlapping clock.  Boundaries are
        # split between neighbouring cues instead of allowing duplicate text.
        for idx in range(len(resolved) - 1):
            current = resolved[idx]
            following = resolved[idx + 1]
            if current is None or following is None or current.end <= following.start - min_gap:
                continue
            boundary = max(current.start + min_duration, (current.end + following.start) / 2.0)
            boundary = min(boundary, following.end - min_duration)
            if boundary <= current.start or boundary >= following.end:
                resolved[idx] = None
                continue
            resolved[idx] = window_type(
                start=current.start,
                end=round(boundary - (min_gap / 2.0), 3),
                confidence=current.confidence,
            )
            resolved[idx + 1] = window_type(
                start=round(boundary + (min_gap / 2.0), 3),
                end=following.end,
                confidence=following.confidence,
            )
        return resolved

    @staticmethod
    def _retime_segments(
        text_segments: Optional[List[TranscriptSegment]],
        timing_segments: Optional[List[TranscriptSegment]],
    ) -> Optional[List[TranscriptSegment]]:
        if not text_segments or not timing_segments or len(text_segments) != len(timing_segments):
            return text_segments
        return [
            TranscriptSegment(start=timing.start, end=timing.end, text=text.text)
            for text, timing in zip(text_segments, timing_segments)
        ]

    @staticmethod
    def _pad_visual_segments(
        segments: List[TranscriptSegment],
        audio_duration: float,
        padding: float,
    ) -> List[TranscriptSegment]:
        if not segments or padding <= 0:
            return segments

        padded = [
            TranscriptSegment(
                start=max(0.0, s.start - padding),
                end=min(audio_duration, s.end + padding),
                text=s.text,
            )
            for s in sorted(segments, key=lambda item: item.start)
        ]

        for idx in range(1, len(padded)):
            previous = padded[idx - 1]
            current = padded[idx]
            if previous.end <= current.start:
                continue
            boundary = (previous.end + current.start) / 2.0
            padded[idx - 1] = TranscriptSegment(
                start=previous.start,
                end=max(previous.start, boundary),
                text=previous.text,
            )
            padded[idx] = TranscriptSegment(
                start=min(current.end, boundary),
                end=current.end,
                text=current.text,
            )
        return padded

    def _maybe_apply_audio_sync_offset(
        self,
        segments: Optional[List[TranscriptSegment]],
        audio_duration: float,
    ) -> Optional[List[TranscriptSegment]]:
        if not segments:
            return segments
        estimate = self._last_subtitle_alignment_estimate
        if estimate is None:
            return segments
        offset = float(getattr(estimate, "offset", 0.0) or 0.0)
        apply_after = getattr(estimate, "apply_after", None)
        if apply_after not in (None, 0, 0.0):
            return segments
        if abs(offset) < self.config.min_subtitle_alignment_offset or abs(offset) > self.config.max_audio_sync_offset:
            return segments
        self.logger.info(
            "LocalizationService: applying small global audio sync offset %.2fs to TTS timeline",
            offset,
        )
        return [
            TranscriptSegment(
                start=max(0.0, min(audio_duration, s.start + offset)),
                end=max(0.0, min(audio_duration, s.end + offset)),
                text=s.text,
            )
            for s in segments
        ]

    def _align_source_segments_to_burned_subtitles(
        self,
        video_path: Optional[Path],
        source_segments: List[TranscriptSegment],
        detected_language: Optional[str],
        audio_duration: float,
    ) -> List[TranscriptSegment]:
        """Shift ASR segment timing to match burned-in subtitle timing.

        Whisper timestamps are anchored to audio. That is usually correct,
        but some short-drama/reup videos have a burned-in subtitle layer that
        appears later or earlier than the audio transcript. Since the render
        covers/replaces the burned-in text, the viewer-visible timeline must
        match that text layer. We estimate one whole-video offset via OCR and
        apply it to every segment; if OCR cannot prove a reliable offset, the
        original ASR timing is kept.
        """
        if not source_segments:
            return source_segments

        # Apply global offset if configured (when OCR alignment is disabled)
        if self.config.global_subtitle_offset != 0.0:
            offset = self.config.global_subtitle_offset
            self.logger.info(
                "LocalizationService: applying global subtitle offset %.2fs",
                offset,
            )
            return [
                TranscriptSegment(
                    start=max(0.0, min(audio_duration, s.start + offset)),
                    end=max(0.0, min(audio_duration, s.end + offset)),
                    text=s.text,
                )
                for s in source_segments
            ]

        if (
            not self.config.align_to_burned_subtitles
            or not self.config.enable_text_cover
            or not video_path
        ):
            return source_segments

        if self.text_detector is not None:
            detector = self.text_detector
        else:
            resolved_ocr_languages = ocr_language_map.resolve_ocr_languages(
                self.config.ocr_languages, detected_language
            )
            detector = OnScreenTextDetector(languages=resolved_ocr_languages)

        if self.config.use_source_subtitle_timing:
            detect_windows = getattr(type(detector), "detect_subtitle_windows_for_segments", None)
            if callable(detect_windows):
                try:
                    windows = detector.detect_subtitle_windows_for_segments(
                        video_path,
                        [(s.start, s.end, s.text) for s in source_segments],
                        audio_duration=audio_duration,
                        search_radius=self.config.source_subtitle_timing_search_radius,
                        step=self.config.source_subtitle_timing_step,
                    )
                except Exception as exc:
                    self.logger.warning(
                        "Per-cue burned-subtitle timing detection failed; falling back to global offset: %s",
                        exc,
                    )
                else:
                    if len(windows) != len(source_segments):
                        self.logger.warning(
                            "Per-cue burned-subtitle timing returned %d window(s) for %d segment(s); "
                            "falling back to global offset",
                            len(windows),
                            len(source_segments),
                        )
                        windows = []
                    detected = [window for window in windows if window is not None]
                    coverage = len(detected) / len(source_segments) if source_segments else 0.0

                    # Validate that source timing roughly matches visual timing
                    if self.config.validate_source_timing_match and detected:
                        validation_count = min(self.config.source_timing_validation_segments, len(source_segments))
                        valid_matches = 0
                        for i in range(validation_count):
                            if windows[i] is not None:
                                valid_matches += 1
                        validation_ratio = valid_matches / validation_count if validation_count > 0 else 0.0
                        if validation_ratio < 0.5:
                            self.logger.warning(
                                "Source timing validation failed: only %d/%d of first segments found visual matches; "
                                "falling back to visual-only detection",
                                valid_matches,
                                validation_count,
                            )
                            windows = []
                            detected = []

                    if detected and coverage >= self.config.source_subtitle_timing_min_coverage:
                        windows = self._fill_missing_subtitle_windows(
                            source_segments,
                            windows,
                            audio_duration,
                        )
                        resolved_count = sum(1 for window in windows if window is not None)
                        self._used_source_subtitle_timing = True
                        self.logger.info(
                            "LocalizationService: using one canonical visual subtitle clock for %d/%d "
                            "segment(s) (%d direct OCR anchor(s))",
                            resolved_count,
                            len(source_segments),
                            len(detected),
                        )
                        self._progress(42, "Đã bắt timing phụ đề gốc theo từng câu")
                        return [
                            TranscriptSegment(
                                start=window.start if window is not None else s.start,
                                end=window.end if window is not None else s.end,
                                text=s.text,
                            )
                            for s, window in zip(source_segments, windows)
                        ]
                    self.logger.info(
                        "LocalizationService: per-cue subtitle timing coverage %.0f%% below threshold %.0f%%; "
                        "falling back to global offset",
                        coverage * 100.0,
                        self.config.source_subtitle_timing_min_coverage * 100.0,
                    )

        try:
            estimate = detector.estimate_subtitle_time_offset(
                video_path,
                [(s.start, s.end, s.text) for s in source_segments],
                search_radius=self.config.max_subtitle_alignment_offset,
                exclude_regions_fractional=self.config.watermark_exclude_regions_fractional,
                min_offset=self.config.min_subtitle_alignment_offset,
            )
        except Exception as exc:
            self.logger.warning("Burned-subtitle timing alignment failed; keeping ASR timing: %s", exc)
            return source_segments

        if estimate is None or abs(estimate.offset) < self.config.min_subtitle_alignment_offset:
            return source_segments
        if estimate.confidence < self.config.min_subtitle_alignment_confidence:
            self.logger.info(
                "LocalizationService: skipping subtitle offset %.2fs due to low confidence %.2f (threshold=%.2f)",
                estimate.offset, estimate.confidence, self.config.min_subtitle_alignment_confidence,
            )
            return source_segments
        self._last_subtitle_alignment_estimate = estimate

        self.logger.info(
            "LocalizationService: shifting %d segment timestamp(s) by %.2fs to match burned-in subtitles "
            "(OCR confidence=%.2f, matches=%d)",
            len(source_segments), estimate.offset, estimate.confidence, estimate.matches,
        )
        self._progress(
            42,
            f"Đã căn lại timing phụ đề nguồn ({estimate.offset:+.1f}s)",
        )
        return [
            TranscriptSegment(
                start=max(0.0, min(audio_duration, s.start + self._segment_alignment_offset(s, estimate))),
                end=max(0.0, min(audio_duration, s.end + self._segment_alignment_offset(s, estimate))),
                text=s.text,
            )
            for s in source_segments
        ]

    @staticmethod
    def _segment_alignment_offset(segment: TranscriptSegment, estimate) -> float:
        apply_after = getattr(estimate, "apply_after", None)
        if apply_after is not None and segment.start < apply_after:
            return 0.0
        return estimate.offset

    async def _finalize(
        self,
        prepared: "PreparedLocalization",
        edited_segments: Optional[List[TranscriptSegment]] = None,
    ) -> LocalizationResult:
        """Steps 4-8: TTS, subtitles, on-screen text-cover, audio mix, and
        final render — resuming from whatever `_prepare()` produced. See
        `localize()`."""
        download_result = prepared.download_result
        audio_result = prepared.audio_result
        source_segments = prepared.source_segments
        output_dir = prepared.output_dir
        effective_target_language = prepared.target_language

        if edited_segments is not None:
            translated_segments = edited_segments
            translated_text = " ".join(s.text for s in translated_segments if s.text)
            tts_segments = self._retime_segments(translated_segments, prepared.tts_segments) or translated_segments
        else:
            translated_segments = prepared.translated_segments
            translated_text = prepared.translated_text
            tts_segments = prepared.tts_segments or translated_segments

        visual_timing_padding = self._visual_timing_padding_for_current_video()
        self.logger.debug(
            "LocalizationService._finalize: source_segments=%s",
            [(s.start, s.end) for s in (source_segments or [])[:3]]  # First 3 segments
        )
        visual_translated_segments = self._pad_visual_segments(
            translated_segments or [],
            audio_duration=audio_result.audio_result.duration,
            padding=visual_timing_padding,
        ) if translated_segments else None
        visual_source_segments = self._pad_visual_segments(
            source_segments,
            audio_duration=audio_result.audio_result.duration,
            padding=visual_timing_padding,
        )
        self.logger.debug(
            "LocalizationService._finalize: visual_source_segments=%s visual_translated_segments=%s",
            [(s.start, s.end) for s in (visual_source_segments or [])[:3]] if visual_source_segments else None,
            [(s.start, s.end) for s in (visual_translated_segments or [])[:3]] if visual_translated_segments else None
        )

        tts_audio_path: Optional[Path] = None
        self._last_tts_playback_segments = None

        # Step 4: Synthesize TTS from translated text, anchored to the
        # original sentence timestamps whenever we have them.
        if self.config.run_tts and (translated_segments or translated_text):
            self._progress(62, "Đang tạo giọng đọc...")
            if self.tts_service is None:
                self.logger.warning("TTS requested but no TTSService injected; skipping")
            else:
                try:
                    if tts_segments:
                        tts_audio_path = await self._synthesize_timed_track_async(
                            tts_segments,
                            total_duration=audio_result.audio_result.duration,
                            output_dir=output_dir,
                            target_language=effective_target_language,
                            voice=self.config.tts_voice,
                        )
                    else:
                        self.logger.info("LocalizationService: synthesizing TTS (whole-text fallback)")
                        tts_audio_path = output_dir / "tts_audio.wav"
                        await asyncio.to_thread(
                            self.tts_service.synthesize,
                            translated_text,
                            output_path=tts_audio_path,
                            language=effective_target_language,
                            voice=self.config.tts_voice,
                        )
                    self.logger.info("LocalizationService: TTS complete: %s", tts_audio_path)
                    self._progress(72, "Đã tạo giọng đọc ✓")
                except Exception as exc:
                    self.logger.error("TTS synthesis failed: %s", exc)
                    # Never silently deliver a "dubbed" reup with no dub.
                    # Surface the failure so the job is refundable/retryable.
                    raise RuntimeError(f"Không tạo được giọng đọc TTS: {exc}") from exc

        # Step 5: Generate subtitles — directly from real timestamps when available.
        subtitle_segments: Optional[List[TimelineSegment]] = None
        subtitles_path: Optional[Path] = None
        if self.config.generate_subtitles and (translated_segments or translated_text or audio_result.transcript):
            self._progress(75, "Đang tạo phụ đề...")
            self.logger.info("LocalizationService: generating subtitles")

            # Check if visual_source_segments have detected subtitle timing (from OCR/burned-in subtitles)
            source_min_start = min((s.start for s in visual_source_segments), default=0.0) if visual_source_segments else 0.0
            has_detected_source_timing = source_min_start > 0.01  # Reduced from 0.05 to catch subtitles starting near 0s

            self.logger.debug(
                "LocalizationService: subtitle generation - visual_translated_segments=%s, "
                "source_min_start=%.3f, has_detected_source_timing=%s",
                "None" if visual_translated_segments is None else f"len={len(visual_translated_segments)}",
                source_min_start,
                has_detected_source_timing
            )

            playback_segments = getattr(self, "_last_tts_playback_segments", None)
            if playback_segments:
                # Karaoke and voice must share one measured playback clock.
                subtitle_segments = self.timeline.from_segments(
                    playback_segments,
                    audio_duration=max(
                        audio_result.audio_result.duration,
                        max((s.end for s in playback_segments), default=audio_result.audio_result.duration),
                    ),
                )
            elif visual_translated_segments:
                # Translated segments already have offset applied via _retime_segments() in _prepare()
                subtitle_segments = self.timeline.from_segments(
                    visual_translated_segments, audio_duration=audio_result.audio_result.duration
                )
            elif has_detected_source_timing:
                # Use source segments if translated segments are missing
                # Source segments have offset from OCR detection
                subtitle_segments = self.timeline.from_segments(
                    visual_source_segments, audio_duration=audio_result.audio_result.duration
                )
            else:
                # Fallback: even-split heuristic over whichever text we have
                # (prefer the translated text so subtitles are in the target
                # language, not the original).
                text_for_subs = translated_text or audio_result.transcript
                subtitle_segments = self.timeline.align_transcript(
                    text_for_subs, audio_result.audio_result.duration
                )

            if subtitle_segments:
                first_start = min((s.start_time for s in subtitle_segments), default=0.0)
                self.logger.info(
                    "LocalizationService: generated %d subtitle segments (first segment starts at %.3fs)",
                    len(subtitle_segments), first_start
                )
            else:
                self.logger.info("LocalizationService: generated 0 subtitle segments")

            subtitles_path = output_dir / "subtitles.ass"
            dimensions = (
                self.renderer._get_video_dimensions(download_result.video_path)
                if self.renderer and download_result.video_path else None
            )
            frame_width, frame_height = dimensions or (1080, 1920)
            ass_content = self.timeline.generate_ass_karaoke(
                subtitle_segments, frame_width=frame_width, frame_height=frame_height,
            )
            subtitles_path.write_text(ass_content, encoding="utf-8")
            self.logger.info("LocalizationService: subtitles written to %s", subtitles_path)
            self._progress(79, "Đã tạo phụ đề ✓")

        # Step 6: Detect + build on-screen text-cover overlays (best-effort).
        text_overlays: Optional[List[TextOverlay]] = None
        if (
            self.config.enable_text_cover
            and visual_translated_segments
            and visual_source_segments
            and download_result.video_path
        ):
            text_overlays = self._build_text_overlays(
                video_path=download_result.video_path,
                source_segments=visual_source_segments,
                translated_segments=visual_translated_segments,
                detected_language=audio_result.detected_language,
            )
            if text_overlays and subtitles_path and subtitle_segments:
                common_font_size = text_overlays[0].font_size or 48
                positions = None
                font_size = None
                if self.config.place_subtitles_in_text_cover_boxes:
                    positions = {
                        (round(overlay.start, 3), round(overlay.end, 3)): (
                            round(overlay.x + overlay.width / 2),
                            round(overlay.y + overlay.height / 2),
                        )
                        for overlay in text_overlays
                    }
                    font_size = common_font_size
                subtitles_path.write_text(
                    self.timeline.generate_ass_karaoke(
                        subtitle_segments,
                        frame_width=frame_width,
                        frame_height=frame_height,
                        positions=positions,
                        font_size=font_size,
                    ),
                    encoding="utf-8",
                )
                # The FFmpeg overlays now only cover the original pixels;
                # drawing their text too would duplicate the ASS captions.
                text_overlays = [
                    TextOverlay(
                        start=o.start, end=o.end, x=o.x, y=o.y,
                        width=o.width, height=o.height, text="",
                        box_color=o.box_color, font_color=o.font_color,
                        font_path=o.font_path, font_size=common_font_size,
                    )
                    for o in text_overlays
                ]

        # Step 7: Mix audio. In copyright-safe mode the downloaded source
        # audio is never reintroduced; only the dub and a separately licensed
        # replacement track are used.
        mixed_audio_path: Optional[Path] = None
        if self.config.mix_audio and tts_audio_path:
            self._progress(83, "Đang phối âm thanh...")
            self.logger.info("LocalizationService: mixing audio streams")
            if self.config.replace_source_audio:
                replacement_track = (
                    self.background_music_library.select_like(
                        audio_result.audio_result.audio_path,
                        selection_key=str(download_result.video_path),
                    )
                    if self.background_music_library is not None else None
                )
                source_effects_bed: Optional[Path] = None
                if audio_result.demucs_output is not None:
                    source_effects_bed = output_dir / "source_effects_bed.wav"
                    self.mixer.build_source_effects_bed(
                        [
                            audio_result.demucs_output.drums,
                            audio_result.demucs_output.bass,
                            audio_result.demucs_output.other,
                        ],
                        total_duration=audio_result.audio_result.duration,
                        output_path=source_effects_bed,
                        volume=self.config.source_effects_volume,
                    )
                else:
                    self.logger.warning(
                        "replace_source_audio is enabled but Demucs stems are unavailable; "
                        "not mixing source audio because it would keep the original voice."
                    )

                mixed_audio_path = output_dir / "audio_safe_mix.wav"
                if source_effects_bed is not None:
                    self.mixer.mix_dub_with_source_and_background(
                        DubbedSourceBackgroundMix(
                            voice_audio=tts_audio_path,
                            source_audio=source_effects_bed,
                            background_audio=replacement_track,
                            total_duration=audio_result.audio_result.duration,
                            source_volume=1.0,
                            background_volume=self.config.replacement_music_volume,
                        ),
                        mixed_audio_path,
                    )
                elif replacement_track is not None:
                    self.mixer.mix_dub_with_background(
                        DubbedBackgroundMix(
                            voice_audio=tts_audio_path,
                            background_audio=replacement_track,
                            total_duration=audio_result.audio_result.duration,
                            background_volume=self.config.replacement_music_volume,
                        ),
                        mixed_audio_path,
                    )
                else:
                    mixed_audio_path = tts_audio_path
            else:
                mixed_audio_path = output_dir / "audio_mixed.wav"
                self.mixer.mix(
                    AudioMix(
                        primary_audio=audio_result.audio_result.audio_path,
                        secondary_audio=tts_audio_path,
                        mix_level=self.config.dub_mix_level_primary,
                    ),
                    mixed_audio_path
                )
            self.logger.info("LocalizationService: audio mix complete: %s", mixed_audio_path)
            self._progress(87, "Đã phối âm thanh ✓")

        # Step 8: Render final video (merge video + audio + optional subtitles/overlays)
        final_video_path: Optional[Path] = None
        if self.config.render_video and download_result.video_path:
            self._progress(90, "Đang render video cuối...")
            if self.renderer is None:
                self.logger.warning("Rendering requested but no Renderer available; skipping")
            else:
                self.logger.info("LocalizationService: rendering final video")
                try:
                    # Use mixed audio if available, otherwise use TTS audio, otherwise use original audio
                    audio_for_render = mixed_audio_path or tts_audio_path or audio_result.audio_result.audio_path
                    static_text_boxes = self._detect_static_text_watermark_boxes(
                        video_path=download_result.video_path,
                        detected_language=audio_result.detected_language,
                        duration=audio_result.audio_result.duration,
                    )
                    if static_text_boxes:
                        existing_boxes = tuple(self.renderer.config.watermark_boxes_fractional or ())
                        self.renderer.config = replace(
                            self.renderer.config,
                            watermark_boxes_fractional=existing_boxes + tuple(static_text_boxes),
                        )

                    # Avoid double-showing the translation: where an
                    # OCR-based text_overlay successfully covers the
                    # original on-screen text and draws the translation in
                    # its place, we don't ALSO want the .srt burning a
                    # second copy of the same sentence at the bottom.
                    #
                    # But OCR detection is best-effort and doesn't always
                    # find a box for every sentence (e.g. low-contrast
                    # frames) — a text_overlay list that's missing some
                    # sentences used to mean those sentences got NO
                    # translated caption at all (since we skipped the whole
                    # .srt whenever ANY overlay existed), which showed up as
                    # "subtitle lúc có lúc không" (translated captions
                    # randomly appearing/disappearing). Instead, we now keep
                    # only the .srt cues that AREN'T already covered by an
                    # overlay, so every sentence gets exactly one rendering:
                    # the in-place overlay where OCR succeeded, or the
                    # bottom .srt caption as a fallback where it didn't —
                    # never neither.
                    animated_config = (
                        self.renderer.config.animated_subtitle_config
                        if self.renderer and self.renderer.config else None
                    )
                    use_ass_karaoke = bool(
                        animated_config
                        and animated_config.enabled
                        and animated_config.effect == SubtitleEffect.KARAOKE
                    )

                    subtitles_for_render = subtitles_path
                    if (
                        not use_ass_karaoke
                        and self.config.skip_srt_when_text_overlays_present
                        and subtitles_path is not None
                        and text_overlays
                        and subtitle_segments
                    ):
                        uncovered = self._filter_uncovered_subtitle_segments(
                            subtitle_segments, text_overlays
                        )
                        if len(uncovered) == len(subtitle_segments):
                            # Overlays didn't actually cover any subtitle
                            # segment's time range (shouldn't normally
                            # happen) — keep the original full .srt.
                            subtitles_for_render = subtitles_path
                        elif uncovered:
                            gap_srt_path = output_dir / "subtitles_gap_fill.ass"
                            gap_srt_path.write_text(
                                self.timeline.generate_ass_karaoke(
                                    uncovered, frame_width=frame_width, frame_height=frame_height,
                                ), encoding="utf-8"
                            )
                            subtitles_for_render = gap_srt_path
                            self.logger.info(
                                "LocalizationService: %d/%d subtitle segment(s) not covered by "
                                "a text_overlay; burning them as a gap-fill ASS so no sentence "
                                "is left without a translated caption",
                                len(uncovered), len(subtitle_segments),
                            )
                        else:
                            # Every segment is covered by an overlay — no
                            # need for the .srt at all.
                            self.logger.info(
                                "LocalizationService: all %d subtitle segment(s) covered by "
                                "text_overlays; skipping separate .srt burn",
                                len(text_overlays),
                            )
                            subtitles_for_render = None

                    final_video_path = output_dir / "output_final.mp4"

                    # Prepare subtitle segments for drawtext-based animated
                    # effects. Karaoke is special: the ASS subtitle file we
                    # generated above already contains real \kf karaoke timing,
                    # while the drawtext karaoke fallback only renders a
                    # static full-color line. Keep ASS for karaoke and only use
                    # drawtext for the other animation effects.
                    subtitle_segments_for_render = None
                    if animated_config and animated_config.enabled and not use_ass_karaoke:
                        subtitle_segments_for_render = [
                            {"text": seg.text, "start": seg.start_time, "end": seg.end_time}
                            for seg in subtitle_segments
                        ] if subtitle_segments else None
                        if subtitle_segments_for_render:
                            if subtitles_for_render is not None:
                                self.logger.info(
                                    "LocalizationService: animated subtitles enabled; skipping separate "
                                    "ASS/SRT burn to avoid duplicate subtitle layers"
                            )
                            subtitles_for_render = None
                    elif use_ass_karaoke and subtitles_for_render is not None:
                        self.logger.info(
                            "LocalizationService: karaoke subtitles enabled; using ASS karaoke "
                            "burn-in and text-cover boxes, not static drawtext subtitles"
                        )

                    async with _RENDER_SLOTS:
                        await asyncio.to_thread(
                            self.renderer.render,
                            video_path=download_result.video_path,
                            audio_path=audio_for_render,
                            subtitles=subtitles_for_render,
                            output_path=final_video_path,
                            text_overlays=text_overlays,
                            subtitle_segments=subtitle_segments_for_render,
                        )
                    self._progress(98, "Đã render ✓")
                    self.logger.info("LocalizationService: render complete: %s", final_video_path)
                except Exception as exc:
                    self.logger.error("Rendering failed: %s", exc)
                    final_video_path = None

        return LocalizationResult(
            download_result=download_result,
            audio_pipeline_result=audio_result,
            translated_text=translated_text,
            source_segments=source_segments,
            translated_segments=translated_segments,
            tts_segments=tts_segments,
            tts_audio_path=tts_audio_path,
            subtitle_segments=subtitle_segments,
            mixed_audio_path=mixed_audio_path,
            text_overlays=text_overlays,
            final_video_path=final_video_path,
        )


    @staticmethod
    def _wav_duration(path: Path) -> float:
        """Return real media duration even when a provider writes MP3/AAC bytes
        to a file named ``.wav``.

        Several TTS backends do exactly that.  ``wave.open`` then returns 0,
        causing the mixer to trust the nominal subtitle window and trim the
        final syllables.  Probe with ffprobe first and keep the stdlib WAV
        reader as a dependency-free fallback.
        """
        try:
            result = subprocess.run(
                [
                    "ffprobe", "-v", "error", "-show_entries", "format=duration",
                    "-of", "default=noprint_wrappers=1:nokey=1", str(path),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            duration = float((result.stdout or "").strip())
            if duration > 0.0:
                return duration
        except Exception:
            pass
        try:
            with wave.open(str(path), "rb") as wav_file:
                rate = wav_file.getframerate()
                frames = wav_file.getnframes()
                return (frames / rate) if rate else 0.0
        except Exception:
            return 0.0

    @staticmethod
    def _atempo_chain(speed: float) -> str:
        speed = max(0.01, float(speed))
        parts: List[float] = []
        while speed > 2.0:
            parts.append(2.0)
            speed /= 2.0
        while speed < 0.5:
            parts.append(0.5)
            speed /= 0.5
        parts.append(speed)
        return ",".join(f"atempo={value:.6f}" for value in parts)

    def _fit_tts_clip_to_window(
        self,
        clip_path: Path,
        *,
        start: float,
        nominal_end: float,
        next_start: Optional[float],
        total_duration: float,
    ) -> TimedAudioClip:
        actual_duration = self._wav_duration(clip_path)
        if actual_duration <= 0.0:
            return TimedAudioClip(start=start, end=nominal_end, audio_path=clip_path)

        available_end = min(total_duration, next_start - self.config.tts_neighbor_gap_seconds) if next_start is not None else total_duration
        available_duration = max(0.05, available_end - start)
        speed = 1.0
        fitted_path = clip_path
        if self.config.tts_fit_real_duration and actual_duration > available_duration + 0.02:
            required_speed = actual_duration / available_duration
            speed = min(max(1.0, required_speed), max(1.0, self.config.tts_max_speed_ratio))
            if speed > 1.01:
                candidate = clip_path.with_name(f"{clip_path.stem}_fit{clip_path.suffix}")
                command = [
                    "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-i", str(clip_path), "-filter:a", self._atempo_chain(speed), str(candidate),
                ]
                try:
                    subprocess.run(command, check=True, capture_output=True, text=True)
                    if candidate.exists() and candidate.stat().st_size > 0:
                        fitted_path = candidate
                        actual_duration = self._wav_duration(candidate) or (actual_duration / speed)
                except Exception as exc:
                    self.logger.warning("Could not time-fit TTS clip %s: %s", clip_path, exc)

        # Critical invariant: never advertise an end earlier than the actual
        # audio. Mixer implementations commonly atrim to TimedAudioClip.end;
        # using the measured duration prevents final syllables being lost.
        real_end = min(total_duration, start + actual_duration)
        if real_end > available_end + 0.01:
            self.logger.warning(
                "TTS clip %s exceeds its free slot by %.3fs after %.3fx fit; preserving the full clip instead of truncating it",
                clip_path.name, real_end - available_end, speed,
            )
        return TimedAudioClip(start=start, end=max(start + 0.05, real_end), audio_path=fitted_path)

    def _schedule_tts_clips(
        self,
        synthesized: List[Tuple[int, TranscriptSegment, Path]],
        *,
        total_duration: float,
    ) -> Tuple[List[TimedAudioClip], List[TranscriptSegment]]:
        """Create a non-truncating speech schedule and its karaoke timeline.

        The source subtitle windows are anchors, not hard audio cut points.
        Generated speech is measured from the actual media files, compressed
        only as much as necessary, and later clips are shifted forward instead
        of overlapping or losing their last words.  The returned transcript
        segments are the single timing source for ASS karaoke.
        """
        items = []
        for idx, seg, clip_path in synthesized:
            duration = self._wav_duration(clip_path)
            if duration <= 0.0:
                self.logger.warning("Could not measure TTS segment %s; skipping it", clip_path)
                continue
            items.append((idx, seg, clip_path, duration))

        clips: List[TimedAudioClip] = []
        playback: List[TranscriptSegment] = []
        previous_end = 0.0
        gap = max(0.0, float(self.config.tts_neighbor_gap_seconds))
        max_speed = max(1.0, float(self.config.tts_max_speed_ratio))

        for pos, (idx, seg, clip_path, raw_duration) in enumerate(items):
            anchor_start = seg.start if seg.has_timing else previous_end
            start = max(anchor_start, previous_end + (gap if clips else 0.0))

            remaining_raw = sum(item[3] for item in items[pos:])
            remaining_gaps = gap * max(0, len(items) - pos - 1)
            remaining_time = max(0.10, total_duration - start - remaining_gaps)
            global_required_speed = remaining_raw / remaining_time

            next_anchor = None
            for _later_idx, later_seg, _later_path, _later_duration in items[pos + 1:]:
                if later_seg.has_timing:
                    next_anchor = later_seg.start
                    break
            if next_anchor is not None:
                local_available = max(0.10, next_anchor - start - gap)
                local_required_speed = raw_duration / local_available
            else:
                local_required_speed = 1.0

            speed = min(max_speed, max(1.0, global_required_speed, local_required_speed))
            fitted_path = clip_path
            fitted_duration = raw_duration
            if speed > 1.01:
                candidate = clip_path.with_name(f"{clip_path.stem}_fit{clip_path.suffix}")
                try:
                    subprocess.run(
                        [
                            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                            "-i", str(clip_path), "-filter:a", self._atempo_chain(speed),
                            "-c:a", "pcm_s16le", str(candidate),
                        ],
                        check=True,
                        capture_output=True,
                        text=True,
                    )
                    measured = self._wav_duration(candidate)
                    if measured > 0.0:
                        fitted_path = candidate
                        fitted_duration = measured
                except Exception as exc:
                    self.logger.warning("Could not fit TTS segment %s: %s", clip_path, exc)

            end = start + fitted_duration
            if end > total_duration:
                # Never trim the waveform.  Log the overflow so the caller can
                # choose a shorter translation or a longer visual timeline.
                self.logger.warning(
                    "TTS schedule exceeds video duration by %.3fs at segment %d; preserving full speech",
                    end - total_duration,
                    idx,
                )
            clips.append(TimedAudioClip(start=start, end=end, audio_path=fitted_path))
            playback.append(TranscriptSegment(start=start, end=end, text=seg.text))
            previous_end = end

        return clips, playback

    def _synthesize_timed_track(
        self,
        translated_segments: List[TranscriptSegment],
        total_duration: float,
        output_dir: Path,
        target_language: str,
        voice: Optional[str] = None,
    ) -> Path:
        """
        Synthesize each translated sentence separately, then assemble them
        onto one track anchored at each sentence's original start time.

        This is what makes the dubbed voice say, at second 0-3, the
        translation of whatever was said at second 0-3 in the source video
        — rather than reading the whole translated transcript back-to-back
        starting from second 0.

        Legacy synchronous implementation - kept for backward compatibility.
        """
        segments_dir = output_dir / "tts_segments"
        segments_dir.mkdir(parents=True, exist_ok=True)

        clips: List[TimedAudioClip] = []
        for idx, seg in enumerate(translated_segments):
            self._raise_if_cancelled()
            if not seg.text.strip():
                continue
            clip_path = segments_dir / f"segment_{idx:04d}.wav"
            try:
                self.tts_service.synthesize(
                    seg.text,
                    output_path=clip_path,
                    language=target_language,
                    voice=voice,
                )
            except Exception as exc:
                self.logger.warning(
                    "Skipping TTS for segment %d after synthesis failed; subtitles remain: %s",
                    idx,
                    exc,
                )
                continue
            if seg.has_timing:
                next_start = next((item.start for item in translated_segments[idx + 1:] if item.has_timing), None)
                clips.append(self._fit_tts_clip_to_window(
                    clip_path, start=seg.start, nominal_end=seg.end,
                    next_start=next_start, total_duration=total_duration,
                ))
            else:
                prev_end = clips[-1].end if clips else 0.0
                clips.append(self._fit_tts_clip_to_window(
                    clip_path, start=prev_end, nominal_end=prev_end + 2.0,
                    next_start=None, total_duration=total_duration,
                ))

        tts_audio_path = output_dir / "tts_audio.wav"
        self.mixer.build_dubbed_track(clips, total_duration=total_duration, output_path=tts_audio_path)
        return tts_audio_path

    async def _synthesize_timed_track_async(
        self,
        translated_segments: List[TranscriptSegment],
        total_duration: float,
        output_dir: Path,
        target_language: str,
        voice: Optional[str] = None,
    ) -> Path:
        """
        Async version of _synthesize_timed_track with parallel TTS synthesis.

        Optimized with parallel TTS synthesis using asyncio and concurrency control.
        This can significantly reduce TTS processing time for videos with many segments.
        """
        segments_dir = output_dir / "tts_segments"
        segments_dir.mkdir(parents=True, exist_ok=True)

        async def synthesize_segment(idx: int, seg: TranscriptSegment) -> Optional[Tuple[int, TranscriptSegment, Path]]:
            """Synthesize a single segment with concurrency control."""
            self._raise_if_cancelled()
            if not seg.text.strip():
                return None
            clip_path = segments_dir / f"segment_{idx:04d}.wav"
            try:
                async with _TTS_SLOTS:
                    await asyncio.to_thread(
                        self.tts_service.synthesize,
                        seg.text,
                        output_path=clip_path,
                        language=target_language,
                        voice=voice,
                    )
            except Exception as exc:
                self.logger.warning(
                    "Skipping TTS for segment %d after synthesis failed; subtitles remain: %s",
                    idx,
                    exc,
                )
                return None

            return idx, seg, clip_path

        # Run TTS synthesis in parallel with concurrency control
        tasks = [
            synthesize_segment(idx, seg)
            for idx, seg in enumerate(translated_segments)
        ]
        results = await asyncio.gather(*tasks)

        completed = [item for item in results if item is not None]
        clips, playback_segments = self._schedule_tts_clips(
            completed,
            total_duration=total_duration,
        )
        self._last_tts_playback_segments = playback_segments

        tts_audio_path = output_dir / "tts_audio.wav"
        mix_duration = max(total_duration, clips[-1].end if clips else total_duration)
        self.mixer.build_dubbed_track(clips, total_duration=mix_duration, output_path=tts_audio_path)
        return tts_audio_path

    @staticmethod
    def _filter_uncovered_subtitle_segments(
        subtitle_segments: List[TimelineSegment],
        text_overlays: List[TextOverlay],
        min_overlap_ratio: float = 0.5,
    ) -> List[TimelineSegment]:
        """Return only the subtitle segments that are NOT already shown via
        an in-place text_overlay, so the .srt can fill in just the gaps
        left by OCR misses instead of either duplicating every overlay'd
        sentence or going dark for every non-overlay'd one."""
        uncovered: List[TimelineSegment] = []
        for seg in subtitle_segments:
            seg_duration = max(1e-6, seg.end_time - seg.start_time)
            covered = False
            for overlay in text_overlays:
                overlap = min(seg.end_time, overlay.end) - max(seg.start_time, overlay.start)
                if overlap > 0 and (overlap / seg_duration) >= min_overlap_ratio:
                    covered = True
                    break
            if not covered:
                uncovered.append(seg)
        return uncovered

    def _visual_timing_padding_for_current_video(self) -> float:
        estimate = self._last_subtitle_alignment_estimate
        if estimate is None:
            return 0.0
        offset = abs(float(getattr(estimate, "offset", 0.0) or 0.0))
        if offset < self.config.min_subtitle_alignment_offset:
            return 0.0
        return max(0.0, self.config.visual_subtitle_timing_padding)

    def _detect_static_text_watermark_boxes(
        self,
        video_path: Optional[Path],
        detected_language: Optional[str],
        duration: float,
    ) -> Tuple[Tuple[float, float, float, float], ...]:
        if (
            not self.config.auto_blur_static_text
            or not self.config.enable_text_cover
            or not video_path
            or duration <= 0
        ):
            return ()
        if self.text_detector is not None:
            detector = self.text_detector
        else:
            resolved_ocr_languages = ocr_language_map.resolve_ocr_languages(
                self.config.ocr_languages, detected_language
            )
            detector = OnScreenTextDetector(languages=resolved_ocr_languages)
        try:
            boxes = detector.detect_persistent_text_regions(
                video_path,
                duration=duration,
                sample_count=self.config.static_text_blur_samples,
            )
        except Exception as exc:
            self.logger.warning("Persistent text/watermark detection failed; skipping static blur: %s", exc)
            return ()
        return tuple(boxes)


    @staticmethod
    def _fill_missing_text_regions(
        windows: List[Tuple[float, float]],
        regions: List[TextRegion],
        typical_line_height: Optional[float],
    ) -> List[TextRegion]:
        """Return one cleanup region for every subtitle window.

        OCR occasionally misses a low-contrast cue even though neighboring
        cues clearly establish the subtitle layout.  Leaving that cue without
        a region exposes the burned-in source text.  This method fills only
        missing windows using time-local detected geometry.  It never assumes
        a fixed screen position: the nearest preceding/following detections
        determine the fallback, and interpolation is used only when both sides
        belong to the same learned vertical band.
        """
        if not windows or not regions:
            return list(regions)

        by_window = {
            (round(region.start, 3), round(region.end, 3)): region
            for region in regions
        }
        detected = sorted(regions, key=lambda r: ((r.start + r.end) / 2.0))
        typical_h = max(1.0, float(typical_line_height or 0.0))
        if typical_h <= 1.0:
            hs = sorted(r.height for r in detected)
            typical_h = float(hs[len(hs) // 2]) if hs else 1.0

        output: List[TextRegion] = []
        for start, end in windows:
            key = (round(start, 3), round(end, 3))
            exact = by_window.get(key)
            if exact is not None:
                output.append(exact)
                continue

            midpoint = (start + end) / 2.0
            before = [r for r in detected if ((r.start + r.end) / 2.0) <= midpoint]
            after = [r for r in detected if ((r.start + r.end) / 2.0) > midpoint]
            left = before[-1] if before else None
            right = after[0] if after else None

            chosen: Optional[TextRegion] = None
            if left is not None and right is not None:
                left_mid = (left.start + left.end) / 2.0
                right_mid = (right.start + right.end) / 2.0
                left_cy = left.y + left.height / 2.0
                right_cy = right.y + right.height / 2.0
                same_band = abs(left_cy - right_cy) <= max(typical_h * 1.5, (left.height + right.height) * 0.55)
                if same_band and right_mid > left_mid:
                    ratio = max(0.0, min(1.0, (midpoint - left_mid) / (right_mid - left_mid)))
                    chosen = TextRegion(
                        start=start, end=end,
                        x=round(left.x + (right.x - left.x) * ratio),
                        y=round(left.y + (right.y - left.y) * ratio),
                        width=round(left.width + (right.width - left.width) * ratio),
                        height=round(left.height + (right.height - left.height) * ratio),
                    )
                else:
                    chosen = left if (midpoint - left_mid) <= (right_mid - midpoint) else right
            else:
                chosen = left or right

            if chosen is not None:
                output.append(TextRegion(
                    start=start, end=end, x=chosen.x, y=chosen.y,
                    width=chosen.width, height=chosen.height,
                ))

        return output

    def _build_text_overlays(
        self,
        video_path: Path,
        source_segments: List[TranscriptSegment],
        translated_segments: List[TranscriptSegment],
        detected_language: Optional[str] = None,
    ) -> Optional[List[TextOverlay]]:
        """
        Detect the on-screen region of each sentence's burned-in original
        text (via OCR, sampled at the ORIGINAL sentence's timestamps) and
        pair each detected region with the matching TRANSLATED sentence, so
        the cover box + translated caption appear during the exact same
        window the original text was on screen.

        Best-effort: returns None (and logs a warning) if OCR isn't available
        or detection fails, rather than failing the whole render.

        :param detected_language: language Whisper detected the spoken
            audio to be in, used to auto-pick the OCR language pack when
            `self.config.ocr_languages` is left at its default "auto"
            sentinel — see `render.ocr_language_map`.
        """
        if self.text_detector is not None:
            detector = self.text_detector
        else:
            resolved_ocr_languages = ocr_language_map.resolve_ocr_languages(
                self.config.ocr_languages, detected_language
            )
            self.logger.info(
                "LocalizationService: using OCR languages %s (configured=%s, detected_audio_language=%s)",
                resolved_ocr_languages, self.config.ocr_languages, detected_language,
            )
            detector = OnScreenTextDetector(languages=resolved_ocr_languages)
        windows = [(s.start, s.end) for s in source_segments]

        try:
            regions = detector.detect_regions_for_windows(
                video_path, windows,
                samples_per_window=self.config.text_cover_samples_per_segment,
                exclude_regions_fractional=self.config.watermark_exclude_regions_fractional,
            )
        except Exception as exc:
            self.logger.warning(
                "On-screen text detection unavailable/failed; skipping text-cover overlays: %s", exc
            )
            return None

        if not regions:
            self.logger.info("LocalizationService: no on-screen text detected; skipping text-cover overlays")
            return None

        detected_count = len(regions)
        regions = self._fill_missing_text_regions(
            windows, regions, detector.last_typical_line_height
        )
        if len(regions) > detected_count:
            self.logger.info(
                "LocalizationService: filled %d OCR-missed subtitle window(s) "
                "from neighboring adaptive regions",
                len(regions) - detected_count,
            )

        # Match detected regions back to the translated sentence at the same
        # (start, end) window. Both lists were built from the same
        # `source_segments` ordering, so a (start, end) -> translated text
        # lookup is exact.
        translation_by_window: Dict[Tuple[float, float], str] = {
            (round(src.start, 3), round(src.end, 3)): translated.text
            for src, translated in zip(source_segments, translated_segments)
        }

        # One fixed font size for every overlay in this video, sized to
        # match the ORIGINAL burned-in text's own line height (learned by
        # the detector from this video's actual OCR boxes), not derived
        # from the (possibly multi-line-inflated) cover-box height. This is
        # what keeps the translated text at a consistent, correctly-sized
        # font instead of fluctuating or oversized.
        typical_line_height = detector.last_typical_line_height
        if typical_line_height:
            fixed_font_size = max(12, int(round(typical_line_height * 0.85)))
        else:
            heights = sorted(r.height for r in regions)
            mid = len(heights) // 2
            median_height = heights[mid] if len(heights) % 2 == 1 else (heights[mid - 1] + heights[mid]) // 2
            fixed_font_size = max(12, int(median_height * 0.6))

        font_path = self.renderer.config.default_overlay_font_path if self.renderer else None
        frame_dims = detector._get_video_dimensions(video_path)
        frame_w = frame_dims[0] if frame_dims else None

        overlays: List[TextOverlay] = []
        for region in regions:
            key = (round(region.start, 3), round(region.end, 3))
            translated_text = translation_by_window.get(key)
            if not translated_text:
                continue

            # Cleanup geometry must follow the ORIGINAL source glyphs, not
            # the translated sentence width.  The ASS renderer is responsible
            # for translated text layout; widening the cleanup box to fit the
            # translation caused the large white rectangles seen in output.
            overlays.append(
                TextOverlay(
                    start=region.start,
                    end=region.end,
                    x=region.x,
                    y=region.y,
                    width=region.width,
                    height=region.height,
                    text=translated_text,
                    font_size=fixed_font_size,
                )
            )

        self.logger.info("LocalizationService: built %d text-cover overlay(s)", len(overlays))
        return overlays or None

    @staticmethod
    def _measure_text_width_px(text: str, font_size: int, font_path: Optional[str]) -> int:
        """
        Estimate the rendered pixel width of `text` at `font_size`, so the
        cover box can be widened when the translated sentence needs more
        room than the original on-screen text did (translations are often
        longer than the source). Uses exact font metrics via Pillow when a
        font file is available, falling back to a simple average-character-
        width heuristic for Latin-script text otherwise.
        """
        if font_path:
            try:
                from PIL import ImageFont
                font = ImageFont.truetype(font_path, font_size)
                bbox = font.getbbox(text)
                return int(bbox[2] - bbox[0])
            except Exception:
                pass
        # Heuristic fallback: average glyph advance for a sans-serif Latin
        # font (incl. Vietnamese diacritics) is roughly 0.55x the font size.
        return int(len(text) * font_size * 0.55)

    def _fit_box_to_text(
        self,
        region_x: int,
        region_width: int,
        translated_text: str,
        font_size: int,
        font_path: Optional[str],
        frame_w: Optional[int],
    ) -> Tuple[int, int]:
        """
        Widen the cover box (keeping it centered on the original detected
        region) if the translated text needs more horizontal room than the
        original on-screen text did, so the white box fully covers the
        translated caption too instead of letting it spill outside. Never
        shrinks the box below what OCR actually detected.
        """
        needed_width = self._measure_text_width_px(translated_text, font_size, font_path) + 32  # side padding
        if needed_width <= region_width:
            return region_x, region_width

        if frame_w is not None:
            needed_width = min(needed_width, frame_w)

        center_x = region_x + region_width / 2.0
        new_x = int(round(center_x - needed_width / 2.0))
        if frame_w is not None:
            new_x = max(0, min(new_x, frame_w - needed_width))
        else:
            new_x = max(0, new_x)

        return new_x, int(needed_width)