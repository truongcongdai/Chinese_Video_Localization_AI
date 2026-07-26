# src/universal_video_ai/timeline/service.py
from __future__ import annotations

from dataclasses import dataclass, field
import logging
from typing import Optional, List
import math
import re

from universal_video_ai.segment import TranscriptSegment

__all__ = ["TimelineService", "TimelineConfig", "TimelineSegment"]

_logger = logging.getLogger(__name__)


def _balanced_caption_chunks(text: str, max_chars: int = 72, line_chars: int = 36) -> List[str]:
    """Split captions at meaningful clause boundaries, then balance lines.

    Punctuation wins over visual symmetry. For a long clause without
    punctuation, Vietnamese/English connector words are preferred before the
    final length-based fallback. This prevents a subtitle from ending at an
    arbitrary phrase such as "hàng chục" while "giờ mỗi tuần" moves alone to
    the next cue.
    """
    clean = " ".join((text or "").split())
    if not clean:
        return []
    raw_punctuation_parts = [p.strip() for p in re.split(r"(?<=[;:!?。！？；：.])\s+", clean) if p.strip()]
    punctuation_parts: List[str] = []
    for part in raw_punctuation_parts:
        combined = f"{punctuation_parts[-1]} {part}" if punctuation_parts else part
        previous_is_tiny = punctuation_parts and len(punctuation_parts[-1].split()) < 3
        if punctuation_parts and (len(combined) <= max_chars or previous_is_tiny):
            punctuation_parts[-1] = combined
        else:
            punctuation_parts.append(part)
    connectors = {
        "nhưng", "vì", "nên", "để", "khi", "nếu", "mà", "rồi", "và", "hoặc", "sẽ",
        "but", "because", "so", "when", "if", "while", "then", "and", "or", "will", "to",
    }
    # Vietnamese meaning is often carried by two/three-word compounds. A
    # visually balanced split inside one of these sounds broken when the
    # matching TTS cue is synthesized separately ("miễn" / "nhiễm với...").
    protected_pairs = {
        ("miễn", "nhiễm"), ("nọc", "độc"), ("trí", "thông"), ("thông", "minh"),
        ("đặc", "điểm"), ("hệ", "thống"), ("thông", "tin"), ("nghiên", "cứu"),
        ("khoa", "học"), ("thức", "ăn"), ("hàng", "rào"), ("khúc", "gỗ"),
        ("con", "người"), ("giải", "quyết"), ("vấn", "đề"), ("sử", "dụng"),
        ("bao", "gồm"), ("có", "thể"), ("hoàn", "toàn"), ("cực", "kỳ"),
        ("ví", "dụ"), ("tuy", "nhiên"), ("đồng", "thời"), ("bởi", "vì"),
    }
    dangling_left = {
        "miễn", "nọc", "trí", "đặc", "hệ", "nghiên", "khoa", "thức", "hàng",
        "khúc", "giải", "vấn", "sử", "bao", "hoàn", "cực", "ví", "tuy", "đồng", "bởi",
    }

    def normalized_word(word: str) -> str:
        return word.lower().strip("\"'“”‘’()[]{}.,;:!?…")

    def safe_boundary(units: List[str], index: int) -> bool:
        if index <= 0 or index >= len(units):
            return False
        left = normalized_word(units[index - 1])
        right = normalized_word(units[index])
        raw_left = units[index - 1].strip()
        raw_right = units[index].strip()
        if "+" in raw_left or "+" in raw_right:
            return False
        if re.search(r"[A-Z].*[A-Z]", raw_left) and re.search(r"^[A-Z0-9+_-]{2,}", raw_right):
            return False
        return (left, right) not in protected_pairs and left not in dangling_left

    def boundary_cost(units: List[str], index: int, target_chars: float, separator: str) -> float:
        cost = abs(len(separator.join(units[:index])) - target_chars)
        if not safe_boundary(units, index):
            cost += 100_000
        # Avoid orphan fragments even when no known compound is present.
        if index < 3 or len(units) - index < 3:
            cost += 10_000
        return cost

    def split_long(part: str) -> List[str]:
        if len(part) <= max_chars:
            return [part]
        units = part.split() if " " in part else list(part)
        separator = " " if " " in part else ""
        connector_points = [
            i for i in range(2, len(units) - 2)
            if units[i].lower().strip("\"'“”‘’([{,") in connectors and safe_boundary(units, i)
        ]
        if connector_points:
            midpoint = len(part) / 2
            split_at = min(
                connector_points,
                key=lambda i: abs(len(separator.join(units[:i])) - midpoint),
            )
            return split_long(separator.join(units[:split_at])) + split_long(separator.join(units[split_at:]))
        cue_count = min(len(units), max(1, math.ceil(len(part) / max_chars)))
        result: List[str] = []
        cursor = 0
        for cue_index in range(cue_count):
            remaining_cues = cue_count - cue_index
            if remaining_cues == 1:
                end = len(units)
            else:
                max_end = len(units) - (remaining_cues - 1)
                target = len(separator.join(units[cursor:])) / remaining_cues
                end = min(
                    range(cursor + 1, max_end + 1),
                    key=lambda i: boundary_cost(
                        units[cursor:], i - cursor, target, separator,
                    ),
                )
            result.append(separator.join(units[cursor:end]))
            cursor = end
        return result

    semantic_chunks = [chunk for part in punctuation_parts for chunk in split_long(part)]
    chunks: List[str] = []
    for chunk in semantic_chunks:
        chunk_units = chunk.split(" ") if " " in chunk else list(chunk)
        separator = " " if " " in chunk else ""
        if len(chunk) > line_chars and len(chunk_units) > 1:
            split_at = min(
                range(1, len(chunk_units)),
                key=lambda i: boundary_cost(
                    chunk_units, i, len(chunk) / 2, separator,
                ),
            )
            chunk = separator.join(chunk_units[:split_at]) + "\n" + separator.join(chunk_units[split_at:])
        chunks.append(chunk)
    return chunks


