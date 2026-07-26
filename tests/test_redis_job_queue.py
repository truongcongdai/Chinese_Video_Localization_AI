from __future__ import annotations

from universal_video_ai.jobs.models import JobStatus
from universal_video_ai.jobs.queue import JobPriority, RedisQueue


def test_redis_queue_fallback_enqueue_dequeue_priority() -> None:
    queue = RedisQueue(redis_url="redis://127.0.0.1:1/0", namespace="test", fallback=True)

    assert queue.enqueue("low", {"value": 1}, JobPriority.LOW) is True
    assert queue.enqueue("urgent", {"value": 2}, JobPriority.URGENT) is True

    envelope = queue.dequeue()

    assert envelope is not None
    assert envelope.job_id == "urgent"
    assert envelope.payload == {"value": 2}
    assert queue.get_status("urgent") == JobStatus.RUNNING


def test_redis_queue_cancel_removes_pending_job() -> None:
    queue = RedisQueue(redis_url="redis://127.0.0.1:1/0", namespace="test", fallback=True)
    queue.enqueue("job1", priority=JobPriority.NORMAL)

    assert queue.cancel("job1") is True

    assert queue.get_status("job1") == JobStatus.CANCELLED
    assert queue.dequeue() is None


def test_redis_queue_requeue_tracks_attempts() -> None:
    queue = RedisQueue(redis_url="redis://127.0.0.1:1/0", namespace="test", fallback=True)
    queue.enqueue("job1", {"kind": "test"}, JobPriority.HIGH)
    envelope = queue.dequeue()

    assert envelope is not None

    queue.requeue(envelope, delay_seconds=0)
    retried = queue.dequeue()

    assert retried is not None
    assert retried.job_id == "job1"
    assert retried.attempts == 1
