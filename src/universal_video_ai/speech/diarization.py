"""Optional local diarization; no model import/download in single-voice mode."""
from __future__ import annotations

from dataclasses import dataclass, replace
from importlib.metadata import PackageNotFoundError, version
import logging
import math
import os
from pathlib import Path
import threading
from typing import Protocol, Sequence

from universal_video_ai.segment import TranscriptSegment

__all__ = ["SpeakerTurn", "Diarizer", "PyannoteDiarizer", "assign_speakers", "diarization_readiness"]
_logger = logging.getLogger(__name__)
_INFERENCE_LOCK = threading.Lock()
_PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _local_model_path(value: str) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else _PROJECT_ROOT / path


def diarization_readiness() -> dict:
    """Lightweight setup check; actual model loading happens only during a job."""
    try:
        version("pyannote.audio")
        installed = True
    except PackageNotFoundError:
        installed = False
    configured = os.getenv("DIARIZATION_MODEL_PATH", "").strip()
    model_ready = bool(configured) and (_local_model_path(configured) / "config.yaml").is_file()
    return {"installed": installed, "model_configured": bool(configured),
            "model_ready": model_ready, "ready": installed and model_ready}


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
        self.model_path = _local_model_path(model_path) if self.configured else Path()
        self.device = device
        self.max_speakers = max_speakers

    def diarize(self, audio_path: Path) -> list[SpeakerTurn]:
        if not self.configured or not (self.model_path / "config.yaml").is_file():
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