def _split_timeline_segment(segment: "TimelineSegment") -> List["TimelineSegment"]:
    chunks = _balanced_caption_chunks(segment.text)
    if len(chunks) <= 1:
        return [TimelineSegment(segment.start_time, segment.end_time, chunks[0] if chunks else segment.text, segment.speaker)]
    weights = [max(1, len(re.sub(r"\s+", "", chunk))) for chunk in chunks]
    total_weight = sum(weights)
    duration = max(0.0, segment.end_time - segment.start_time)
    result: List[TimelineSegment] = []
    elapsed_weight = 0
    for chunk, weight in zip(chunks, weights):
        start = segment.start_time + duration * elapsed_weight / total_weight
        elapsed_weight += weight
        end = segment.start_time + duration * elapsed_weight / total_weight
        result.append(TimelineSegment(start, end, chunk, segment.speaker))
    return result


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

        return [part for segment in segments for part in _split_timeline_segment(segment)]

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
        result = [part for segment in result for part in _split_timeline_segment(segment)]
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

    def generate_ass_karaoke(
        self,
        segments: List[TimelineSegment],
        frame_width: int = 1080,
        frame_height: int = 1920,
        positions: Optional[dict] = None,
        font_size: Optional[int] = None,
    ) -> str:
        """Generate ASS captions whose words fill yellow over time.

        ``positions`` optionally maps a rounded ``(start, end)`` pair to a
        pixel ``(center_x, center_y)``. Positioned cues use middle-centre ASS
        alignment so karaoke text sits in the matching OCR cover box. Cues
        without a position retain the normal bottom-centre fallback.
        """
        def timestamp(seconds: float) -> str:
            centis = max(0, round(seconds * 100))
            hours, centis = divmod(centis, 360_000)
            minutes, centis = divmod(centis, 6_000)
            secs, centis = divmod(centis, 100)
            return f"{hours}:{minutes:02d}:{secs:02d}.{centis:02d}"

        dialogues: List[str] = []
        for segment in segments:
            lines = segment.text.split("\n")
            words = [word for line in lines for word in line.split()]
            total_cs = max(len(words), round(max(0.01, segment.end_time - segment.start_time) * 100))
            weights = [max(1, len(word)) for word in words]
            remaining_cs = total_cs
            remaining_weight = sum(weights) or 1
            rendered_lines: List[str] = []
            word_index = 0
            for line in lines:
                rendered_words: List[str] = []
                for word in line.split():
                    weight = weights[word_index]
                    is_last = word_index == len(words) - 1
                    word_cs = remaining_cs if is_last else max(1, round(remaining_cs * weight / remaining_weight))
                    remaining_cs -= word_cs
                    remaining_weight -= weight
                    word_index += 1
                    safe = word.replace("{", "(").replace("}", ")").replace("\\", "")
                    rendered_words.append(f"{{\\kf{word_cs}}}{safe}")
                rendered_lines.append(" ".join(rendered_words))
            position = None
            best_overlap = 0.0
            # Timeline caption balancing may split one translated source
            # sentence into multiple shorter cues. Match by temporal overlap
            # so every child cue inherits the source sentence's OCR position.
            for (window_start, window_end), candidate in (positions or {}).items():
                overlap = min(segment.end_time, window_end) - max(segment.start_time, window_start)
                if overlap > best_overlap:
                    best_overlap = overlap
                    position = candidate
            placement = f"\\an5\\pos({position[0]},{position[1]})" if position else ""
            dialogues.append(
                f"Dialogue: 0,{timestamp(segment.start_time)},{timestamp(segment.end_time)},Default,,0,0,0,,"
                f"{{\\fad(80,100){placement}}}" + r"\N".join(rendered_lines)
            )

        font_size = font_size or (48 if frame_height >= frame_width else 50)
        margin_lr = max(40, round(frame_width * 0.07))
        margin_v = max(60, round(frame_height * 0.07))
        header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {frame_width}
PlayResY: {frame_height}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,DejaVu Sans,{font_size},&H0000D7FF,&H00FFFFFF,&H00101010,&H80000000,1,0,0,0,100,100,0,0,1,3,1,2,{margin_lr},{margin_lr},{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
        return header + "\n".join(dialogues) + "\n"

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
