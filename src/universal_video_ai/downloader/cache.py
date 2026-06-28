# src/universal_video_ai/downloader/cache.py
from __future__ import annotations

from dataclasses import dataclass, asdict, field
from pathlib import Path
import hashlib
import json
import logging
import time
from typing import Any, Dict, List, Optional

from universal_video_ai.config import TEMP_DIR

__all__ = ["CacheEntry", "CacheManager"]

_logger = logging.getLogger(__name__)

# Default cache directory
DEFAULT_CACHE_DIR = TEMP_DIR / "cache"
DEFAULT_TTL_SECONDS = 7 * 24 * 60 * 60  # 7 days


def _hash_url(url: str) -> str:
    """
    Compute SHA256 hash of a URL string for safe filename generation.

    :param url: URL to hash
    :return: hex string (first 16 chars for brevity)
    """
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class CacheEntry:
    """
    Represents a cached downloaded video.

    Attributes:
        url: original video URL
        platform: platform identifier (e.g., "youtube", "tiktok")
        video_path: Path to the downloaded video file
        content_hash: SHA256 hash of the video file (for integrity check)
        timestamp: epoch timestamp when entry was created
        ttl_seconds: time-to-live in seconds; entry invalid if now > timestamp + ttl_seconds
        title: optional video title
        size_bytes: optional file size at time of caching
    """

    url: str
    platform: str
    video_path: str  # stored as string for JSON serialization
    content_hash: str
    timestamp: float
    ttl_seconds: float = DEFAULT_TTL_SECONDS
    title: str = ""
    size_bytes: int = 0

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dict for JSON serialization."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> CacheEntry:
        """Create from dict (e.g., loaded from JSON)."""
        return cls(**data)


