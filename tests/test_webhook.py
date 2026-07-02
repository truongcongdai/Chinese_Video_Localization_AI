# tests/test_webhook.py
import pytest
from unittest.mock import Mock, patch
from universal_video_ai.webhook import WebhookService, WebhookEvent


def test_webhook_send_success():
    """Test successful webhook send."""
    service = WebhookService()

    with patch("requests.post") as mock_post:
        mock_post.return_value = Mock(status_code=200)

        result = service.send(
            webhook_url="https://example.com/webhook",
            event=WebhookEvent.JOB_COMPLETED,
            job_id="job_123",
            user_id=999,
            status="completed",
            url="https://youtube.com/watch?v=123",
            duration_seconds=120.5,
        )

        assert result is True
        mock_post.assert_called_once()


def test_webhook_send_failed():
    """Test webhook send failure."""
    service = WebhookService(max_retries=1)

    with patch("requests.post") as mock_post:
        mock_post.side_effect = Exception("Network error")

        result = service.send(
            webhook_url="https://example.com/webhook",
            event=WebhookEvent.JOB_FAILED,
            job_id="job_123",
            user_id=999,
            status="failed",
            url="https://example.com/video",
            error_message="Download failed",
        )

        assert result is False


def test_webhook_event_types():
    """Test webhook event types."""
    assert WebhookEvent.JOB_STARTED.value == "job.started"
    assert WebhookEvent.JOB_COMPLETED.value == "job.completed"
    assert WebhookEvent.JOB_FAILED.value == "job.failed"