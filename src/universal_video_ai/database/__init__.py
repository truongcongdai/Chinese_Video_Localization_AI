# src/universal_video_ai/database/__init__.py
"""
Database helpers for Universal Video AI.

Exports:
- DatabaseManager
- DownloadRecord
"""
from .manager import DatabaseManager, DownloadRecord

__all__ = ["DatabaseManager", "DownloadRecord"]