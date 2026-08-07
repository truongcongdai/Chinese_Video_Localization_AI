# src/universal_video_ai/monitoring/metrics.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional
import logging
import threading
import time
from enum import Enum

__all__ = ["MetricsCollector", "JobMetrics", "UserMetrics", "JobStatus"]

_logger = logging.getLogger(__name__)


class JobStatus(Enum):
    """Job execution status."""
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class JobMetrics:
    """Metrics for a single job."""
    job_id: str
    user_id: int
    status: JobStatus
    url: str
    started_at: float
    completed_at: Optional[float] = None
    error_message: Optional[str] = None
    duration_seconds: float = 0.0
    credits_used: float = 0.0

    def to_dict(self) -> Dict:
        return {
            "job_id": self.job_id,
            "user_id": self.user_id,
            "status": self.status.value,
            "url": self.url,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "error_message": self.error_message,
            "duration_seconds": self.duration_seconds,
            "credits_used": self.credits_used,
        }


@dataclass
class UserMetrics:
    """Aggregated metrics for a user."""
    user_id: int
    total_jobs: int = 0
    completed_jobs: int = 0
    failed_jobs: int = 0
    total_credits_used: float = 0.0
    total_duration_seconds: float = 0.0
    average_duration_seconds: float = 0.0
    last_job_at: Optional[float] = None


class MetricsCollector:
    """
    Collects and aggregates job and user metrics for monitoring and analytics.

    Thread-safe with internal locking.
    """

    def __init__(self, logger: Optional[logging.Logger] = None) -> None:
        self.logger = logger or _logger
        self._lock = threading.RLock()

        # Store metrics: job_id -> JobMetrics
        self._jobs: Dict[str, JobMetrics] = {}

        # Store user aggregates: user_id -> UserMetrics
        self._user_stats: Dict[int, UserMetrics] = {}

        self.logger.debug("MetricsCollector initialized")

    def record_job_start(self, job_id: str, user_id: int, url: str) -> None:
        """Record start of a job."""
        with self._lock:
            job = JobMetrics(
                job_id=job_id,
                user_id=user_id,
                status=JobStatus.PROCESSING,
                url=url,
                started_at=time.time(),
            )
            self._jobs[job_id] = job
            self.logger.debug("Job started: job_id=%s user_id=%s url=%s", job_id, user_id, url)

    def record_job_complete(self, job_id: str, credits_used: float = 1.0) -> None:
        """Record successful completion of a job."""
        with self._lock:
            if job_id not in self._jobs:
                self.logger.warning("Job not found for completion: job_id=%s", job_id)
                return

            job = self._jobs[job_id]
            job.status = JobStatus.COMPLETED
            job.completed_at = time.time()
            job.duration_seconds = job.completed_at - job.started_at
            job.credits_used = credits_used

            # Update user stats
            self._update_user_stats(job)
            self.logger.info("Job completed: job_id=%s duration=%.2fs credits=%.2f",
                             job_id, job.duration_seconds, credits_used)

    def record_job_failed(self, job_id: str, error_message: str) -> None:
        """Record failure of a job."""
        with self._lock:
            if job_id not in self._jobs:
                self.logger.warning("Job not found for failure: job_id=%s", job_id)
                return

            job = self._jobs[job_id]
            job.status = JobStatus.FAILED
            job.completed_at = time.time()
            job.duration_seconds = job.completed_at - job.started_at
            job.error_message = error_message

            # Update user stats
            self._update_user_stats(job, failed=True)
            self.logger.error("Job failed: job_id=%s error=%s", job_id, error_message)

    def _update_user_stats(self, job: JobMetrics, failed: bool = False) -> None:
        """Update user aggregate statistics (must hold lock)."""
        user_id = job.user_id
        if user_id not in self._user_stats:
            self._user_stats[user_id] = UserMetrics(user_id=user_id)

        stats = self._user_stats[user_id]
        stats.total_jobs += 1
        stats.total_duration_seconds += job.duration_seconds
        stats.average_duration_seconds = stats.total_duration_seconds / stats.total_jobs
        stats.last_job_at = job.completed_at

        if failed:
            stats.failed_jobs += 1
        else:
            stats.completed_jobs += 1
            stats.total_credits_used += job.credits_used

    def get_job_metrics(self, job_id: str) -> Optional[JobMetrics]:
        """Retrieve metrics for a specific job."""
        with self._lock:
            return self._jobs.get(job_id)

    def get_user_metrics(self, user_id: int) -> UserMetrics:
        """Retrieve aggregated metrics for a user."""
        with self._lock:
            return self._user_stats.get(user_id, UserMetrics(user_id=user_id))

    def get_all_jobs(self, limit: int = 100) -> List[JobMetrics]:
        """Retrieve recent jobs."""
        with self._lock:
            jobs = list(self._jobs.values())
            # Sort by started_at descending (most recent first)
            jobs.sort(key=lambda j: j.started_at, reverse=True)
            return jobs[:limit]

    def get_all_users(self) -> List[UserMetrics]:
        """Retrieve metrics for all users."""
        with self._lock:
            return list(self._user_stats.values())

    def get_summary_stats(self) -> Dict:
        """Get overall system statistics."""
        with self._lock:
            all_jobs = list(self._jobs.values())
            completed = [j for j in all_jobs if j.status == JobStatus.COMPLETED]
            failed = [j for j in all_jobs if j.status == JobStatus.FAILED]

            total_duration = sum(j.duration_seconds for j in completed) if completed else 0.0
            avg_duration = total_duration / len(completed) if completed else 0.0
            total_credits = sum(j.credits_used for j in completed)

            return {
                "total_jobs": len(all_jobs),
                "completed_jobs": len(completed),
                "failed_jobs": len(failed),
                "success_rate": len(completed) / len(all_jobs) if all_jobs else 0.0,
                "total_credits_used": total_credits,
                "average_job_duration_seconds": avg_duration,
                "unique_users": len(self._user_stats),
            }