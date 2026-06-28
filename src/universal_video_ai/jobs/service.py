# src/universal_video_ai/jobs/service.py
from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Optional, List, Dict
from datetime import datetime
from threading import Thread
import json

from .models import Job, JobStatus, JobConfig

__all__ = ["JobService"]

_logger = logging.getLogger(__name__)


class JobService:
    """In-memory job service for background processing.

    Responsibilities:
    - Track job status and progress
    - Provide job history
    - Run jobs in background threads (simple, no celery)
    """

    def __init__(self, logger: Optional[logging.Logger] = None) -> None:
        self.jobs: Dict[str, Job] = {}  # job_id -> Job
        self.logger = logger or _logger
        self.logger.debug("JobService initialized")

    def create_job(self, config: JobConfig) -> Job:
        """Create a new job record."""
        job_id = str(uuid.uuid4())
        job = Job(job_id=job_id, config=config, status=JobStatus.PENDING)
        self.jobs[job_id] = job
        self.logger.info("JobService: created job %s for %s", job_id, config.url)
        return job

    def get_job(self, job_id: str) -> Optional[Job]:
        """Retrieve job by ID."""
        return self.jobs.get(job_id)

    def list_jobs(self, status: Optional[JobStatus] = None) -> List[Job]:
        """List all jobs, optionally filtered by status."""
        if status is None:
            return list(self.jobs.values())
        return [j for j in self.jobs.values() if j.status == status]

    def update_job(
            self,
            job_id: str,
            status: Optional[JobStatus] = None,
            progress: Optional[float] = None,
            message: str = "",
            error: Optional[str] = None,
            result_path: Optional[Path] = None,
    ) -> Job:
        """Update job status and progress."""
        job = self.jobs.get(job_id)
        if job is None:
            raise ValueError(f"Job {job_id} not found")

        # Create updated job
        updated_job = Job(
            job_id=job.job_id,
            config=job.config,
            status=status or job.status,
            progress=progress if progress is not None else job.progress,
            message=message or job.message,
            result_path=result_path or job.result_path,
            error=error or job.error,
            created_at=job.created_at,
            started_at=job.started_at or (datetime.utcnow() if status == JobStatus.RUNNING else None),
            completed_at=job.completed_at or (
                datetime.utcnow() if status in (JobStatus.COMPLETED, JobStatus.FAILED) else None),
            duration_seconds=job.duration_seconds,
        )

        # Calculate duration if job finished
        if updated_job.completed_at and updated_job.started_at:
            updated_job.duration_seconds = (updated_job.completed_at - updated_job.started_at).total_seconds()

        self.jobs[job_id] = updated_job
        self.logger.debug("JobService: updated job %s status=%s progress=%.1f%%", job_id, status,
                          progress * 100 if progress else 0)
        return updated_job

    def run_job_async(self, job_id: str, callback) -> Thread:
        """
        Run a job in a background thread.

        :param job_id: ID of the job to run
        :param callback: async function that accepts job_id and returns result/error
        :return: Thread (started)
        """
        job = self.jobs.get(job_id)
        if job is None:
            raise ValueError(f"Job {job_id} not found")

        def _worker():
            try:
                self.logger.info("JobService: starting job %s", job_id)
                self.update_job(job_id, status=JobStatus.RUNNING)

                # Execute callback
                result = callback(job_id)

                # Mark as complete
                self.update_job(
                    job_id,
                    status=JobStatus.COMPLETED,
                    progress=1.0,
                    message="Completed successfully",
                    result_path=result,
                )
                self.logger.info("JobService: completed job %s", job_id)
            except Exception as exc:
                self.logger.exception("JobService: job %s failed: %s", job_id, exc)
                self.update_job(
                    job_id,
                    status=JobStatus.FAILED,
                    error=str(exc),
                )

        thread = Thread(target=_worker, daemon=True)
        thread.start()
        return thread

    def export_jobs(self, output_path: Path) -> None:
        """Export job history to JSON file."""
        output_path = Path(output_path).resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)

        data = {
            "jobs": [job.to_dict() for job in self.jobs.values()],
            "exported_at": datetime.utcnow().isoformat(),
        }

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

        self.logger.info("JobService: exported %d jobs to %s", len(self.jobs), output_path)

    def import_jobs(self, input_path: Path) -> None:
        """Import job history from JSON file."""
        input_path = Path(input_path).resolve()

        with open(input_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        for job_data in data.get("jobs", []):
            job = Job.from_dict(job_data)
            self.jobs[job.job_id] = job

        self.logger.info("JobService: imported %d jobs from %s", len(data.get("jobs", [])), input_path)