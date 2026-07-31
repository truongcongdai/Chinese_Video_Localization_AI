"""
AI Content OS - A content creation workflow system.

This package provides an isolated, feature-flagged content creation system
that sits above the existing video localization pipeline.

Feature flag: CONTENT_OS_ENABLED (default: false)
"""

from universal_video_ai.config import CONTENT_OS_ENABLED

__all__ = ["CONTENT_OS_ENABLED"]
