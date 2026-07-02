# tests/test_redis_cache.py
import pytest
from universal_video_ai.cache import RedisCache


def test_redis_cache_set_get():
    """Test cache set and get."""
    cache = RedisCache(fallback=True)
    cache.set("test_key", {"data": "value"}, ttl_seconds=3600)
    result = cache.get("test_key")
    assert result == {"data": "value"}


def test_redis_cache_get_miss():
    """Test cache miss."""
    cache = RedisCache(fallback=True)
    result = cache.get("nonexistent_key")
    assert result is None


def test_redis_cache_delete():
    """Test cache delete."""
    cache = RedisCache(fallback=True)
    cache.set("delete_key", "value")
    assert cache.get("delete_key") == "value"
    cache.delete("delete_key")
    assert cache.get("delete_key") is None


def test_redis_cache_make_key():
    """Test cache key generation."""
    cache = RedisCache(fallback=True)
    key1 = cache.make_key("translate", "zh", "vi", "hello")
    key2 = cache.make_key("translate", "zh", "vi", "hello")
    assert key1 == key2


def test_redis_cache_clear():
    """Test cache clear."""
    cache = RedisCache(fallback=True)
    cache.set("key1", "value1")
    cache.set("key2", "value2")
    cache.clear()
    assert cache.get("key1") is None
    assert cache.get("key2") is None