class CacheManager:
    """
    Manage cache of downloaded videos.

    Responsibilities:
    - Store and retrieve cached video entries.
    - Check cache validity (TTL, file exists).
    - Persist cache metadata to JSON.
    - Clean up expired or invalid entries.

    Storage:
    - Uses JSON file (cache_dir/cache_index.json) to store CacheEntry list.
    - Video files stored in cache_dir with safe names based on URL hash.
    """

    def __init__(self, cache_dir: Optional[Path] = None, logger: Optional[logging.Logger] = None) -> None:
        """
        Initialize CacheManager.

        :param cache_dir: directory to store cache (default: TEMP_DIR/cache)
        :param logger: optional logger instance
        """
        self.cache_dir: Path = (cache_dir or DEFAULT_CACHE_DIR).resolve()
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        self.index_file = self.cache_dir / "cache_index.json"
        self.logger = logger or _logger

        self.logger.debug("CacheManager initialized with cache_dir=%s", str(self.cache_dir))

    def _load_index(self) -> List[CacheEntry]:
        """
        Load cache index from JSON file.

        :return: list of CacheEntry objects, empty list if file missing or corrupted
        """
        if not self.index_file.exists():
            self.logger.debug("Cache index does not exist: %s", str(self.index_file))
            return []

        try:
            content = self.index_file.read_text(encoding="utf-8")
            data = json.loads(content)
            if not isinstance(data, list):
                self.logger.warning("Cache index corrupted (not a list), returning empty")
                return []
            entries = [CacheEntry.from_dict(item) for item in data]
            self.logger.debug("Loaded %d cache entries from %s", len(entries), str(self.index_file))
            return entries
        except (json.JSONDecodeError, ValueError, KeyError) as exc:
            self.logger.warning("Failed to load cache index from %s: %s; returning empty", str(self.index_file), exc)
            return []
        except Exception as exc:  # pragma: no cover
            self.logger.exception("Unexpected error loading cache index: %s", exc)
            return []

    def _save_index(self, entries: List[CacheEntry]) -> None:
        """
        Save cache index to JSON file (atomic write).

        :param entries: list of CacheEntry to persist
        """
        try:
            data = [e.to_dict() for e in entries]
            temp_file = self.index_file.with_suffix(".json.tmp")
            temp_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
            # Atomic rename
            temp_file.replace(self.index_file)
            self.logger.debug("Saved %d cache entries to %s", len(entries), str(self.index_file))
        except Exception as exc:
            self.logger.exception("Failed to save cache index: %s", exc)

    def is_valid(self, entry: CacheEntry) -> bool:
        """
        Check if a cache entry is valid.

        Criteria:
        - TTL not expired: now <= timestamp + ttl_seconds
        - Video file exists

        :param entry: CacheEntry to validate
        :return: True if valid
        """
        now = time.time()
        if now > entry.timestamp + entry.ttl_seconds:
            self.logger.debug("Cache entry for %s expired (TTL %.0f sec ago)", entry.url,
                              now - entry.timestamp - entry.ttl_seconds)
            return False

        video_path = Path(entry.video_path)
        if not video_path.exists():
            self.logger.debug("Cache entry file missing: %s", video_path)
            return False

        self.logger.debug("Cache entry for %s is valid", entry.url)
        return True

    def get(self, url: str, platform: Optional[str] = None) -> Optional[CacheEntry]:
        """
        Find a valid cache entry for the given URL and optional platform.

        :param url: video URL
        :param platform: optional platform filter (e.g., "youtube")
        :return: valid CacheEntry or None
        """
        self.logger.debug("Cache.get(url=%s, platform=%s)", url, platform)
        entries = self._load_index()

        for entry in entries:
            if entry.url == url and (platform is None or entry.platform == platform):
                if self.is_valid(entry):
                    self.logger.info("Cache hit for %s (platform=%s)", url, entry.platform)
                    return entry
                else:
                    self.logger.info("Cache entry exists but invalid (TTL or missing file) for %s", url)
                    return None

        self.logger.debug("Cache miss for %s", url)
        return None

    def save(self, entry: CacheEntry) -> None:
        """
        Save a cache entry (add or update).

        :param entry: CacheEntry to save
        """
        self.logger.debug("Cache.save(url=%s, platform=%s)", entry.url, entry.platform)

        # Validate video file exists before caching
        video_path = Path(entry.video_path)
        if not video_path.exists():
            self.logger.warning("Cache.save: video file does not exist, not caching: %s", video_path)
            return

        entries = self._load_index()

        # Remove any existing entry for the same URL + platform
        entries = [e for e in entries if not (e.url == entry.url and e.platform == entry.platform)]

        # Add new entry
        entries.append(entry)
        self._save_index(entries)
        self.logger.info("Cached video: %s (platform=%s)", entry.url, entry.platform)

    def delete(self, url: Optional[str] = None, platform: Optional[str] = None) -> None:
        """
        Delete cache entries matching criteria.

        :param url: if provided, delete entries for this URL only
        :param platform: if provided, delete entries for this platform only
                        (delete by both url and platform if both provided)
        """
        entries = self._load_index()
        original_count = len(entries)

        if url and platform:
            entries = [e for e in entries if not (e.url == url and e.platform == platform)]
        elif url:
            entries = [e for e in entries if e.url != url]
        elif platform:
            entries = [e for e in entries if e.platform != platform]
        else:
            # delete all
            entries = []

        self._save_index(entries)
        deleted = original_count - len(entries)
        self.logger.debug("Deleted %d cache entries (url=%s, platform=%s)", deleted, url, platform)

    def clear(self) -> None:
        """
        Clear all cache entries (not the cached files themselves, just the index).
        """
        self._save_index([])
        self.logger.info("Cleared all cache entries")

    def cleanup_expired(self) -> int:
        """
        Remove expired cache entries from index.

        :return: number of entries removed
        """
        entries = self._load_index()
        original = len(entries)
        entries = [e for e in entries if self.is_valid(e)]
        self._save_index(entries)
        removed = original - len(entries)
        if removed > 0:
            self.logger.info("Cleanup removed %d expired cache entries", removed)
        return removed