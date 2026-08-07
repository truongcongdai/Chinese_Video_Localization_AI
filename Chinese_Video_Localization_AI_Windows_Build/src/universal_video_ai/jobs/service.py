# src/universal_video_ai/jobs/service.py
from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Optional, List, Dict
from datetime import datetime, timezone
from threading import Thread
import json

from .models import Job, JobStatus, JobConfig
from .queue import JobQueue, JobPriority

__all__ = ["JobService", "JobQueue"]

_logger = logging.getLogger(__name__)


class JobService:
    """In-memory job service for background processing.

    Responsibilities:
    - Track job status and progress
    - Provide job history
    - Run jobs in background threads (simple, no celery)
    - Optional queue-based processing with priority support
    """

    def __init__(
        self,
        use_queue: bool = False,
        max_workers: int = 4,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.jobs: Dict[str, Job] = {}  # job_id -> Job
        self.logger = logger or _logger
        self.use_queue = use_queue
        
        # Initialize queue if enabled
        self.queue: Optional[JobQueue] = None
        if use_queue:
            self.queue = JobQueue(max_workers=max_workers, logger=self.logger)
            self.queue.start()
        
        self.logger.debug("JobService initialized use_queue=%s max_workers=%d", use_queue, max_workers)

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
            started_at=job.started_at or (datetime.now(timezone.utc) if status == JobStatus.RUNNING else None),
            completed_at=job.completed_at or (
                datetime.now(timezone.utc) if status in (JobStatus.COMPLETED, JobStatus.FAILED) else None),
            duration_seconds=job.duration_seconds,
        )

        # Calculate duration if job finished
        if updated_job.completed_at and updated_job.started_at:
            updated_job.duration_seconds = (updated_job.completed_at - updated_job.started_at).total_seconds()

        self.jobs[job_id] = updated_job
        self.logger.debug("JobService: updated job %s status=%s progress=%.1f%%", job_id, status,
                          progress * 100 if progress else 0)
        return updated_job

    def run_job_async(self, job_id: str, callback, priority: JobPriority = JobPriority.NORMAL) -> Optional[Thread]:
        """
        Run a job in a background thread or queue.

        :param job_id: ID of the job to run
        :param callback: function that accepts job_id and returns result/error
        :param priority: Job priority (only used if queue enabled)
        :return: Thread if not using queue, None if using queue
        """
        job = self.jobs.get(job_id)
        if job is None:
            raise ValueError(f"Job {job_id} not found")

        # Use queue if enabled
        if self.use_queue and self.queue:
            self.logger.info("JobService: enqueuing job %s with priority %s", job_id, priority.name)
            
            def _queue_callback(jid: str) -> None:
                """Callback wrapper for queue processing."""
                try:
                    self.update_job(jid, status=JobStatus.RUNNING)
                    result = callback(jid)
                    self.update_job(
                        jid,
                        status=JobStatus.COMPLETED,
                        progress=1.0,
                        message="Completed successfully",
                        result_path=result if isinstance(result, Path) else None,
                    )
                except Exception as exc:
                    self.logger.exception("JobService: job %s failed: %s", jid, exc)
                    self.update_job(jid, status=JobStatus.FAILED, error=str(exc))
            
            self.queue.enqueue(job_id, _queue_callback, priority)
            return None

        # Use simple thread if queue not enabled
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
                    result_path=result if isinstance(result, Path) else None,
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
            "exported_at": datetime.now(timezone.utc).isoformat(),
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

    def shutdown(self) -> None:
        """Shutdown queue if enabled."""
        if self.queue:
            self.queue.stop()
            self.logger.info("JobService: queue shutdown complete")

    def get_queue_stats(self) -> Optional[dict]:
        """Get queue statistics if queue enabled."""
        if self.queue:
            return self.queue.get_stats()
        return None