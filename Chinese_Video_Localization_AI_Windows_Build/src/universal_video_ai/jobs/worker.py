from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Optional, Protocol, runtime_checkable

from .models import JobStatus
from .queue import QueueEnvelope, RedisQueue

__all__ = ["WorkerProtocol", "RedisWorker"]

_logger = logging.getLogger(__name__)


@runtime_checkable
class WorkerProtocol(Protocol):
    """Protocol for background job processors."""

    def start(self) -> None:
        """Start processing queued jobs."""
        ...

    def stop(self) -> None:
        """Stop processing queued jobs."""
        ...

    def process_job(self, envelope: QueueEnvelope) -> None:
        """Process one queued job."""
        ...


class RedisWorker:
    """Polling worker for RedisQueue jobs with retry/backoff support."""

    def __init__(
        self,
        queue: RedisQueue,
        callback: Callable[[QueueEnvelope], Any],
        *,
        poll_interval_seconds: float = 1.0,
        max_retries: int = 3,
        backoff_multiplier: float = 2.0,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.queue = queue
        self.callback = callback
        self.poll_interval_seconds = poll_interval_seconds
        self.max_retries = max_retries
        self.backoff_multiplier = backoff_multiplier
        self.logger = logger or _logger
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        """Start the polling worker."""
        if self._running:
            self.logger.warning("RedisWorker already running")
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, name="RedisWorker", daemon=True)
        self._thread.start()
        self.logger.info("RedisWorker started")

    def stop(self) -> None:
        """Stop the polling worker."""
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None
        self.logger.info("RedisWorker stopped")

    def process_job(self, envelope: QueueEnvelope) -> None:
        """Process one queued job and update queue status."""
        if self.queue.get_status(envelope.job_id) == JobStatus.CANCELLED:
            self.logger.info("Skipping cancelled job %s", envelope.job_id)
            return

        self.queue.mark_status(envelope.job_id, JobStatus.RUNNING, attempts=envelope.attempts)
        try:
            self.callback(envelope)
            if self.queue.get_status(envelope.job_id) != JobStatus.CANCELLED:
                self.queue.mark_status(envelope.job_id, JobStatus.COMPLETED)
        except Exception as exc:
            next_attempt = envelope.attempts + 1
            if next_attempt <= self.max_retries:
                delay = self._retry_delay(next_attempt)
                self.logger.warning(
                    "Job %s failed, retrying attempt %d/%d after %.2fs: %s",
                    envelope.job_id,
                    next_attempt,
                    self.max_retries,
                    delay,
                    exc,
                )
                self.queue.requeue(envelope, delay_seconds=delay)
                return
            self.logger.exception("Job %s failed after %d retries: %s", envelope.job_id, self.max_retries, exc)
            self.queue.mark_status(
                envelope.job_id,
                JobStatus.FAILED,
                error=str(exc),
                attempts=next_attempt,
            )

    def _run(self) -> None:
        while self._running:
            envelope = self.queue.dequeue(timeout_seconds=self.poll_interval_seconds)
            if envelope is None:
                continue
            self.process_job(envelope)

    def _retry_delay(self, attempt: int) -> float:
        if self.backoff_multiplier <= 0:
            return 0.0
        return max(0.0, self.backoff_multiplier ** max(attempt - 1, 0))
