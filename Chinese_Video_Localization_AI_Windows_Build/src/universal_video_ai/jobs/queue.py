# src/universal_video_ai/jobs/queue.py
"""
Job queue implementation with priority support.
"""

from __future__ import annotations

import logging
import json
import queue
import threading
from typing import Optional, Callable, Any, Protocol, runtime_checkable
from dataclasses import dataclass, field
from enum import Enum
import time

from .models import JobStatus

__all__ = [
    "JobQueue",
    "JobPriority",
    "JobQueueProtocol",
    "QueueEnvelope",
    "QueuedJob",
    "RedisQueue",
]

_logger = logging.getLogger(__name__)
_QUEUE_SCORE_SCALE = 100.0


class JobPriority(Enum):
    """Job priority levels."""
    LOW = 1
    NORMAL = 2
    HIGH = 3
    URGENT = 4


@dataclass(frozen=True)
class QueueEnvelope:
    """Serializable queue item used by Redis-backed workers."""

    job_id: str
    payload: dict[str, Any] = field(default_factory=dict)
    priority: JobPriority = JobPriority.NORMAL
    attempts: int = 0
    enqueued_at: float = field(default_factory=time.time)


@runtime_checkable
class JobQueueProtocol(Protocol):
    """Protocol for durable job queue operations."""

    def enqueue(
        self,
        job_id: str,
        payload: Optional[dict[str, Any]] = None,
        priority: JobPriority = JobPriority.NORMAL,
    ) -> bool:
        """Add a job to the queue and persist its initial status."""
        ...

    def dequeue(self, timeout_seconds: float = 0.0) -> Optional[QueueEnvelope]:
        """Return the next queued job, or None when no job is available."""
        ...

    def get_status(self, job_id: str) -> Optional[JobStatus]:
        """Return the persisted status for a job if it exists."""
        ...

    def cancel(self, job_id: str) -> bool:
        """Mark a job as cancelled and remove it from the pending queue."""
        ...


