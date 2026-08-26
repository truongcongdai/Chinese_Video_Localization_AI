# src/universal_video_ai/audio/pipeline.py
from __future__ import annotations

from dataclasses import dataclass
import logging
import shutil
from pathlib import Path
import hashlib
import json
from typing import List, Optional

from universal_video_ai.downloader.download_result import DownloadResult
from universal_video_ai.segment import TranscriptSegment, UNKNOWN_TIMING
from .audio_result import AudioResult
from .demucs import DemucsOutput
from .extractor import AudioExtractor
from .demucs import DEMUCS_AVAILABLE

# depend on service layer (DI)
from universal_video_ai.speech.service import SpeechService  # type: ignore

__all__ = ["AudioPipelineConfig", "AudioPipelineResult", "AudioPipeline"]

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AudioPipelineConfig:
    run_demucs: bool = False
    demucs_output_dir: Optional[Path] = None
    run_transcription: bool = False
    transcription_language: Optional[str] = None
    transcription_model: Optional[str] = None
    # Source-effect separation is optional. Multi-hour Demucs jobs generate
    # many gigabytes of stems and intermediate WAV data; skip it beyond this
    # duration so transcription/localization can still finish. None disables
    # the limit.
    demucs_max_duration_seconds: Optional[float] = 2 * 60 * 60


@dataclass(frozen=True)
class AudioPipelineResult:
    audio_result: AudioResult
    demucs_output: Optional[DemucsOutput] = None
    transcript: Optional[str] = None
    # Per-sentence transcript with real start/end timestamps (seconds), when
    # the configured SpeechBackend supports it (e.g. Whisper). Downstream
    # stages (translation, TTS, subtitles, on-screen text cover) should
    # prefer this over `transcript` so the localized video stays aligned
    # with the original video's timing. May contain a single segment with
    # `end == UNKNOWN_TIMING` if the backend only provided flat text.
    segments: Optional[List[TranscriptSegment]] = None
    # Language Whisper actually detected the spoken audio to be in (e.g.
    # "zh", "en", "ja") — None if transcription was skipped, the backend
    # doesn't expose it, or this result came from cache. Lets callers pick
    # a matching OCR language pack for burned-in on-screen text (see
    # LocalizationConfig.ocr_languages == ("auto",)) instead of assuming
    # every source video is Chinese.
    detected_language: Optional[str] = None


