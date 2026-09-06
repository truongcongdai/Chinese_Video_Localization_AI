"""Deterministic source-to-localization coverage reconciliation.

ASR and burned-subtitle OCR are complementary evidence sources.  A relevant
event must receive a localization decision instead of disappearing because
one detector missed it, especially at the beginning of a video.
"""
from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
import re
from typing import Iterable, List, Sequence

from universal_video_ai.segment import TranscriptSegment

__all__ = [
    "SourceEvidence",
    "CoverageGap",
    "CoverageReport",
    "reconcile_source_evidence",
    "validate_timeline_coverage",
]


@dataclass(frozen=True)
class SourceEvidence:
    start: float
    end: float
    text: str
    detector: str

    def as_segment(self) -> TranscriptSegment:
        return TranscriptSegment(self.start, self.end, self.text)


@dataclass(frozen=True)
class CoverageGap:
    start: float
    end: float
    source_text: str
    source_detector: str
    reason: str


@dataclass(frozen=True)
class CoverageReport:
    total_relevant: int
    covered: int
    coverage_ratio: float
    uncovered: List[CoverageGap]

    @property
    def complete(self) -> bool:
        return not self.uncovered


def _normalized(text: str) -> str:
    return re.sub(r"\W+", "", (text or "").casefold(), flags=re.UNICODE)


def _text_similarity(left: str, right: str) -> float:
    a, b = _normalized(left), _normalized(right)
    if not a or not b:
        return 0.0
    if a in b or b in a:
        return min(len(a), len(b)) / max(len(a), len(b))
    return SequenceMatcher(None, a, b).ratio()


def _overlap_ratio(left_start: float, left_end: float, right_start: float, right_end: float) -> float:
    overlap = min(left_end, right_end) - max(left_start, right_start)
    if overlap <= 0:
        return 0.0
    shortest = max(1e-6, min(left_end - left_start, right_end - right_start))
    return overlap / shortest


def reconcile_source_evidence(
    asr_segments: Sequence[TranscriptSegment],
    ocr_segments: Sequence[TranscriptSegment],
    *,
    duplicate_overlap_ratio: float = 0.35,
    duplicate_text_similarity: float = 0.45,
) -> List[SourceEvidence]:
    """Merge OCR-only gaps into ASR without double translating duplicates."""
    events = [
        SourceEvidence(float(item.start), float(item.end), item.text.strip(), "asr")
        for item in asr_segments
        if item.has_timing and item.end > item.start and item.text.strip()
    ]
    for item in ocr_segments:
        if not item.has_timing or item.end <= item.start or not item.text.strip():
            continue
        duplicate = any(
            _overlap_ratio(item.start, item.end, event.start, event.end) >= duplicate_overlap_ratio
            and _text_similarity(item.text, event.text) >= duplicate_text_similarity
            for event in events
        )
        if not duplicate:
            events.append(SourceEvidence(float(item.start), float(item.end), item.text.strip(), "ocr"))
    return sorted(events, key=lambda event: (event.start, event.end, event.detector))


def validate_timeline_coverage(
    source: Sequence[SourceEvidence] | Sequence[TranscriptSegment],
    localized: Iterable[object],
    *,
    localized_start_attr: str = "start",
    localized_end_attr: str = "end",
    minimum_overlap_ratio: float = 0.20,
    default_detector: str = "asr",
) -> CoverageReport:
    """Return every relevant source event not covered by a localized cue."""
    evidence: List[SourceEvidence] = []
    for item in source:
        if isinstance(item, SourceEvidence):
            event = item
        else:
            event = SourceEvidence(
                float(item.start), float(item.end), str(item.text), default_detector
            )
        if event.end > event.start and event.text.strip():
            evidence.append(event)

    localized_windows = []
    for item in localized:
        start = getattr(item, localized_start_attr, None)
        end = getattr(item, localized_end_attr, None)
        text = getattr(item, "text", "")
        # 0.0 is valid; only None means absent.
        if start is None or end is None or float(end) <= float(start) or not str(text).strip():
            continue
        localized_windows.append((float(start), float(end)))

    gaps: List[CoverageGap] = []
    for event in evidence:
        covered = any(
            _overlap_ratio(event.start, event.end, start, end) >= minimum_overlap_ratio
            for start, end in localized_windows
        )
        if not covered:
            gaps.append(CoverageGap(
                event.start,
                event.end,
                event.text,
                event.detector,
                "no_overlapping_localized_timeline_entry",
            ))
    total = len(evidence)
    covered_count = total - len(gaps)
    return CoverageReport(
        total_relevant=total,
        covered=covered_count,
        coverage_ratio=(covered_count / total if total else 1.0),
        uncovered=gaps,
    )
