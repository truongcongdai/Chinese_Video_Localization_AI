# src/universal_video_ai/segment.py
"""
Core (Layer 5) shared data model for timed transcript text.

This module intentionally contains ONLY a tiny, dependency-free dataclass so
that Layer 3 services (speech/, translate/) can produce/consume timed segments
without importing each other (see PROJECT_BRAIN/IMPORT_RULES.md).

A `TranscriptSegment` represents one sentence/utterance of the ORIGINAL audio,
anchored to real start/end timestamps (seconds) as reported by the ASR engine.
Downstream stages (translation, TTS, subtitles, on-screen text cover) all key
off of `start`/`end` to keep the localized video aligned with the source.
"""
from __future__ import annotations

from dataclasses import dataclass

__all__ = ["TranscriptSegment"]

# Sentinel used when an ASR/backend only produced flat text without real
# per-sentence timestamps (e.g. NoOp backends, or a TTS/ASR backend that
# doesn't expose segment-level timing). Downstream code should treat a
# segment with end == UNKNOWN_TIMING as "spans the whole audio".
UNKNOWN_TIMING: float = -1.0


@dataclass(frozen=True)
class TranscriptSegment:
    """A single timed chunk of transcript text.

    Attributes:
        start: start time in seconds (relative to the source audio/video).
        end: end time in seconds. May be UNKNOWN_TIMING (-1.0) if the backend
             could not provide per-segment timing.
        text: the transcript text for this segment (source language).
    """

    start: float
    end: float
    text: str

    @property
    def has_timing(self) -> bool:
        """Whether this segment carries real start/end timing."""
        return self.end != UNKNOWN_TIMING and self.end >= self.start

    @property
    def duration(self) -> float:
        """Segment duration in seconds (0.0 if timing is unknown)."""
        if not self.has_timing:
            return 0.0
        return max(0.0, self.end - self.start)