class AudioPipeline:
    """Small orchestrator for audio extraction -> optional demucs -> optional transcription.

    Notes:
    - Accepts dependencies via DI (extractor, demucs_processor, speech_service).
    - Does not construct heavy backends itself.
    """

    def __init__(
        self,
        config: Optional[AudioPipelineConfig] = None,
        extractor: Optional[AudioExtractor] = None,
        demucs_processor: Optional[object] = None,
        speech_service: Optional[SpeechService] = None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.config = config or AudioPipelineConfig()
        self.extractor = extractor or AudioExtractor()
        self.demucs_processor = demucs_processor
        self.speech_service = speech_service
        self.logger = logger or _logger

        self.logger.debug(
            "AudioPipeline initialized run_demucs=%s demucs_processor=%s run_transcription=%s speech_service=%s",
            self.config.run_demucs,
            type(self.demucs_processor).__name__ if self.demucs_processor is not None else None,
            self.config.run_transcription,
            type(self.speech_service).__name__ if self.speech_service is not None else None,
        )

    def process(self, download_result: DownloadResult, output_dir: Optional[Path] = None) -> AudioPipelineResult:
        if not download_result.success:
            raise ValueError("Cannot process audio for unsuccessful download_result")

        video_path = download_result.video_path
        self.logger.info("AudioPipeline.process: video=%s", video_path)

        # Extract audio
        audio_result = self.extractor.extract(video_path, output_dir=output_dir)

        demucs_output: Optional[DemucsOutput] = None
        transcript: Optional[str] = None
        segments: Optional[List[TranscriptSegment]] = None
        detected_language: Optional[str] = None

        # Demucs step (optional)
        if self.config.run_demucs:
            if not DEMUCS_AVAILABLE and self.demucs_processor is None:
                raise RuntimeError("Demucs requested but not available and no demucs_processor injected")
            if self.demucs_processor is None:
                raise RuntimeError("Demucs requested but no demucs_processor was provided")
            max_duration = self.config.demucs_max_duration_seconds
            skip_for_duration = bool(
                max_duration
                and audio_result.duration > max_duration
            )
            # A regular WAV Demucs run plus chunk and stem assembly can peak at
            # well over 10x the input size. If the disk cannot sustain that,
            # source effects are optional and the safe fallback is dub + the
            # licensed replacement track.
            free_bytes = shutil.disk_usage(audio_result.audio_path.parent).free
            estimated_required = max(8 * 1024 ** 3, audio_result.filesize * 12)
            skip_for_disk = free_bytes < estimated_required
            if skip_for_duration:
                self.logger.warning(
                    "Skipping Demucs for %.2f-hour audio (configured maximum %.2f hours); "
                    "continuing localization without source effects",
                    audio_result.duration / 3600,
                    float(max_duration) / 3600,
                )
            elif skip_for_disk:
                self.logger.warning(
                    "Skipping Demucs because free disk space is %.2f GB but an estimated %.2f GB is required; "
                    "continuing localization without source effects",
                    free_bytes / 1024 ** 3,
                    estimated_required / 1024 ** 3,
                )
            else:
                self.logger.info("AudioPipeline: running demucs for %s", audio_result.audio_path)
                demucs_output = self.demucs_processor.separate(
                    audio_result.audio_path, output_dir=self.config.demucs_output_dir
                )
                self.logger.debug("AudioPipeline: demucs_output=%s", demucs_output)

        # Transcription step (optional) via SpeechService.
        # We always request per-sentence segments (transcribe_segments); the
        # service transparently falls back to a single unknown-timing segment
        # if the backend doesn't support real segment-level timestamps. This
        # keeps `transcript` (flat text, used by legacy callers) and
        # `segments` (timed, used by the localization pipeline) in sync and
        # avoids calling the backend twice.
        if self.config.run_transcription:
            if self.speech_service is None:
                raise RuntimeError("Transcription requested but no SpeechService was injected")
            self.logger.info("AudioPipeline: running transcription for %s (lang=%s)",
                             audio_result.audio_path, self.config.transcription_language)
            transcript_cache = audio_result.audio_path.parent / ".uvai_transcript_cache.json"
            fingerprint = self._audio_fingerprint(audio_result.audio_path)
            cache_identity = {
                "fingerprint": fingerprint,
                "language": self.config.transcription_language or "auto",
                "model": self.config.transcription_model or "base",
            }
            cached_payload = self._load_transcript_cache(transcript_cache, cache_identity)
            if cached_payload is not None:
                segments = [TranscriptSegment(**item) for item in cached_payload["segments"]]
                detected_language = cached_payload.get("detected_language")
                self.logger.info(
                    "AudioPipeline: transcript cache hit (%d segments)", len(segments)
                )
            else:
                segments = self.speech_service.transcribe_segments(
                    audio_result.audio_path, language=self.config.transcription_language
                )
                detected_language = getattr(self.speech_service.backend, "last_detected_language", None)
                self._save_transcript_cache(
                    transcript_cache,
                    cache_identity,
                    segments,
                    detected_language,
                )
            transcript = " ".join(seg.text.strip() for seg in segments if seg.text.strip()) or None
            # Best-effort: only populated when the backend actually ran (not
            # a cache hit) and exposes `last_detected_language` (WhisperBackend
            # does; NoOp/other backends simply won't have the attribute).
            if detected_language is None:
                detected_language = getattr(self.speech_service.backend, "last_detected_language", None)
            self.logger.debug(
                "AudioPipeline: transcript length=%d segments=%d detected_language=%s",
                len(transcript) if transcript else 0,
                len(segments) if segments else 0,
                detected_language,
            )

        return AudioPipelineResult(
            audio_result=audio_result,
            demucs_output=demucs_output,
            transcript=transcript,
            segments=segments,
            detected_language=detected_language,
        )

    @staticmethod
    def _audio_fingerprint(audio_path: Path) -> str:
        digest = hashlib.sha256()
        with audio_path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _load_transcript_cache(self, path: Path, identity: dict) -> Optional[dict]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if payload.get("identity") != identity or not isinstance(payload.get("segments"), list):
                return None
            return payload
        except (OSError, ValueError, TypeError):
            return None

    def _save_transcript_cache(
        self,
        path: Path,
        identity: dict,
        segments: List[TranscriptSegment],
        detected_language: Optional[str],
    ) -> None:
        payload = {
            "identity": identity,
            "detected_language": detected_language,
            "segments": [
                {"start": item.start, "end": item.end, "text": item.text}
                for item in segments
            ],
        }
        temporary = path.with_suffix(path.suffix + ".tmp")
        try:
            temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            temporary.replace(path)
        except OSError as exc:
            self.logger.warning("Could not save transcript cache %s: %s", path, exc)
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
