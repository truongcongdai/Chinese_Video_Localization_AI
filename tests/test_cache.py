# tests/test_cache.py
import json
import time
from pathlib import Path

import pytest

from universal_video_ai.downloader.cache import CacheEntry, CacheManager, _hash_url


def test_hash_url():
    url1 = "https://youtube.com/watch?v=abc123"
    url2 = "https://youtube.com/watch?v=different"
    h1 = _hash_url(url1)
    h2 = _hash_url(url2)
    assert len(h1) == 16
    assert h1 != h2


def test_cache_entry_serialization():
    entry = CacheEntry(
        url="https://example.com/video",
        platform="youtube",
        video_path="/tmp/video.mp4",
        content_hash="abc123def456",
        timestamp=time.time(),
        title="Test Video",
        size_bytes=1024,
    )
    data = entry.to_dict()
    assert data["url"] == "https://example.com/video"

    recovered = CacheEntry.from_dict(data)
    assert recovered == entry


def test_cache_manager_save_and_get(tmp_path: Path):
    manager = CacheManager(cache_dir=tmp_path)

    # Create a dummy video file
    video_file = tmp_path / "video.mp4"
    video_file.write_bytes(b"fake video content")

    # Create and save a cache entry
    entry = CacheEntry(
        url="https://example.com/video1",
        platform="test_platform",
        video_path=str(video_file),
        content_hash="hash123",
        timestamp=time.time(),
        title="Test Video",
    )
    manager.save(entry)

    # Retrieve it
    found = manager.get(entry.url, platform="test_platform")
    assert found is not None
    assert found.url == entry.url
    assert found.title == "Test Video"


def test_cache_manager_ttl_expiry(tmp_path: Path):
    manager = CacheManager(cache_dir=tmp_path)

    video_file = tmp_path / "video.mp4"
    video_file.write_bytes(b"content")

    # Create entry with very short TTL (already expired)
    entry = CacheEntry(
        url="https://example.com/expires",
        platform="test",
        video_path=str(video_file),
        content_hash="hash",
        timestamp=time.time() - 100,  # 100 seconds ago
        ttl_seconds=1,  # expires after 1 second
    )
    manager.save(entry)

    # Should be expired
    found = manager.get(entry.url, platform="test")
    assert found is None


def test_cache_manager_invalid_file(tmp_path: Path):
    manager = CacheManager(cache_dir=tmp_path)

    # Entry points to non-existent file
    entry = CacheEntry(
        url="https://example.com/missing",
        platform="test",
        video_path=str(tmp_path / "missing_video.mp4"),
        content_hash="hash",
        timestamp=time.time(),
    )
    manager.save(entry)

    # Should not find it (file missing)
    found = manager.get(entry.url, platform="test")
    assert found is None


def test_cache_manager_delete(tmp_path: Path):
    manager = CacheManager(cache_dir=tmp_path)

    video1 = tmp_path / "v1.mp4"
    video2 = tmp_path / "v2.mp4"
    video1.write_bytes(b"v1")
    video2.write_bytes(b"v2")

    entry1 = CacheEntry(
        url="https://example.com/1",
        platform="youtube",
        video_path=str(video1),
        content_hash="h1",
        timestamp=time.time(),
    )
    entry2 = CacheEntry(
        url="https://example.com/2",
        platform="tiktok",
        video_path=str(video2),
        content_hash="h2",
        timestamp=time.time(),
    )

    manager.save(entry1)
    manager.save(entry2)

    # Delete first entry by URL
    manager.delete(url=entry1.url)
    assert manager.get(entry1.url) is None
    assert manager.get(entry2.url) is not None

    # Delete by platform
    manager.delete(platform="tiktok")
    assert manager.get(entry2.url) is None


def test_cache_manager_clear(tmp_path: Path):
    manager = CacheManager(cache_dir=tmp_path)

    video = tmp_path / "video.mp4"
    video.write_bytes(b"content")

    entry = CacheEntry(
        url="https://example.com/test",
        platform="test",
        video_path=str(video),
        content_hash="hash",
        timestamp=time.time(),
    )
    manager.save(entry)

    # Clear all
    manager.clear()
    assert manager.get(entry.url) is None


def test_cache_manager_corrupted_index(tmp_path: Path):
    manager = CacheManager(cache_dir=tmp_path)

    # Write corrupted JSON
    manager.index_file.write_text("{ invalid json }", encoding="utf-8")

    # Should gracefully fallback to empty list
    entries = manager._load_index()
    assert entries == []


def test_cache_manager_cleanup_expired(tmp_path: Path):
    manager = CacheManager(cache_dir=tmp_path)

    video1 = tmp_path / "v1.mp4"
    video2 = tmp_path / "v2.mp4"
    video1.write_bytes(b"v1")
    video2.write_bytes(b"v2")

    # One valid, one expired
    valid = CacheEntry(
        url="https://example.com/valid",
        platform="test",
        video_path=str(video1),
        content_hash="h1",
        timestamp=time.time(),
        ttl_seconds=3600,
    )
    expired = CacheEntry(
        url="https://example.com/expired",
        platform="test",
        video_path=str(video2),
        content_hash="h2",
        timestamp=time.time() - 100,
        ttl_seconds=1,
    )

    manager.save(valid)
    manager.save(expired)

    # Cleanup should remove the expired one
    removed = manager.cleanup_expired()
    assert removed == 1
    assert manager.get(valid.url) is not None
    assert manager.get(expired.url) is None