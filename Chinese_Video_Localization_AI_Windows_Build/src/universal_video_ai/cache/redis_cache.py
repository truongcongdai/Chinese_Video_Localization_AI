# src/universal_video_ai/cache/redis_cache.py
"""
Redis-based caching layer with in-memory fallback.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional
import logging
import json
import hashlib
import time

__all__ = ["RedisCache", "CacheEntry"]

_logger = logging.getLogger(__name__)


@dataclass
class CacheEntry:
    """Cache entry with TTL."""
    key: str
    value: Any
    ttl_seconds: int = 86400  # 24 hours default


class RedisCache:
    """
    Redis-based cache with in-memory fallback.

    If Redis unavailable, uses in-memory dict automatically.
    No need to install Redis on Windows!

    Usage:
        cache = RedisCache()  # Auto fallback to memory
        cache.set("my_key", {"data": "value"}, ttl=3600)
        value = cache.get("my_key")
    """

    def __init__(
            self,
            url: str = "redis://127.0.0.1:6379/0",
            fallback: bool = True,
            logger: Optional[logging.Logger] = None,
    ) -> None:
        self.url = url
        self.logger = logger or _logger
        self._fallback_dict: dict[str, tuple[Any, float]] = {}  # key -> (value, expiry_time)
        self._redis_client = None
        self._use_fallback = False

        # Try to connect to Redis
        try:
            import redis
            self._redis_client = redis.from_url(url, decode_responses=True)
            self._redis_client.ping()
            self.logger.info("✓ Connected to Redis at %s", url)
        except Exception as exc:
            if fallback:
                self.logger.warning("⚠ Redis unavailable, using IN-MEMORY CACHE: %s", exc)
                self._use_fallback = True
            else:
                self.logger.error("✗ Redis unavailable and fallback disabled")
                raise

    def set(self, key: str, value: Any, ttl_seconds: int = 86400) -> bool:
        """Set cache entry with TTL."""
        try:
            if self._use_fallback or not self._redis_client:
                self._fallback_dict[key] = (value, time.time() + ttl_seconds)
                self.logger.debug("Cache SET (memory): key=%s ttl=%d", key, ttl_seconds)
                return True

            # Serialize value to JSON
            serialized = json.dumps(value)
            self._redis_client.setex(key, ttl_seconds, serialized)
            self.logger.debug("Cache SET (redis): key=%s ttl=%d", key, ttl_seconds)
            return True
        except Exception as exc:
            self.logger.error("Cache set failed: %s", exc)
            return False

    def get(self, key: str) -> Optional[Any]:
        """Get cache entry."""
        try:
            if self._use_fallback or not self._redis_client:
                if key in self._fallback_dict:
                    value, expiry = self._fallback_dict[key]
                    if time.time() < expiry:
                        self.logger.debug("Cache GET (memory HIT): key=%s", key)
                        return value
                    else:
                        del self._fallback_dict[key]
                self.logger.debug("Cache GET (memory MISS): key=%s", key)
                return None

            # Get from Redis
            serialized = self._redis_client.get(key)
            if serialized is None:
                self.logger.debug("Cache GET (redis MISS): key=%s", key)
                return None
            value = json.loads(serialized)
            self.logger.debug("Cache GET (redis HIT): key=%s", key)
            return value
        except Exception as exc:
            self.logger.error("Cache get failed: %s", exc)
            return None

    def delete(self, key: str) -> bool:
        """Delete cache entry."""
        try:
            if self._use_fallback or not self._redis_client:
                if key in self._fallback_dict:
                    del self._fallback_dict[key]
                return True

            self._redis_client.delete(key)
            return True
        except Exception as exc:
            self.logger.error("Cache delete failed: %s", exc)
            return False

    def clear(self) -> bool:
        """Clear all cache entries."""
        try:
            if self._use_fallback or not self._redis_client:
                self._fallback_dict.clear()
                self.logger.info("✓ Cache cleared (memory)")
                return True

            self._redis_client.flushdb()
            self.logger.info("✓ Cache cleared (redis)")
            return True
        except Exception as exc:
            self.logger.error("Cache clear failed: %s", exc)
            return False

    def make_key(self, prefix: str, *parts: str) -> str:
        """Create cache key from parts."""
        combined = ":".join([prefix] + list(parts))
        # Hash long keys to keep them short
        if len(combined) > 100:
            return f"{prefix}:{hashlib.sha256(combined.encode()).hexdigest()}"
        return combined