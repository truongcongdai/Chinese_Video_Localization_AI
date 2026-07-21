# src/universal_video_ai/orchestrator/service.py
from __future__ import annotations

from dataclasses import dataclass, field
import logging
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from universal_video_ai.downloader.service import DownloadService
from universal_video_ai.downloader.download_result import DownloadResult
from universal_video_ai.downloader.platform import Platform
from universal_video_ai.audio.factory import create_audio_pipeline
from universal_video_ai.audio.pipeline import AudioPipelineResult
from universal_video_ai.audio.audio_result import AudioResult
from universal_video_ai.segment import TranscriptSegment
from universal_video_ai.translate.service import TranslateService
from universal_video_ai.tts.service import TTSService
from universal_video_ai.timeline.service import TimelineService, TimelineConfig, TimelineSegment
from universal_video_ai.mixer.service import MixerService, MixerConfig, AudioMix, TimedAudioClip
from universal_video_ai.render.renderer import Renderer, RenderConfig, TextOverlay
from universal_video_ai.render.text_detector import OnScreenTextDetector
from universal_video_ai.render import ocr_language_map

__all__ = [
    "LocalizationService", "LocalizationConfig", "LocalizationResult",
    "PreparedLocalization", "prepared_localization_to_dict", "prepared_localization_from_dict",
]

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
    # Explicit Edge-TTS voice id (e.g. "vi-VN-NamMinhNeural") to use instead
    # of the target language's default voice — see tts.voices.VOICE_OPTIONS
    # for the curated male/female choices the web UI offers. None = use
    # tts.voice_for_language()'s default for target_language, unchanged
    # from before this option existed.
    tts_voice: Optional[str] = None

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
    text_cover_samples_per_segment: int = 2


