# src/universal_video_ai/webhook/__init__.py
"""
Webhook subsystem for job notifications.
"""

from __future__ import annotations

from .service import WebhookService, WebhookEvent

__all__ = ["WebhookService", "WebhookEvent"]