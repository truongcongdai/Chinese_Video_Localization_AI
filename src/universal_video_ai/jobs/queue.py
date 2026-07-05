# src/universal_video_ai/jobs/queue.py
"""
Job queue implementation with priority support.
"""

from __future__ import annotations

import logging
import queue
import threading
from typing import Optional, Callable, Any
from dataclasses import dataclass, field
from enum import Enum
import time

from .models import Job, JobStatus

__all__ = ["JobQueue", "JobPriority", "QueuedJob"]

_logger = logging.getLogger(__name__)


class JobPriority(Enum):
    """Job priority levels."""
    LOW = 1
    NORMAL = 2
    HIGH = 3
    URGENT = 4


@dataclass(order=True)
class QueuedJob:
    """Job with priority for queue ordering."""
    priority: int
    job_id: str
    callback: Callable[[str], Any] = field(compare=False)
    created_at: float = field(default_factory=time.time, compare=False)


class JobQueue:
    """
    Thread-safe job queue with priority support.
    
    Responsibilities:
    - Queue jobs with priority levels
    - Process jobs in background worker threads
    - Handle job failures and retries
    - Provide queue statistics
    """

    def __init__(
        self,
        max_workers: int = 4,
        max_retries: int = 3,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.max_workers = max_workers
        self.max_retries = max_retries
        self.logger = logger or _logger
        
        self._queue: queue.PriorityQueue[QueuedJob] = queue.PriorityQueue()
        self._workers: list[threading.Thread] = []
        self._running = False
        self._job_retries: dict[str, int] = {}
        
        self.logger.debug("JobQueue initialized max_workers=%d max_retries=%d", max_workers, max_retries)

    def start(self) -> None:
        """Start worker threads."""
        if self._running:
            self.logger.warning("JobQueue already running")
            return
        
        self._running = True
        for i in range(self.max_workers):
            worker = threading.Thread(target=self._worker, name=f"JobQueue-Worker-{i}", daemon=True)
            worker.start()
            self._workers.append(worker)
        
        self.logger.info("JobQueue started with %d workers", self.max_workers)

    def stop(self) -> None:
        """Stop worker threads gracefully."""
        self._running = False
        
        # Wake up all workers
        for _ in range(self.max_workers):
            self._queue.put(QueuedJob(priority=0, job_id="STOP", callback=lambda _: None))
        
        # Wait for workers to finish
        for worker in self._workers:
            worker.join(timeout=5.0)
        
        self._workers.clear()
        self.logger.info("JobQueue stopped")

    def enqueue(
        self,
        job_id: str,
        callback: Callable[[str], Any],
        priority: JobPriority = JobPriority.NORMAL,
    ) -> bool:
        """
        Add job to queue.
        
        :param job_id: Job identifier
        :param callback: Function to execute (receives job_id)
        :param priority: Job priority level
        :return: True if enqueued successfully
        """
        if not self._running:
            self.logger.error("JobQueue not running, cannot enqueue job %s", job_id)
            return False
        
        queued_job = QueuedJob(
            priority=priority.value,
            job_id=job_id,
            callback=callback,
        )
        
        self._queue.put(queued_job)
        self.logger.info("JobQueue: enqueued job %s with priority %s", job_id, priority.name)
        return True

    def _worker(self) -> None:
        """Worker thread that processes jobs from queue."""
        while self._running:
            try:
                # Get job with timeout to allow checking _running flag
                queued_job = self._queue.get(timeout=1.0)
                
                # Check for stop signal
                if queued_job.job_id == "STOP":
                    self._queue.task_done()
                    continue
                
                self.logger.debug("JobQueue worker processing job %s", queued_job.job_id)
                
                # Execute callback
                try:
                    queued_job.callback(queued_job.job_id)
                    # Reset retry count on success
                    self._job_retries.pop(queued_job.job_id, None)
                    self._queue.task_done()
                except Exception as exc:
                    self.logger.exception("JobQueue worker failed for job %s: %s", queued_job.job_id, exc)
                    
                    # Retry logic
                    retry_count = self._job_retries.get(queued_job.job_id, 0)
                    if retry_count < self.max_retries:
                        self._job_retries[queued_job.job_id] = retry_count + 1
                        self.logger.warning("JobQueue: retrying job %s (attempt %d/%d)", 
                                          queued_job.job_id, retry_count + 1, self.max_retries)
                        # Re-queue with same priority (don't call task_done yet)
                        self._queue.put(queued_job)
                    else:
                        self.logger.error("JobQueue: job %s failed after %d retries", 
                                         queued_job.job_id, self.max_retries)
                        self._job_retries.pop(queued_job.job_id, None)
                        self._queue.task_done()
                
            except queue.Empty:
                # Timeout, continue loop to check _running flag
                continue
            except Exception as exc:
                self.logger.exception("JobQueue worker error: %s", exc)

    def get_stats(self) -> dict[str, Any]:
        """Get queue statistics."""
        return {
            "queue_size": self._queue.qsize(),
            "running": self._running,
            "workers": len(self._workers),
            "job_retries": len(self._job_retries),
        }

    def clear(self) -> None:
        """Clear all pending jobs."""
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
                self._queue.task_done()
            except queue.Empty:
                break
        self.logger.info("JobQueue cleared")
