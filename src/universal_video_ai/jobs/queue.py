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
import math
import uuid
import itertools

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
    claim_token: str = ""


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
    """Atomic claims with renewable leases; fallback capacity is process-local.

    Redis keys for one namespace must live on one Redis instance. A recovered
    callback can run again, so side effects still require their own idempotency.
    """

    _ENQUEUE = """
    if redis.call('EXISTS', KEYS[1]) == 1 then return 0 end
    redis.call('HSET', KEYS[1], unpack(ARGV, 4))
    redis.call('ZADD', KEYS[2], ARGV[2], ARGV[1])
    redis.call('ZADD', KEYS[3], ARGV[2], ARGV[1])
    return 1
    """
    _CLAIM = """
    local now, expires, token = tonumber(ARGV[1]), tonumber(ARGV[2]), ARGV[3]
    local prefix = ARGV[4]
    local ranks = {LOW=1, NORMAL=2, HIGH=3, URGENT=4}
    -- Reclaim a bounded batch. A live worker renews its deadline independently.
    for _, id in ipairs(redis.call('ZRANGEBYSCORE', KEYS[2], '-inf', now, 'LIMIT', 0, 64)) do
      local key = prefix .. ':job:' .. id
      if redis.call('HGET', key, 'status') == 'running' then
        local rank = ranks[redis.call('HGET', key, 'priority')] or 2
        redis.call('HINCRBY', key, 'attempts', 1)
        redis.call('HSET', key, 'status', 'pending', 'enqueued_at', now,
                   'updated_at', now, 'claim_token', '')
        redis.call('ZADD', KEYS[1], now, id)
        redis.call('ZADD', KEYS[rank+2], now, id)
      end
      redis.call('ZREM', KEYS[2], id)
    end
    -- Index legacy pending entries once, without executing an unclaimed job.
    for _, id in ipairs(redis.call('ZRANGE', KEYS[1], 0, 127)) do
      local key = prefix .. ':job:' .. id
      if redis.call('HGET', key, 'queue_version') ~= '2' then
        local rank = ranks[redis.call('HGET', key, 'priority')] or 2
        local at = tonumber(redis.call('HGET', key, 'enqueued_at')) or now
        redis.call('HSET', key, 'queue_version', '2')
        redis.call('ZADD', KEYS[rank+2], at, id)
        redis.call('ZADD', KEYS[1], at, id)
      end
    end
    for rank = 4, 1, -1 do
      local ids = redis.call('ZRANGEBYSCORE', KEYS[rank+2], '-inf', now, 'LIMIT', 0, 1)
      if #ids > 0 then
        local id, key = ids[1], prefix .. ':job:' .. ids[1]
        redis.call('ZREM', KEYS[rank+2], id)
        redis.call('ZREM', KEYS[1], id)
        if redis.call('HGET', key, 'status') == 'pending' then
          redis.call('HSET', key, 'status', 'running', 'claim_token', token,
                     'lease_expires_at', expires, 'updated_at', now)
          redis.call('ZADD', KEYS[2], expires, id)
          return redis.call('HGETALL', key)
        end
      end
    end
    return {}
    """
    _TRANSITION = """
    local state = redis.call('HGET', KEYS[1], 'status')
    if not state or state == 'cancelled' then return 0 end
    if ARGV[1] ~= '' and redis.call('HGET', KEYS[1], 'claim_token') ~= ARGV[1] then return 0 end
    if ARGV[1] ~= '' and (tonumber(redis.call('HGET', KEYS[1], 'lease_expires_at')) or 0) <= tonumber(ARGV[4]) then return 0 end
    redis.call('HSET', KEYS[1], 'status', ARGV[2], 'updated_at', ARGV[4], 'error', ARGV[5])
    if ARGV[6] ~= '' then redis.call('HSET', KEYS[1], 'attempts', ARGV[6]) end
    if ARGV[2] == 'pending' then
      redis.call('HSET', KEYS[1], 'enqueued_at', ARGV[7], 'claim_token', '')
      redis.call('ZADD', KEYS[2], ARGV[7], ARGV[3])
      redis.call('ZADD', KEYS[4], ARGV[7], ARGV[3])
    end
    if ARGV[2] ~= 'running' then redis.call('ZREM', KEYS[3], ARGV[3]) end
    return 1
    """
    _RENEW = """
    if redis.call('HGET', KEYS[1], 'status') ~= 'running' or
       redis.call('HGET', KEYS[1], 'claim_token') ~= ARGV[1] or
       (tonumber(redis.call('HGET', KEYS[1], 'lease_expires_at')) or 0) <= tonumber(ARGV[4]) then return 0 end
    redis.call('HSET', KEYS[1], 'lease_expires_at', ARGV[2])
    redis.call('ZADD', KEYS[2], ARGV[2], ARGV[3])
    return 1
    """
    _CANCEL = """
    if redis.call('EXISTS', KEYS[1]) == 0 then return 0 end
    redis.call('HSET', KEYS[1], 'status', 'cancelled', 'claim_token', '', 'updated_at', ARGV[2])
    for i = 2, #KEYS do redis.call('ZREM', KEYS[i], ARGV[1]) end
    return 1
    """

    def __init__(self, redis_url: str = "redis://127.0.0.1:6379/0",
                 namespace: str = "universal_video_ai:jobs", client: Optional[Any] = None,
                 fallback: bool = True, logger: Optional[logging.Logger] = None,
                 lease_seconds: float = 60.0) -> None:
        if not math.isfinite(lease_seconds) or lease_seconds <= 0:
            raise ValueError("lease_seconds must be finite and positive")
        self.namespace = namespace.rstrip(":")
        self.logger = logger or _logger
        self.lease_seconds = float(lease_seconds)
        self._redis = client
        self._memory_lock = threading.RLock()
        self._fallback_queue: list[QueueEnvelope] = []
        self._fallback_jobs: dict[str, dict[str, Any]] = {}
        self._fallback_leases: dict[str, float] = {}
        self._using_fallback = client is None
        if client is None:
            try:
                import redis
                self._redis = redis.from_url(redis_url, decode_responses=True,
                                             socket_connect_timeout=2, socket_timeout=2)
                self._redis.ping()
                self._using_fallback = False
            except Exception as exc:
                if not fallback:
                    raise RuntimeError("Redis queue unavailable") from exc
                self.logger.warning("Redis queue unavailable; using process-local queue")

    @property
    def pending_key(self) -> str:
        """Sorted set of pending job identifiers."""
        return f"{self.namespace}:pending"

    @property
    def inflight_key(self) -> str:
        """Sorted set of claim expiry timestamps."""
        return f"{self.namespace}:inflight"

    def job_key(self, job_id: str) -> str:
        """Hash containing the job and its current claim token."""
        return f"{self.namespace}:job:{job_id}"

    def _priority_key(self, priority: JobPriority) -> str:
        return f"{self.namespace}:priority:{priority.value}"

    def _claim_keys(self) -> list[str]:
        return [self.pending_key, self.inflight_key, *(self._priority_key(p) for p in JobPriority)]

    def enqueue(self, job_id: str, payload: Optional[dict[str, Any]] = None,
                priority: JobPriority = JobPriority.NORMAL) -> bool:
        """Insert once; never switch storage after an ambiguous Redis failure."""
        envelope = QueueEnvelope(job_id, payload or {}, priority, enqueued_at=time.time())
        data = self._serialize_job(envelope, JobStatus.PENDING)
        if self._using_fallback:
            with self._memory_lock:
                if job_id in self._fallback_jobs:
                    return False
                self._fallback_jobs[job_id] = data
                self._fallback_queue.append(envelope)
                return True
        try:
            flat = [value for pair in data.items() for value in pair]
            return bool(self._redis.eval(self._ENQUEUE, 3, self.job_key(job_id), self.pending_key,
                        self._priority_key(priority), job_id, envelope.enqueued_at, '', *flat))
        except Exception:
            self.logger.exception("Redis enqueue failed for job %s", job_id)
            return False

    def dequeue(self, timeout_seconds: float = 0.0) -> Optional[QueueEnvelope]:
        """Atomically claim a due job, urgent first and FIFO within priority."""
        deadline = time.monotonic() + max(timeout_seconds, 0.0)
        while True:
            result = self._dequeue_once()
            if result is not None:
                return result
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            time.sleep(min(0.1, remaining))

    def _dequeue_once(self) -> Optional[QueueEnvelope]:
        now = time.time()
        token = uuid.uuid4().hex
        if self._using_fallback:
            with self._memory_lock:
                for job_id, expiry in list(self._fallback_leases.items()):
                    data = self._fallback_jobs[job_id]
                    if expiry <= now:
                        self._fallback_leases.pop(job_id, None)
                        data.update(status='pending', enqueued_at=now, claim_token='',
                                    attempts=int(data.get('attempts', 0)) + 1)
                        self._fallback_queue.append(self._deserialize_job(data))
                self._fallback_queue.sort(key=lambda e: (-e.priority.value, e.enqueued_at))
                for index, envelope in enumerate(self._fallback_queue):
                    if envelope.enqueued_at > now:
                        continue
                    data = self._fallback_jobs[envelope.job_id]
                    if data['status'] != 'pending':
                        continue
                    self._fallback_queue.pop(index)
                    data.update(status='running', claim_token=token, lease_expires_at=now+self.lease_seconds)
                    self._fallback_leases[envelope.job_id] = now + self.lease_seconds
                    return self._deserialize_job(data)
                return None
        try:
            rows = self._redis.eval(self._CLAIM, 6, *self._claim_keys(),
                                    now, now+self.lease_seconds, token, self.namespace)
            if not rows:
                return None
            data = dict(zip(rows[::2], rows[1::2]))
            return self._deserialize_job(data)
        except Exception:
            self.logger.exception("Redis dequeue failed")
            return None

    def get_status(self, job_id: str) -> Optional[JobStatus]:
        """Read the persisted current state."""
        data = self._get_job_data(job_id)
        return JobStatus(data['status']) if data else None

    def cancel(self, job_id: str) -> bool:
        """Invalidate the claim and remove pending/inflight entries atomically."""
        if self._using_fallback:
            with self._memory_lock:
                data = self._fallback_jobs.get(job_id)
                if not data:
                    return False
                data.update(status='cancelled', claim_token='')
                self._fallback_leases.pop(job_id, None)
                self._fallback_queue = [e for e in self._fallback_queue if e.job_id != job_id]
                return True
        return bool(self._redis.eval(self._CANCEL, 7, self.job_key(job_id), *self._claim_keys(),
                                     job_id, time.time()))

    def mark_status(self, job_id: str, status: JobStatus, *, error: Optional[str] = None,
                    attempts: Optional[int] = None, claim_token: str = '') -> bool:
        """Transition only a current claim; cancellation is terminal."""
        now = time.time()
        if self._using_fallback:
            with self._memory_lock:
                data = self._fallback_jobs.get(job_id)
                if not data or data['status'] == 'cancelled':
                    return False
                if claim_token and (data.get('claim_token') != claim_token or
                                    float(data.get('lease_expires_at', 0)) <= now):
                    return False
                data.update(status=status.value, updated_at=now, error=error or '')
                if status != JobStatus.RUNNING:
                    self._fallback_leases.pop(job_id, None)
                if attempts is not None:
                    data['attempts'] = attempts
                return True
        return bool(self._redis.eval(self._TRANSITION, 4, self.job_key(job_id), self.pending_key,
                    self.inflight_key, self._priority_key(JobPriority.NORMAL), claim_token,
                    status.value, job_id, now, error or '', '' if attempts is None else attempts, now))

    def renew(self, envelope: QueueEnvelope) -> bool:
        """Extend a live claim; stale workers cannot renew a replacement claim."""
        now = time.time()
        if self._using_fallback:
            with self._memory_lock:
                data = self._fallback_jobs.get(envelope.job_id, {})
                if (data.get('status') != 'running' or data.get('claim_token') != envelope.claim_token
                        or float(data.get('lease_expires_at', 0)) <= now):
                    return False
                data['lease_expires_at'] = now + self.lease_seconds
                self._fallback_leases[envelope.job_id] = now + self.lease_seconds
                return True
        return bool(self._redis.eval(self._RENEW, 2, self.job_key(envelope.job_id), self.inflight_key,
                                     envelope.claim_token, now+self.lease_seconds, envelope.job_id, now))

    def requeue(self, envelope: QueueEnvelope, delay_seconds: float = 0.0) -> bool:
        """Retry a current claim only, preserving cancellation and due time."""
        now = time.time()
        available = now + max(0.0, delay_seconds)
        if self._using_fallback:
            with self._memory_lock:
                if not self.mark_status(envelope.job_id, JobStatus.PENDING,
                                        attempts=envelope.attempts+1, claim_token=envelope.claim_token):
                    return False
                data = self._fallback_jobs[envelope.job_id]
                data.update(enqueued_at=available, claim_token='')
                self._fallback_queue.append(self._deserialize_job(data))
                return True
        return bool(self._redis.eval(self._TRANSITION, 4, self.job_key(envelope.job_id),
                    self.pending_key, self.inflight_key, self._priority_key(envelope.priority),
                    envelope.claim_token, 'pending', envelope.job_id, now, '', envelope.attempts+1, available))

    def _get_job_data(self, job_id: str) -> dict[str, Any]:
        if self._using_fallback:
            with self._memory_lock:
                return dict(self._fallback_jobs.get(job_id, {}))
        return dict(self._redis.hgetall(self.job_key(job_id)))

    @staticmethod
    def _serialize_job(envelope: QueueEnvelope, status: JobStatus) -> dict[str, Any]:
        return dict(job_id=envelope.job_id, payload=json.dumps(envelope.payload),
                    priority=envelope.priority.name, attempts=envelope.attempts,
                    status=status.value, enqueued_at=envelope.enqueued_at,
                    updated_at=time.time(), queue_version=2, claim_token='')

    @staticmethod
    def _deserialize_job(data: dict[str, Any]) -> QueueEnvelope:
        return QueueEnvelope(job_id=str(data['job_id']), payload=json.loads(data.get('payload') or '{}'),
                             priority=JobPriority[str(data.get('priority', 'NORMAL'))],
                             attempts=int(data.get('attempts', 0)), enqueued_at=float(data['enqueued_at']),
                             claim_token=str(data.get('claim_token', '')))


@dataclass(order=True)
class QueuedJob:
    """Job with priority for queue ordering."""
    priority: int
    job_id: str = field(compare=False)
    callback: Callable[[str], Any] = field(compare=False)
    sequence: int = field(default=0)
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
        self._sequence = itertools.count()
        
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
            priority=-priority.value,
            job_id=job_id,
            callback=callback,
            sequence=next(self._sequence),
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
                        # Each get has its own unfinished-task count.
                        self._queue.put(queued_job)
                        self._queue.task_done()
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
