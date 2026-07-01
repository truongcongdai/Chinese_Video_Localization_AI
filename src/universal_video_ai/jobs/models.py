# src/universal_video_ai/jobs/models.py
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional
from datetime import datetime, UTC
import json

__all__ = ["JobStatus", "Job", "JobConfig"]


class JobStatus(Enum):
    """Job execution status."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class JobConfig:
    """Configuration for a job execution."""

    url: str
    output_dir: Path
    run_transcription: bool = False
    transcription_language: Optional[str] = None
    run_translation: bool = False
    target_language: Optional[str] = None
    run_tts: bool = False
    run_demucs: bool = False
    generate_subtitles: bool = False
    mix_audio: bool = False

    def to_dict(self) -> dict:
        """Serialize to dict for storage."""
        return {
            "url": self.url,
            "output_dir": str(self.output_dir),
            "run_transcription": self.run_transcription,
            "transcription_language": self.transcription_language,
            "run_translation": self.run_translation,
            "target_language": self.target_language,
            "run_tts": self.run_tts,
            "run_demucs": self.run_demucs,
            "generate_subtitles": self.generate_subtitles,
            "mix_audio": self.mix_audio,
        }

    @classmethod
    def from_dict(cls, data: dict) -> JobConfig:
        """Deserialize from dict."""
        return cls(
            url=data["url"],
            output_dir=Path(data["output_dir"]),
            run_transcription=data.get("run_transcription", False),
            transcription_language=data.get("transcription_language"),
            run_translation=data.get("run_translation", False),
            target_language=data.get("target_language"),
            run_tts=data.get("run_tts", False),
            run_demucs=data.get("run_demucs", False),
            generate_subtitles=data.get("generate_subtitles", False),
            mix_audio=data.get("mix_audio", False),
        )


@dataclass
class Job:
    """Represents a background job."""

    job_id: str
    config: JobConfig
    status: JobStatus = JobStatus.PENDING
    progress: float = 0.0  # 0-1
    message: str = ""
    result_path: Optional[Path] = None
    error: Optional[str] = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    duration_seconds: float = 0.0

    def to_dict(self) -> dict:
        """Serialize to dict for storage."""
        return {
            "job_id": self.job_id,
            "config": self.config.to_dict(),
            "status": self.status.value,
            "progress": self.progress,
            "message": self.message,
            "result_path": str(self.result_path) if self.result_path else None,
            "error": self.error,
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "duration_seconds": self.duration_seconds,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Job:
        """Deserialize from dict."""
        return cls(
            job_id=data["job_id"],
            config=JobConfig.from_dict(data["config"]),
            status=JobStatus(data.get("status", "pending")),
            progress=data.get("progress", 0.0),
            message=data.get("message", ""),
            result_path=Path(data["result_path"]) if data.get("result_path") else None,
            error=data.get("error"),
            created_at=datetime.fromisoformat(data["created_at"]),
            started_at=datetime.fromisoformat(data["started_at"]) if data.get("started_at") else None,
            completed_at=datetime.fromisoformat(data["completed_at"]) if data.get("completed_at") else None,
            duration_seconds=data.get("duration_seconds", 0.0),
        )