class RedisQueue:
    """Redis-backed queue with priority ordering and in-memory fallback."""

    def __init__(
        self,
        redis_url: str = "redis://127.0.0.1:6379/0",
        namespace: str = "universal_video_ai:jobs",
        client: Optional[Any] = None,
        fallback: bool = True,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.namespace = namespace.rstrip(":")
        self.logger = logger or _logger
        self._redis = client
        self._fallback_enabled = fallback
        self._fallback_queue: list[QueueEnvelope] = []
        self._fallback_jobs: dict[str, dict[str, Any]] = {}
        self._using_fallback = client is None

        if client is None:
            try:
                import redis

                self._redis = redis.from_url(redis_url, decode_responses=True)
                self._redis.ping()
                self._using_fallback = False
            except Exception as exc:
                if not fallback:
                    raise RuntimeError(f"Redis queue unavailable: {exc}") from exc
                self.logger.warning("Redis queue unavailable, using in-memory fallback: %s", exc)

    @property
    def pending_key(self) -> str:
        """Redis sorted set key for pending jobs."""
        return f"{self.namespace}:pending"

    def job_key(self, job_id: str) -> str:
        """Redis hash key for a job."""
        return f"{self.namespace}:job:{job_id}"

    def enqueue(
        self,
        job_id: str,
        payload: Optional[dict[str, Any]] = None,
        priority: JobPriority = JobPriority.NORMAL,
    ) -> bool:
        """Add a job to the queue."""
        envelope = QueueEnvelope(job_id=job_id, payload=payload or {}, priority=priority)
        if self._using_fallback:
            self._fallback_jobs[job_id] = self._serialize_job(envelope, JobStatus.PENDING)
            self._fallback_queue.append(envelope)
            self._fallback_queue.sort(key=lambda item: (-item.priority.value, item.enqueued_at))
            return True

        try:
            assert self._redis is not None
            self._redis.hset(self.job_key(job_id), mapping=self._serialize_job(envelope, JobStatus.PENDING))
            self._redis.zadd(self.pending_key, {job_id: self._queue_score(envelope.enqueued_at, priority)})
            return True
        except Exception as exc:
            self.logger.exception("Failed to enqueue job %s: %s", job_id, exc)
            if not self._fallback_enabled:
                return False
            self._using_fallback = True
            return self.enqueue(job_id, payload, priority)

    def dequeue(self, timeout_seconds: float = 0.0) -> Optional[QueueEnvelope]:
        """Return the highest-priority pending job."""
        deadline = time.monotonic() + max(timeout_seconds, 0.0)
        while True:
            envelope = self._dequeue_once()
            if envelope is not None:
                return envelope
            if timeout_seconds <= 0 or time.monotonic() >= deadline:
                return None
            time.sleep(min(0.1, deadline - time.monotonic()))

    def get_status(self, job_id: str) -> Optional[JobStatus]:
        """Return the stored job status."""
        data = self._get_job_data(job_id)
        if not data:
            return None
        try:
            return JobStatus(data["status"])
        except (KeyError, ValueError):
            return None

    def cancel(self, job_id: str) -> bool:
        """Cancel a queued or running job."""
        if self._using_fallback:
            if job_id not in self._fallback_jobs:
                return False
            self._fallback_queue = [item for item in self._fallback_queue if item.job_id != job_id]
            self._fallback_jobs[job_id]["status"] = JobStatus.CANCELLED.value
            return True

        try:
            assert self._redis is not None
            if not self._redis.exists(self.job_key(job_id)):
                return False
            self._redis.zrem(self.pending_key, job_id)
            self._redis.hset(self.job_key(job_id), mapping={"status": JobStatus.CANCELLED.value, "updated_at": time.time()})
            return True
        except Exception as exc:
            self.logger.exception("Failed to cancel job %s: %s", job_id, exc)
            return False

    def mark_status(
        self,
        job_id: str,
        status: JobStatus,
        *,
        error: Optional[str] = None,
        attempts: Optional[int] = None,
    ) -> None:
        """Persist a job status update."""
        update: dict[str, Any] = {"status": status.value, "updated_at": time.time()}
        if error is not None:
            update["error"] = error
        if attempts is not None:
            update["attempts"] = attempts

        if self._using_fallback:
            self._fallback_jobs.setdefault(job_id, {}).update(update)
            return

        assert self._redis is not None
        self._redis.hset(self.job_key(job_id), mapping=update)

    def requeue(self, envelope: QueueEnvelope, delay_seconds: float = 0.0) -> None:
        """Place a failed job back on the pending queue."""
        next_envelope = QueueEnvelope(
            job_id=envelope.job_id,
            payload=envelope.payload,
            priority=envelope.priority,
            attempts=envelope.attempts + 1,
            enqueued_at=time.time() + max(delay_seconds, 0.0),
        )
        self.mark_status(envelope.job_id, JobStatus.PENDING, attempts=next_envelope.attempts)
        if self._using_fallback:
            self._fallback_queue.append(next_envelope)
            self._fallback_queue.sort(key=lambda item: (-item.priority.value, item.enqueued_at))
            return
        assert self._redis is not None
        self._redis.zadd(
            self.pending_key,
            {next_envelope.job_id: self._queue_score(next_envelope.enqueued_at, next_envelope.priority)},
        )

    def _dequeue_once(self) -> Optional[QueueEnvelope]:
        if self._using_fallback:
            now = time.time()
            for index, envelope in enumerate(self._fallback_queue):
                if envelope.enqueued_at <= now:
                    self._fallback_queue.pop(index)
                    if self.get_status(envelope.job_id) == JobStatus.CANCELLED:
                        return None
                    self.mark_status(envelope.job_id, JobStatus.RUNNING)
                    return envelope
            return None

        try:
            assert self._redis is not None
            rows = self._redis.zrange(self.pending_key, 0, 0, withscores=True)
            if not rows:
                return None
            job_id, score = rows[0]
            if float(score) > self._queue_score(time.time(), JobPriority.LOW):
                return None
            self._redis.zrem(self.pending_key, job_id)
            if self.get_status(job_id) == JobStatus.CANCELLED:
                return None
            data = self._get_job_data(job_id)
            if not data:
                return None
            self.mark_status(job_id, JobStatus.RUNNING)
            return self._deserialize_job(data)
        except Exception as exc:
            self.logger.exception("Failed to dequeue job: %s", exc)
            return None

    def _get_job_data(self, job_id: str) -> dict[str, Any]:
        if self._using_fallback:
            return dict(self._fallback_jobs.get(job_id, {}))
        assert self._redis is not None
        return dict(self._redis.hgetall(self.job_key(job_id)))

    @staticmethod
    def _serialize_job(envelope: QueueEnvelope, status: JobStatus) -> dict[str, Any]:
        return {
            "job_id": envelope.job_id,
            "payload": json.dumps(envelope.payload),
            "priority": envelope.priority.name,
            "attempts": envelope.attempts,
            "status": status.value,
            "enqueued_at": envelope.enqueued_at,
            "updated_at": time.time(),
        }

    @staticmethod
    def _deserialize_job(data: dict[str, Any]) -> QueueEnvelope:
        return QueueEnvelope(
            job_id=str(data["job_id"]),
            payload=json.loads(data.get("payload") or "{}"),
            priority=JobPriority[str(data.get("priority", JobPriority.NORMAL.name))],
            attempts=int(data.get("attempts", 0)),
            enqueued_at=float(data.get("enqueued_at", time.time())),
        )

    @staticmethod
    def _queue_score(available_at: float, priority: JobPriority) -> float:
        return (available_at * _QUEUE_SCORE_SCALE) - priority.value


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
