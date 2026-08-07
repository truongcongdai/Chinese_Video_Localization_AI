# src/universal_video_ai/jobs/batch.py
"""
Batch job processing for multiple videos.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional
import logging
import csv
from io import StringIO

__all__ = ["BatchJob", "BatchStatus", "BatchProcessor"]

_logger = logging.getLogger(__name__)


class BatchStatus(Enum):
    """Batch job status."""
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class BatchJob:
    """Batch job container."""
    batch_id: str
    user_id: int
    total_videos: int
    completed_videos: int = 0
    failed_videos: int = 0
    status: BatchStatus = BatchStatus.PENDING
    video_urls: List[str] = field(default_factory=list)
    results: List[dict] = field(default_factory=list)

    def progress_percent(self) -> float:
        """Get progress percentage."""
        if self.total_videos == 0:
            return 0.0
        return ((self.completed_videos + self.failed_videos) / self.total_videos) * 100


class BatchProcessor:
    """
    Process multiple videos in batch.

    Max 2 concurrent jobs at a time.
    """

    def __init__(self, max_concurrent: int = 2, logger: Optional[logging.Logger] = None) -> None:
        self.max_concurrent = max_concurrent
        self.logger = logger or _logger
        self._batches: dict[str, BatchJob] = {}

    def parse_csv(self, csv_content: str) -> List[str]:
        """Parse CSV with list of URLs."""
        urls = []
        reader = csv.DictReader(StringIO(csv_content))
        for row in reader:
            url = row.get("url", "").strip()
            if url:
                urls.append(url)
        return urls

    def create_batch(self, batch_id: str, user_id: int, video_urls: List[str]) -> BatchJob:
        """Create new batch job."""
        batch = BatchJob(
            batch_id=batch_id,
            user_id=user_id,
            total_videos=len(video_urls),
            video_urls=video_urls,
        )
        self._batches[batch_id] = batch
        self.logger.info("Batch created: batch_id=%s user_id=%s total_videos=%d", batch_id, user_id, len(video_urls))
        return batch

    def get_batch(self, batch_id: str) -> Optional[BatchJob]:
        """Get batch status."""
        return self._batches.get(batch_id)

    def record_completion(self, batch_id: str, success: bool = True) -> None:
        """Record video completion in batch."""
        batch = self._batches.get(batch_id)
        if batch is None:
            return

        if success:
            batch.completed_videos += 1
        else:
            batch.failed_videos += 1

        if batch.completed_videos + batch.failed_videos == batch.total_videos:
            batch.status = BatchStatus.COMPLETED
            self.logger.info("Batch completed: batch_id=%s completed=%d failed=%d",
                             batch_id, batch.completed_videos, batch.failed_videos)