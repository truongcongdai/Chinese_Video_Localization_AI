# src/universal_video_ai/orchestrator/__init__.py
"""
Orchestrator for end-to-end video localization workflow.

Chains download -> audio extraction -> optional separation -> optional transcription.
"""

from __future__ import annotations

from .service import LocalizationService, LocalizationConfig, LocalizationResult

__all__ = [
    "LocalizationService",
    "LocalizationConfig",
    "LocalizationResult",
]