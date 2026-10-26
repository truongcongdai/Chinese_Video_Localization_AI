"""Optional local diarization; no model import/download in single-voice mode."""
from __future__ import annotations

from dataclasses import dataclass, replace
import logging
import math
from pathlib import Path
import threading
from typing import Protocol, Sequence

from universal_video_ai.segment import TranscriptSegment

__all__ = ["SpeakerTurn", "Diarizer", "PyannoteDiarizer", "assign_speakers"]
_logger = logging.getLogger(__name__)
_INFERENCE_LOCK = threading.Lock()


@dataclass(frozen=True)
class SpeakerTurn:
    start: float
    end: float
    speaker_id: str


class Diarizer(Protocol):
    def diarize(self, audio_path: Path) -> Sequence[SpeakerTurn]: ...


def assign_speakers(segments: Sequence[TranscriptSegment], turns: Sequence[SpeakerTurn]) -> list[TranscriptSegment]:
    """Assign by temporal overlap; ties/overlap/mixed utterances need review.

    Never split ASR text heuristically: splitting timestamps alone would
    duplicate words and invent a word-to-speaker alignment we do not have.
    """
    valid = sorted((t for t in turns if math.isfinite(t.start) and math.isfinite(t.end)
                    and t.end > t.start and t.start >= 0 and t.speaker_id), key=lambda t: t.start)
    names = {name: f"speaker_{i}" for i, name in enumerate(dict.fromkeys(t.speaker_id for t in valid))}
    result = []
    for segment in segments:
        overlaps: dict[str, float] = {}
        for turn in valid:
            overlap = max(0.0, min(segment.end, turn.end) - max(segment.start, turn.start))
            if overlap:
                overlaps[turn.speaker_id] = overlaps.get(turn.speaker_id, 0.0) + overlap
        ranked = sorted(overlaps, key=lambda name: overlaps[name], reverse=True)
        if not ranked:
            result.append(replace(segment, speaker_review=True))
            continue
        best = ranked[0]
        uncertain = overlaps[best] < segment.duration * 0.6 or (
            len(ranked) > 1 and overlaps[ranked[1]] >= min(0.2, segment.duration * 0.1))
        result.append(replace(segment, speaker_id=names[best], speaker_review=uncertain))
    return result


class PyannoteDiarizer:
    def __init__(self, model_path: str, *, device: str = "cpu", max_speakers: int = 8) -> None:
        self.configured = bool(model_path.strip())
        self.model_path = Path(model_path).expanduser()
        self.device = device
        self.max_speakers = max_speakers

    def diarize(self, audio_path: Path) -> list[SpeakerTurn]:
        if not self.configured or not self.model_path.is_dir():
            raise RuntimeError("Download an accepted pyannote community-1 model locally and set DIARIZATION_MODEL_PATH.")
        try:
            import torch
            from pyannote.audio import Pipeline
        except ImportError as exc:
            raise RuntimeError("Install requirements-diarization.txt to enable automatic speaker detection.") from exc
        with _INFERENCE_LOCK:
            _logger.info("Running local speaker detection on %s", self.device)
            pipeline = Pipeline.from_pretrained(str(self.model_path))
            pipeline.to(torch.device(self.device))
            output = pipeline(str(audio_path), max_speakers=self.max_speakers)
            # Real annotation preserves overlaps so assign_speakers can flag them.
            annotation = getattr(output, "speaker_diarization", output)
            return [SpeakerTurn(float(turn.start), float(turn.end), str(speaker))
                    for turn, _, speaker in annotation.itertracks(yield_label=True)]
