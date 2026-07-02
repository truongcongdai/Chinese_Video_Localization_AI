# tests/test_monitoring.py
import pytest
import time
from universal_video_ai.monitoring.metrics import (
    MetricsCollector,
    JobStatus,
    JobMetrics,
    UserMetrics,
)


def test_metrics_collector_record_job_start():
    """Test recording job start."""
    collector = MetricsCollector()
    collector.record_job_start("job_123", user_id=999, url="https://example.com/video")

    metrics = collector.get_job_metrics("job_123")
    assert metrics is not None
    assert metrics.job_id == "job_123"
    assert metrics.user_id == 999
    assert metrics.status == JobStatus.PROCESSING
    assert metrics.url == "https://example.com/video"


def test_metrics_collector_record_job_complete():
    """Test recording job completion."""
    collector = MetricsCollector()
    collector.record_job_start("job_123", user_id=999, url="https://example.com/video")

    time.sleep(0.1)
    collector.record_job_complete("job_123", credits_used=1.0)

    metrics = collector.get_job_metrics("job_123")
    assert metrics.status == JobStatus.COMPLETED
    assert metrics.credits_used == 1.0
    assert metrics.duration_seconds >= 0.1
    assert metrics.completed_at is not None


def test_metrics_collector_record_job_failed():
    """Test recording job failure."""
    collector = MetricsCollector()
    collector.record_job_start("job_123", user_id=999, url="https://example.com/video")

    time.sleep(0.05)
    collector.record_job_failed("job_123", error_message="Network error")

    metrics = collector.get_job_metrics("job_123")
    assert metrics.status == JobStatus.FAILED
    assert metrics.error_message == "Network error"
    assert metrics.completed_at is not None


def test_metrics_collector_user_stats():
    """Test user aggregated statistics."""
    collector = MetricsCollector()

    # First job - completed
    collector.record_job_start("job_1", user_id=999, url="url1")
    time.sleep(0.05)
    collector.record_job_complete("job_1", credits_used=1.0)

    # Second job - completed
    collector.record_job_start("job_2", user_id=999, url="url2")
    time.sleep(0.05)
    collector.record_job_complete("job_2", credits_used=1.0)

    # Third job - failed
    collector.record_job_start("job_3", user_id=999, url="url3")
    collector.record_job_failed("job_3", error_message="Error")

    user_stats = collector.get_user_metrics(999)
    assert user_stats.user_id == 999
    assert user_stats.total_jobs == 3
    assert user_stats.completed_jobs == 2
    assert user_stats.failed_jobs == 1
    assert user_stats.total_credits_used == 2.0


def test_metrics_collector_get_all_jobs():
    """Test retrieving all jobs."""
    collector = MetricsCollector()

    for i in range(5):
        collector.record_job_start(f"job_{i}", user_id=999, url=f"url_{i}")
        collector.record_job_complete(f"job_{i}", credits_used=1.0)

    jobs = collector.get_all_jobs(limit=10)
    assert len(jobs) == 5
    # Should be sorted by started_at descending
    assert jobs[0].job_id == "job_4"


def test_metrics_collector_summary_stats():
    """Test overall system statistics."""
    collector = MetricsCollector()

    # 2 successful jobs
    for i in range(2):
        collector.record_job_start(f"job_success_{i}", user_id=111, url="url")
        collector.record_job_complete(f"job_success_{i}", credits_used=1.0)

    # 1 failed job
    collector.record_job_start("job_failed", user_id=222, url="url")
    collector.record_job_failed("job_failed", error_message="Error")

    stats = collector.get_summary_stats()
    assert stats["total_jobs"] == 3
    assert stats["completed_jobs"] == 2
    assert stats["failed_jobs"] == 1
    assert pytest.approx(stats["success_rate"], 0.01) == (2.0 / 3.0)
    assert stats["total_credits_used"] == 2.0
    assert stats["unique_users"] == 2