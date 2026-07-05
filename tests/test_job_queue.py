# tests/test_job_queue.py
import pytest
import time
from pathlib import Path

from universal_video_ai.jobs.queue import JobQueue, JobPriority, QueuedJob


def test_job_queue_start_stop():
    """Test queue can start and stop workers."""
    queue = JobQueue(max_workers=2)
    queue.start()
    
    stats = queue.get_stats()
    assert stats["running"] is True
    assert stats["workers"] == 2
    
    queue.stop()
    stats = queue.get_stats()
    assert stats["running"] is False
    assert stats["workers"] == 0


def test_job_queue_enqueue():
    """Test enqueuing jobs."""
    queue = JobQueue(max_workers=1)
    queue.start()
    
    results = []
    
    def callback(job_id: str):
        results.append(job_id)
    
    success = queue.enqueue("job1", callback, JobPriority.NORMAL)
    assert success is True
    
    queue.stop()


def test_job_queue_priority_ordering():
    """Test that priority field is used for ordering in dataclass."""
    from universal_video_ai.jobs.queue import QueuedJob, JobPriority
    
    job_low = QueuedJob(priority=JobPriority.LOW.value, job_id="low", callback=lambda _: None)
    job_urgent = QueuedJob(priority=JobPriority.URGENT.value, job_id="urgent", callback=lambda _: None)
    job_normal = QueuedJob(priority=JobPriority.NORMAL.value, job_id="normal", callback=lambda _: None)
    job_high = QueuedJob(priority=JobPriority.HIGH.value, job_id="high", callback=lambda _: None)
    
    # Verify ordering by priority
    assert job_low < job_normal < job_high < job_urgent


def test_job_queue_retry_on_failure():
    """Test that failed jobs are retried."""
    queue = JobQueue(max_workers=1, max_retries=2)
    queue.start()
    
    attempts = []
    
    def failing_callback(job_id: str):
        attempts.append(1)
        raise RuntimeError("test error")
    
    queue.enqueue("failing_job", failing_callback, JobPriority.NORMAL)
    
    # Wait for retries (initial + 2 retries = 3 total)
    time.sleep(1.5)
    queue.stop()
    
    # Should have attempted initial + max_retries times
    assert len(attempts) == 3


def test_job_queue_stats():
    """Test queue statistics."""
    queue = JobQueue(max_workers=2)
    queue.start()
    
    def dummy_callback(job_id: str):
        pass
    
    queue.enqueue("job1", dummy_callback)
    queue.enqueue("job2", dummy_callback)
    
    stats = queue.get_stats()
    assert stats["queue_size"] >= 0
    assert stats["running"] is True
    assert stats["workers"] == 2
    
    queue.stop()


def test_job_queue_clear():
    """Test clearing the queue."""
    queue = JobQueue(max_workers=1)
    queue.start()
    
    def dummy_callback(job_id: str):
        pass
    
    queue.enqueue("job1", dummy_callback)
    queue.enqueue("job2", dummy_callback)
    
    queue.clear()
    
    stats = queue.get_stats()
    assert stats["queue_size"] == 0
    
    queue.stop()


def test_queued_job_ordering():
    """Test QueuedJob dataclass ordering by priority."""
    job1 = QueuedJob(priority=1, job_id="low", callback=lambda _: None)
    job2 = QueuedJob(priority=3, job_id="high", callback=lambda _: None)
    
    assert job1 < job2  # Lower priority number comes first
