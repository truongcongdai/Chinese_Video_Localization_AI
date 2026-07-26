from __future__ import annotations

from universal_video_ai.jobs.models import JobStatus
from universal_video_ai.jobs.queue import QueueEnvelope, RedisQueue
from universal_video_ai.jobs.worker import RedisWorker


def test_redis_worker_process_job_completes() -> None:
    queue = RedisQueue(redis_url="redis://127.0.0.1:1/0", namespace="worker-test", fallback=True)
    processed: list[str] = []

    def callback(envelope: QueueEnvelope) -> None:
        processed.append(envelope.job_id)

    queue.enqueue("job1")
    envelope = queue.dequeue()
    assert envelope is not None

    RedisWorker(queue, callback, backoff_multiplier=0).process_job(envelope)

    assert processed == ["job1"]
    assert queue.get_status("job1") == JobStatus.COMPLETED


def test_redis_worker_retries_until_failed() -> None:
    queue = RedisQueue(redis_url="redis://127.0.0.1:1/0", namespace="worker-test", fallback=True)
    attempts: list[str] = []

    def callback(envelope: QueueEnvelope) -> None:
        attempts.append(envelope.job_id)
        raise RuntimeError("boom")

    worker = RedisWorker(queue, callback, max_retries=1, backoff_multiplier=0)
    queue.enqueue("job1")

    first = queue.dequeue()
    assert first is not None
    worker.process_job(first)
    assert queue.get_status("job1") == JobStatus.PENDING

    second = queue.dequeue()
    assert second is not None
    worker.process_job(second)

    assert attempts == ["job1", "job1"]
    assert queue.get_status("job1") == JobStatus.FAILED