@dataclass(frozen=True)
class LocalizationResult:
    """Result of end-to-end localization workflow."""

    download_result: DownloadResult
    audio_pipeline_result: AudioPipelineResult
    translated_text: Optional[str] = None
    # Per-sentence translated segments with the ORIGINAL sentence's start/end
    # timestamps preserved. This is what keeps dubbing/subtitles/text-cover
    # aligned with the source video; `translated_text` is kept only for
    # backward compatibility with callers that just want the flat string.
    translated_segments: Optional[List[TranscriptSegment]] = None
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
    return PreparedLocalization(
        download_result=download_result,
        audio_result=audio_pipeline_result,
        source_segments=source_segments,
        translated_segments=translated_segments,
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
            tts_service: Optional[TTSService] = None,
            timeline: Optional[TimelineService] = None,
            mixer: Optional[MixerService] = None,
            renderer: Optional[Renderer] = None,
            text_detector: Optional[OnScreenTextDetector] = None,
            config: Optional[LocalizationConfig] = None,
            logger: Optional[logging.Logger] = None,
            progress_callback: Optional[Callable[[int, str], None]] = None,
    ) -> None:
        self.downloader = downloader or DownloadService()
        self.translate_service = translate_service
        self.tts_service = tts_service
        self.timeline = timeline or TimelineService()
        self.mixer = mixer or MixerService()
        self.renderer = renderer or Renderer()
        self.text_detector = text_detector
        self.config = config or LocalizationConfig()
        self.logger = logger or _logger
        self.progress_callback = progress_callback

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
        if self.progress_callback:
            try:
                self.progress_callback(max(0, min(100, percent)), message)
            except Exception:
                self.logger.exception("Progress callback failed")

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
        audio, and translate the transcript. See `localize()`."""
        output_dir = Path(output_dir).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)

        effective_target_language = target_language or self.config.target_language or "en"

        self.logger.info(
            "LocalizationService._prepare: url=%s output_dir=%s target_language=%s",
            url, output_dir, effective_target_language,
        )

        # Step 1: Download video
        self._progress(5, "Đang tải video nguồn")
        self.logger.info("LocalizationService: downloading video")
        download_result = self.downloader.download(url, output_dir)

        if not download_result.success:
            raise ValueError(f"Download failed for {url}")

        self.logger.info("LocalizationService: download successful: %s", download_result.video_path)
        self._progress(15, "Đã tải video, đang xử lý âm thanh")

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
        self._progress(40, "Đã tách âm thanh và nhận diện lời nói")

        source_segments: List[TranscriptSegment] = [
            s for s in (audio_result.segments or []) if s.has_timing
        ]

        translated_text: Optional[str] = None
        translated_segments: Optional[List[TranscriptSegment]] = None

        # Step 3: Translate transcript — prefer segment-level translation so
        # every sentence keeps the timestamp it had in the source video.
        if self.config.run_translation and audio_result.transcript:
            self._progress(45, "Đang dịch nội dung")
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
                    if source_segments:
                        self.logger.info(
                            "LocalizationService: translating %d timed segments to %s",
                            len(source_segments), target_lang,
                        )
                        translated_segments = await self.translate_service.translate_segments(
                            source_segments, source_lang=source_lang, target_lang=target_lang
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
                    self._progress(58, "Đã dịch nội dung")
                except Exception as exc:
                    self.logger.error("Translation failed: %s", exc)
                    translated_text = None
                    translated_segments = None

        return PreparedLocalization(
            download_result=download_result,
            audio_result=audio_result,
            source_segments=source_segments,
            translated_segments=translated_segments,
            translated_text=translated_text,
            target_language=effective_target_language,
            output_dir=output_dir,
        )

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
        else:
            translated_segments = prepared.translated_segments
            translated_text = prepared.translated_text

        tts_audio_path: Optional[Path] = None

        # Step 4: Synthesize TTS from translated text, anchored to the
        # original sentence timestamps whenever we have them.
        if self.config.run_tts and (translated_segments or translated_text):
            self._progress(62, "Đang tạo giọng đọc")
            if self.tts_service is None:
                self.logger.warning("TTS requested but no TTSService injected; skipping")
            else:
                try:
                    if translated_segments:
                        tts_audio_path = self._synthesize_timed_track(
                            translated_segments,
                            total_duration=audio_result.audio_result.duration,
                            output_dir=output_dir,
                            target_language=effective_target_language,
                            voice=self.config.tts_voice,
                        )
                    else:
                        self.logger.info("LocalizationService: synthesizing TTS (whole-text fallback)")
                        tts_audio_path = output_dir / "tts_audio.wav"
                        self.tts_service.synthesize(
                            translated_text,
                            output_path=tts_audio_path,
                            language=effective_target_language,
                            voice=self.config.tts_voice,
                        )
                    self.logger.info("LocalizationService: TTS complete: %s", tts_audio_path)
                    self._progress(72, "Đã tạo giọng đọc")
                except Exception as exc:
                    self.logger.error("TTS synthesis failed: %s", exc)
                    # Never silently deliver a "dubbed" reup with no dub.
                    # Surface the failure so the job is refundable/retryable.
                    raise RuntimeError(f"Không tạo được giọng đọc TTS: {exc}") from exc

        # Step 5: Generate subtitles — directly from real timestamps when available.
        subtitle_segments: Optional[List[TimelineSegment]] = None
        subtitles_path: Optional[Path] = None
        if self.config.generate_subtitles and (translated_segments or translated_text or audio_result.transcript):
            self._progress(75, "Đang tạo phụ đề")
            self.logger.info("LocalizationService: generating subtitles")
            if translated_segments:
                subtitle_segments = self.timeline.from_segments(
                    translated_segments, audio_duration=audio_result.audio_result.duration
                )
            else:
                # Fallback: even-split heuristic over whichever text we have
                # (prefer the translated text so subtitles are in the target
                # language, not the original).
                text_for_subs = translated_text or audio_result.transcript
                subtitle_segments = self.timeline.align_transcript(
                    text_for_subs, audio_result.audio_result.duration
                )
            self.logger.info("LocalizationService: generated %d subtitle segments", len(subtitle_segments))

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
            self._progress(79, "Đã tạo phụ đề")

        # Step 6: Detect + build on-screen text-cover overlays (best-effort).
        text_overlays: Optional[List[TextOverlay]] = None
        if (
            self.config.enable_text_cover
            and translated_segments
            and source_segments
            and download_result.video_path
        ):
            text_overlays = self._build_text_overlays(
                video_path=download_result.video_path,
                source_segments=source_segments,
                translated_segments=translated_segments,
                detected_language=audio_result.detected_language,
            )
            if text_overlays and subtitles_path and subtitle_segments:
                # ASS owns all translated text so every cue keeps the karaoke
                # fill effect. Detected cues are middle-centred at their OCR
                # box; unmatched cues remain bottom-centred as a safety net.
                # One explicit size is shared by every cue in this video.
                common_font_size = text_overlays[0].font_size or 48
                positions = {
                    (round(overlay.start, 3), round(overlay.end, 3)): (
                        round(overlay.x + overlay.width / 2),
                        round(overlay.y + overlay.height / 2),
                    )
                    for overlay in text_overlays
                }
                subtitles_path.write_text(
                    self.timeline.generate_ass_karaoke(
                        subtitle_segments,
                        frame_width=frame_width,
                        frame_height=frame_height,
                        positions=positions,
                        font_size=common_font_size,
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

        # Step 7: Mix audio (original + TTS)
        mixed_audio_path: Optional[Path] = None
        if self.config.mix_audio and tts_audio_path:
            self._progress(83, "Đang phối âm thanh")
            self.logger.info("LocalizationService: mixing audio streams")
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
            self._progress(87, "Đã phối âm thanh")

        # Step 8: Render final video (merge video + audio + optional subtitles/overlays)
        final_video_path: Optional[Path] = None
        if self.config.render_video and download_result.video_path:
            self._progress(90, "Đang render video cuối")
            if self.renderer is None:
                self.logger.warning("Rendering requested but no Renderer available; skipping")
            else:
                self.logger.info("LocalizationService: rendering final video")
                try:
                    # Use mixed audio if available, otherwise use TTS audio, otherwise use original audio
                    audio_for_render = mixed_audio_path or tts_audio_path or audio_result.audio_result.audio_path

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
                    subtitles_for_render = subtitles_path
                    if (
                        self.config.skip_srt_when_text_overlays_present
                        and subtitles_path is not None
                        and text_overlays
                        and any(overlay.text for overlay in text_overlays)
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
                    self.renderer.render(
                        video_path=download_result.video_path,
                        audio_path=audio_for_render,
                        subtitles=subtitles_for_render,
                        output_path=final_video_path,
                        text_overlays=text_overlays,
                    )
                    self._progress(98, "Đã render, đang kiểm tra video")
                    self.logger.info("LocalizationService: render complete: %s", final_video_path)
                except Exception as exc:
                    self.logger.error("Rendering failed: %s", exc)
                    final_video_path = None

        return LocalizationResult(
            download_result=download_result,
            audio_pipeline_result=audio_result,
            translated_text=translated_text,
            translated_segments=translated_segments,
            tts_audio_path=tts_audio_path,
            subtitle_segments=subtitle_segments,
            mixed_audio_path=mixed_audio_path,
            text_overlays=text_overlays,
            final_video_path=final_video_path,
        )

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
        """
        segments_dir = output_dir / "tts_segments"
        segments_dir.mkdir(parents=True, exist_ok=True)

        clips: List[TimedAudioClip] = []
        for idx, seg in enumerate(translated_segments):
            if not seg.text.strip():
                continue
            clip_path = segments_dir / f"segment_{idx:04d}.wav"
            self.tts_service.synthesize(
                seg.text,
                output_path=clip_path,
                language=target_language,
                voice=voice,
            )
            if seg.has_timing:
                clips.append(TimedAudioClip(start=seg.start, end=seg.end, audio_path=clip_path))
            else:
                # No real timing for this one sentence: place it right after
                # the previous clip rather than dropping it.
                prev_end = clips[-1].end if clips else 0.0
                clips.append(TimedAudioClip(start=prev_end, end=prev_end + 2.0, audio_path=clip_path))

        tts_audio_path = output_dir / "tts_audio.wav"
        self.mixer.build_dubbed_track(clips, total_duration=total_duration, output_path=tts_audio_path)
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

            x, width = self._fit_box_to_text(
                region.x, region.width, translated_text, fixed_font_size, font_path, frame_w,
            )

            overlays.append(
                TextOverlay(
                    start=region.start,
                    end=region.end,
                    x=x,
                    y=region.y,
                    width=width,
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
        needed_width = self._measure_text_width_px(translated_text, font_size, font_path) + 16  # small side padding
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
