# tests/test_analytics.py
import pytest
import time
from unittest.mock import Mock
from universal_video_ai.analytics import AnalyticsEngine, RevenueReport


def test_analytics_daily_report():
    """Test daily revenue report generation."""
    engine = AnalyticsEngine(credit_to_usd=0.01)

    now = time.time()

    # Create mock jobs
    jobs = [
        Mock(user_id=111, credits_used=1.0, status=Mock(value="completed"), started_at=now),
        Mock(user_id=111, credits_used=1.0, status=Mock(value="completed"), started_at=now),
        Mock(user_id=222, credits_used=1.0, status=Mock(value="completed"), started_at=now),
        Mock(user_id=222, credits_used=2.0, status=Mock(value="failed"), started_at=now),
    ]

    report = engine.generate_daily_report(jobs, now - 100, now + 100)

    assert report.total_jobs == 4
    assert report.completed_jobs == 3
    assert report.failed_jobs == 1
    assert report.unique_users == 2
    assert report.total_revenue == pytest.approx(0.03, 0.001)  # 3 credits * $0.01


def test_analytics_top_users():
    """Test top users by revenue."""
    engine = AnalyticsEngine(credit_to_usd=0.01)

    now = time.time()
    jobs = [
        Mock(user_id=111, credits_used=100.0, status=Mock(value="completed"), started_at=now),
        Mock(user_id=222, credits_used=50.0, status=Mock(value="completed"), started_at=now),
        Mock(user_id=333, credits_used=25.0, status=Mock(value="completed"), started_at=now),
    ]

    report = engine.generate_daily_report(jobs, now - 100, now + 100)

    assert report.top_users[0] == (111, 100.0)
    assert report.top_users[1] == (222, 50.0)
    assert report.top_users[2] == (333, 25.0)


def test_analytics_to_dict():
    """Test report to dict conversion."""
    engine = AnalyticsEngine(credit_to_usd=0.01)

    now = time.time()
    report = RevenueReport(
        period_start=now,
        period_end=now + 3600,
        total_jobs=100,
        completed_jobs=95,
        failed_jobs=5,
        total_revenue=0.95,
        avg_revenue_per_job=0.01,
        unique_users=10,
        top_users=[(111, 50.0)],
    )

    dict_report = engine.to_dict(report)
    assert dict_report["total_jobs"] == 100
    assert dict_report["success_rate"] == pytest.approx(0.95, 0.01)