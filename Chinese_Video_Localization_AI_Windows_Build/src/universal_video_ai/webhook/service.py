# src/universal_video_ai/webhook/service.py
"""
Webhook service for job notifications.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional
import logging
import json
import time

__all__ = ["WebhookService", "WebhookEvent"]

_logger = logging.getLogger(__name__)


class WebhookEvent(Enum):
    """Webhook event types."""
    JOB_STARTED = "job.started"
    JOB_COMPLETED = "job.completed"
    JOB_FAILED = "job.failed"


@dataclass
class WebhookPayload:
    """Webhook payload."""
    event: str
    job_id: str
    user_id: int
    status: str
    url: str
    timestamp: float
    error_message: Optional[str] = None
    duration_seconds: Optional[float] = None


class WebhookService:
    """
    Send webhook notifications for job events.

    Supports retry with exponential backoff.
    """

    def __init__(self, timeout_seconds: int = 5, max_retries: int = 3, logger: Optional[logging.Logger] = None) -> None:
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.logger = logger or _logger

    def send(
            self,
            webhook_url: str,
            event: WebhookEvent,
            job_id: str,
            user_id: int,
            status: str,
            url: str,
            error_message: Optional[str] = None,
            duration_seconds: Optional[float] = None,
    ) -> bool:
        """Send webhook notification with retry."""
        payload = {
            "event": event.value,
            "job_id": job_id,
            "user_id": user_id,
            "status": status,
            "url": url,
            "timestamp": time.time(),
            "error_message": error_message,
            "duration_seconds": duration_seconds,
        }

        for attempt in range(self.max_retries):
            try:
                import requests
                response = requests.post(
                    webhook_url,
                    json=payload,
                    timeout=self.timeout_seconds,
                )
                if 200 <= response.status_code < 300:
                    self.logger.info("Webhook sent successfully: event=%s job_id=%s", event.value, job_id)
                    return True
                else:
                    self.logger.warning("Webhook returned status %d", response.status_code)
            except Exception as exc:
                self.logger.warning("Webhook attempt %d failed: %s", attempt + 1, exc)
                if attempt < self.max_retries - 1:
                    time.sleep(2 ** attempt)  # Exponential backoff

        self.logger.error("Webhook delivery failed after %d attempts: job_id=%s", self.max_retries, job_id)
        return False