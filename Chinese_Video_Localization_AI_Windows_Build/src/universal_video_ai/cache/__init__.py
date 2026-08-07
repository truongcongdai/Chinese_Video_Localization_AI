# src/universal_video_ai/cache/__init__.py
"""
Caching subsystem for translations, TTS, and job results.
Fallback to in-memory if Redis unavailable.
"""

from __future__ import annotations

from .redis_cache import RedisCache, CacheEntry

__all__ = ["RedisCache", "CacheEntry"]