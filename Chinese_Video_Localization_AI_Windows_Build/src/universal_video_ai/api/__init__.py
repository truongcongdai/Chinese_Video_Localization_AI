# src/universal_video_ai/api/__init__.py
"""
Simple Flask API for admin dashboard.
"""

from __future__ import annotations

from .routes import create_app

__all__ = ["create_app"]