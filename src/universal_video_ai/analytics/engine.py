# src/universal_video_ai/analytics/engine.py
"""
Analytics engine for revenue reports and insights.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Dict, Optional
import logging

__all__ = ["AnalyticsEngine", "RevenueReport"]

_logger = logging.getLogger(__name__)


@dataclass
class RevenueReport:
    """Revenue report for period."""
    period_start: float
    period_end: float
    total_jobs: int
    completed_jobs: int
    failed_jobs: int
    total_revenue: float
    avg_revenue_per_job: float
    unique_users: int
    top_users: List[tuple[int, float]]  # (user_id, revenue)


class AnalyticsEngine:
    """
    Generate analytics reports from metrics and database.

    Assumes 1 credit = $0.01 USD or configurable rate.
    """

    def __init__(self, credit_to_usd: float = 0.01, logger: Optional[logging.Logger] = None) -> None:
        self.credit_to_usd = credit_to_usd
        self.logger = logger or _logger

    def generate_daily_report(self, metrics_list: List, start_timestamp: float, end_timestamp: float) -> RevenueReport:
        """Generate daily revenue report."""
        jobs = [m for m in metrics_list if start_timestamp <= m.started_at <= end_timestamp]
        completed = [j for j in jobs if j.status.value == "completed"]
        failed = [j for j in jobs if j.status.value == "failed"]

        total_revenue = sum(j.credits_used for j in completed) * self.credit_to_usd
        avg_revenue = total_revenue / len(completed) if completed else 0.0
        unique_users = len(set(j.user_id for j in completed))

        # Top users by revenue
        user_revenue: Dict[int, float] = {}
        for job in completed:
            user_revenue[job.user_id] = user_revenue.get(job.user_id, 0) + job.credits_used
        top_users = sorted(user_revenue.items(), key=lambda x: x[1], reverse=True)[:10]

        return RevenueReport(
            period_start=start_timestamp,
            period_end=end_timestamp,
            total_jobs=len(jobs),
            completed_jobs=len(completed),
            failed_jobs=len(failed),
            total_revenue=total_revenue,
            avg_revenue_per_job=avg_revenue,
            unique_users=unique_users,
            top_users=top_users,
        )

    def to_dict(self, report: RevenueReport) -> Dict:
        """Convert report to dictionary."""
        return {
            "period_start": report.period_start,
            "period_end": report.period_end,
            "total_jobs": report.total_jobs,
            "completed_jobs": report.completed_jobs,
            "failed_jobs": report.failed_jobs,
            "success_rate": report.completed_jobs / report.total_jobs if report.total_jobs > 0 else 0.0,
            "total_revenue_usd": round(report.total_revenue, 2),
            "avg_revenue_per_job_usd": round(report.avg_revenue_per_job, 2),
            "unique_users": report.unique_users,
            "top_users": [(uid, round(rev * self.credit_to_usd, 2)) for uid, rev in report.top_users],
        }