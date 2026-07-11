# src/universal_video_ai/timeline/service.py
from __future__ import annotations

from dataclasses import dataclass, field
import logging
from typing import Optional, List

from universal_video_ai.segment import TranscriptSegment

__all__ = ["TimelineService", "TimelineConfig", "TimelineSegment"]

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TimelineSegment:
    """A segment of timed text/subtitles."""

    start_time: float  # seconds
    end_time: float  # seconds
    text: str
    speaker: Optional[str] = None


@dataclass
class TimelineConfig:
    """Configuration for timeline service."""

    subtitle_format: str = "srt"  # srt, vtt, ass
    fps: int = 30


class TimelineService:
    """Service for managing subtitle timing and synchronization.

    Responsibilities:
    - Convert transcript timestamps to SRT/VTT format.
    - Align subtitles with audio events (speaker changes, music, etc.).
    - Generate subtitle files.
    """

    def __init__(self, config: Optional[TimelineConfig] = None, logger: Optional[logging.Logger] = None) -> None:
        self.config = config or TimelineConfig()
        self.logger = logger or _logger
        self.logger.debug("TimelineService initialized subtitle_format=%s fps=%s", self.config.subtitle_format,
                          self.config.fps)

    def align_transcript(self, transcript: str, audio_duration: float) -> List[TimelineSegment]:
        """
        Convert a flat transcript into timed segments.

        For now, this is a simple implementation that distributes the transcript
        evenly across the audio duration. In a production system, you'd use speech
        timestamps from Whisper or another ASR backend.

        :param transcript: full transcript text
        :param audio_duration: audio duration in seconds
        :return: list of TimelineSegment
        """
        if not transcript or not transcript.strip():
            self.logger.warning("Empty transcript provided")
            return []

        self.logger.info("TimelineService.align_transcript: duration=%.2f seconds", audio_duration)

        # Simple heuristic: split by sentences and distribute evenly
        sentences = [s.strip() for s in transcript.split('.') if s.strip()]
        if not sentences:
            sentences = [transcript]

        duration_per_segment = audio_duration / len(sentences)
        segments = []

        for idx, sentence in enumerate(sentences):
            start = idx * duration_per_segment
            end = (idx + 1) * duration_per_segment
            segment = TimelineSegment(start_time=start, end_time=end, text=sentence + ".")
            segments.append(segment)
            self.logger.debug("TimelineService: segment %d [%.2f - %.2f] %s", idx, start, end, sentence[:50])

        return segments

    def from_segments(self, segments: List[TranscriptSegment], audio_duration: Optional[float] = None) -> List[TimelineSegment]:
        """
        Build subtitle segments directly from real ASR/translation timestamps,
        instead of guessing timing by evenly splitting a flat transcript.

        Use this (rather than `align_transcript`) whenever `TranscriptSegment`
        objects with real timing are available (e.g. from
        `SpeechService.transcribe_segments` or
        `TranslateService.translate_segments`) — this is what keeps subtitles,
        the dubbed voice, and the on-screen text cover all lined up with the
        moment the original speaker actually said each sentence.

        Segments whose timing is unknown (`TranscriptSegment.has_timing` is
        False, e.g. a legacy backend that only returned flat text) are
        distributed evenly across `audio_duration` as a best-effort fallback,
        mirroring `align_transcript`'s behavior.

        :param segments: timed transcript/translated segments
        :param audio_duration: total audio duration, used only as a fallback
            for segments without real timing
        :return: list of TimelineSegment
        """
        if not segments:
            return []

        timed = [s for s in segments if s.has_timing]
        untimed = [s for s in segments if not s.has_timing]

        result: List[TimelineSegment] = [
            TimelineSegment(start_time=s.start, end_time=s.end, text=s.text) for s in timed
        ]

        if untimed:
            self.logger.warning(
                "TimelineService.from_segments: %d segment(s) had no real timing; "
                "falling back to even-split for those",
                len(untimed),
            )
            duration = audio_duration or 0.0
            per = duration / len(untimed) if untimed and duration else 0.0
            base = max((s.end_time for s in result), default=0.0)
            for idx, s in enumerate(untimed):
                start = base + idx * per
                end = base + (idx + 1) * per
                result.append(TimelineSegment(start_time=start, end_time=end, text=s.text))

        result.sort(key=lambda seg: seg.start_time)
        self.logger.info("TimelineService.from_segments: built %d subtitle segments from real timestamps", len(result))
        return result

    def generate_srt(self, segments: List[TimelineSegment]) -> str:
        """Generate SRT subtitle file content."""
        lines = []
        for idx, seg in enumerate(segments, start=1):
            lines.append(str(idx))
            lines.append(f"{self._format_timestamp_srt(seg.start_time)} --> {self._format_timestamp_srt(seg.end_time)}")
            lines.append(seg.text)
            lines.append("")
        return "\n".join(lines)

    def generate_vtt(self, segments: List[TimelineSegment]) -> str:
        """Generate WebVTT subtitle file content."""
        lines = ["WEBVTT\n"]
        for seg in segments:
            lines.append(f"{self._format_timestamp_vtt(seg.start_time)} --> {self._format_timestamp_vtt(seg.end_time)}")
            lines.append(seg.text)
            lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _format_timestamp_srt(seconds: float) -> str:
        """Convert seconds to SRT timestamp format HH:MM:SS,mmm"""
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        millis = int((seconds % 1) * 1000)
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"

    @staticmethod
    def _format_timestamp_vtt(seconds: float) -> str:
        """Convert seconds to VTT timestamp format HH:MM:SS.mmm"""
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        millis = int((seconds % 1) * 1000)
        return f"{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}"