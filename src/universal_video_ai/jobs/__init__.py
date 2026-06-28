# src/universal_video_ai/jobs/__init__.py
"""
Job queue service for background video processing.

Provides simple in-memory job tracking and async execution.
"""

from __future__ import annotations

from .models import Job, JobStatus, JobConfig
from .service import JobService

__all__ = [
    "Job",
    "JobStatus",
    "JobConfig",
    "JobService",
]