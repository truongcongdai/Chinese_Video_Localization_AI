from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Iterable

from universal_video_ai.segment import TranscriptSegment

__all__ = [
    "SpeechFitConfig",
    "SpeechFitReport",
    "estimate_speech_seconds",
    "fit_segment_text",
    "fit_translated_segments",
    "speech_fit_report",
]


_VIETNAMESE_FILLER_PATTERNS = (
    (re.compile(r"\bthực sự là\b", re.IGNORECASE), ""),
    (re.compile(r"\bthực sự\b", re.IGNORECASE), ""),
    (re.compile(r"\brất là\b", re.IGNORECASE), "rất"),
    (re.compile(r"\bmột cách\s+", re.IGNORECASE), ""),
    (re.compile(r"\bở đây\b", re.IGNORECASE), ""),
    (re.compile(r"\bvề cơ bản\b", re.IGNORECASE), ""),
    (re.compile(r"\bnói chung là\b", re.IGNORECASE), ""),
    (re.compile(r"\bđiều này\b", re.IGNORECASE), "việc này"),
    (re.compile(r"\bcác bạn\b", re.IGNORECASE), "bạn"),
)


@dataclass(frozen=True)
class SpeechFitConfig:
    """Limits used to keep translated subtitles readable and TTS slots sane."""

    max_cps: float = 17.0
    max_wps: float = 3.2
    speech_buffer_ratio: float = 0.92
    max_tts_speedup: float = 1.15
    hard_limit_ratio: float = 1.15
    allow_local_rewrite: bool = False
    allow_truncation: bool = False


@dataclass(frozen=True)
class SpeechFitReport:
    text: str
    duration: float
    target_speech_seconds: float
    char_count: int
    word_count: int
    max_chars: int
    cps: float
    wps: float
    estimated_speech_seconds: float
    fits: bool


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def _word_count(text: str) -> int:
    return len([part for part in re.split(r"\s+", text.strip()) if part])


def estimate_speech_seconds(text: str, config: SpeechFitConfig = SpeechFitConfig()) -> float:
    """Estimate spoken duration from both character and word rates."""
    clean = _clean_text(text)
    if not clean:
        return 0.0
    char_seconds = len(clean) / max(1.0, config.max_cps)
    word_seconds = _word_count(clean) / max(0.1, config.max_wps)
    return max(char_seconds, word_seconds)


def speech_fit_report(
    segment: TranscriptSegment,
    config: SpeechFitConfig = SpeechFitConfig(),
) -> SpeechFitReport:
    clean = _clean_text(segment.text)
    duration = max(0.0, segment.duration)
    target_speech_seconds = duration * max(0.1, config.speech_buffer_ratio)
    char_count = len(clean)
    word_count = _word_count(clean)
    max_chars = max(1, math.floor(target_speech_seconds * max(1.0, config.max_cps))) if duration else char_count
    cps = char_count / duration if duration > 0 else 0.0
    wps = word_count / duration if duration > 0 else 0.0
    estimated = estimate_speech_seconds(clean, config)
    allowed_seconds = duration * max(1.0, config.max_tts_speedup)
    fits = (
        duration <= 0
        or (
            char_count <= max_chars
            and cps <= config.max_cps
            and wps <= config.max_wps
            and estimated <= allowed_seconds
        )
    )
    return SpeechFitReport(
        text=clean,
        duration=duration,
        target_speech_seconds=target_speech_seconds,
        char_count=char_count,
        word_count=word_count,
        max_chars=max_chars,
        cps=cps,
        wps=wps,
        estimated_speech_seconds=estimated,
        fits=fits,
    )


def _remove_fillers(text: str) -> str:
    shortened = text
    for pattern, replacement in _VIETNAMESE_FILLER_PATTERNS:
        shortened = pattern.sub(replacement, shortened)
    return _clean_text(shortened)


def _truncate_at_word_boundary(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    words = text.split()
    kept: list[str] = []
    for word in words:
        candidate = " ".join([*kept, word])
        if len(candidate) > max_chars:
            break
        kept.append(word)
    return " ".join(kept).strip(" ,;:")


def fit_segment_text(
    text: str,
    duration: float,
    config: SpeechFitConfig = SpeechFitConfig(),
) -> str:
    """Best-effort local rewrite for a translated cue when no LLM can help.

    The function first removes low-information filler. It only truncates as a
    final fallback when the cue is substantially over budget, because blind
    truncation can lose meaning.
    """
    clean = _clean_text(text)
    if not clean or duration <= 0:
        return clean
    if not config.allow_local_rewrite:
        return clean

    target_seconds = duration * max(0.1, config.speech_buffer_ratio)
    max_chars = max(1, math.floor(target_seconds * max(1.0, config.max_cps)))
    if len(clean) <= max_chars:
        return clean

    shortened = _remove_fillers(clean)
    if len(shortened) <= max_chars:
        return shortened

    hard_limit = max_chars * max(1.0, config.hard_limit_ratio)
    if len(shortened) <= hard_limit:
        return shortened
    if not config.allow_truncation:
        return shortened

    return _truncate_at_word_boundary(shortened, max_chars)


def fit_translated_segments(
    segments: Iterable[TranscriptSegment],
    config: SpeechFitConfig = SpeechFitConfig(),
) -> list[TranscriptSegment]:
    result: list[TranscriptSegment] = []
    for segment in segments:
        text = fit_segment_text(segment.text, segment.duration, config)
        result.append(TranscriptSegment(start=segment.start, end=segment.end, text=text or segment.text))
    return result
