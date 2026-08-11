"""Download cache to avoid re-downloading the same videos."""
import hashlib
import json
import logging
import os
import shutil
from pathlib import Path
from typing import Any, Dict, Optional
from datetime import datetime, timedelta

from universal_video_ai.downloader.media_validation import validate_video_file

_logger = logging.getLogger(__name__)


class DownloadCache:
    """Cache downloaded videos by URL hash to avoid re-downloading."""
    
    def __init__(self, cache_dir: Path, max_age_days: int = 7, max_size_gb: float = 10.0):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.max_age = timedelta(days=max_age_days)
        self.max_size_bytes = max_size_gb * 1024 * 1024 * 1024
        self.metadata_file = self.cache_dir / "cache_metadata.json"
        self.metadata = self._load_metadata()
        # Enforce the configured bound at startup too. Previously cleanup ran
        # only before the next successful download, allowing an overfull cache
        # to remain indefinitely after a restart.
        self._cleanup_if_needed()
        
    def _load_metadata(self) -> dict:
        """Load cache metadata from disk."""
        if self.metadata_file.exists():
            try:
                with open(self.metadata_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                _logger.warning(f"Failed to load cache metadata: {e}")
        return {}
    
    def _save_metadata(self) -> None:
        """Save cache metadata to disk."""
        try:
            with open(self.metadata_file, 'w', encoding='utf-8') as f:
                json.dump(self.metadata, f, indent=2)
        except Exception as e:
            _logger.warning(f"Failed to save cache metadata: {e}")
    
    def _get_url_hash(self, url: str) -> str:
        """Generate hash for URL."""
        return hashlib.sha256(url.encode()).hexdigest()[:16]
    
    def _get_cache_path(self, url: str) -> Path:
        """Get cache file path for URL."""
        url_hash = self._get_url_hash(url)
        return self.cache_dir / f"{url_hash}.mp4"
    
    def get_entry(self, url: str) -> Optional[Dict[str, Any]]:
        """Return a valid cache entry including its source metadata."""
        cache_path = self._get_cache_path(url)
        url_hash = self._get_url_hash(url)
        
        if not cache_path.exists():
            return None
        
        # Check if cache entry exists in metadata
        entry = self.metadata.get(url_hash)
        if not entry:
            # Clean up orphaned file
            cache_path.unlink(missing_ok=True)
            return None
        
        # Check if cache is expired
        cached_time = datetime.fromisoformat(entry.get("cached_at", ""))
        if datetime.now() - cached_time > self.max_age:
            _logger.info(f"Cache entry expired for {url[:50]}...")
            self.remove(url)
            return None
        
        # Verify file still exists
        if not cache_path.exists():
            self.remove(url)
            return None

        valid, reason = validate_video_file(cache_path)
        if not valid:
            _logger.warning(
                "Discarding invalid cached video for %s...: %s (%s)",
                url[:50], cache_path, reason,
            )
            self.remove(url)
            return None
        
        _logger.info(f"Cache hit for {url[:50]}... -> {cache_path}")
        result = dict(entry)
        result["video_path"] = str(cache_path)
        return result

    def get(self, url: str) -> Optional[Path]:
        """Backward-compatible cached video lookup."""
        entry = self.get_entry(url)
        return Path(entry["video_path"]) if entry else None

    def put(
        self,
        url: str,
        video_path: Path,
        *,
        source_metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Cache a downloaded video and metadata required by Publishing Pack."""
        if not video_path.exists():
            _logger.warning(f"Cannot cache non-existent video: {video_path}")
            return
        valid, reason = validate_video_file(video_path)
        if not valid:
            _logger.warning("Refusing to cache invalid video %s: %s", video_path, reason)
            return
        
        # Check cache size before adding
        self._cleanup_if_needed()
        
        cache_path = self._get_cache_path(url)
        url_hash = self._get_url_hash(url)
        
        # Prefer a hard link on the same volume. Job files and cache entries
        # then have independent names/lifetimes without storing the same
        # multi-gigabyte video twice.
        try:
            cache_path.unlink(missing_ok=True)
            try:
                os.link(video_path, cache_path)
                storage_method = "hardlink"
            except OSError:
                shutil.copy2(video_path, cache_path)
                storage_method = "copy"
            
            # Update metadata
            self.metadata[url_hash] = {
                "url": url,
                "cached_at": datetime.now().isoformat(),
                "file_size": cache_path.stat().st_size,
                "original_path": str(video_path),
                "source_metadata": dict(source_metadata or {}),
            }
            self._save_metadata()
            
            _logger.info(
                "Cached video for %s... -> %s (%s)",
                url[:50], cache_path, storage_method,
            )
            self._cleanup_if_needed()
        except Exception as e:
            _logger.error(f"Failed to cache video: {e}")
    
    def remove(self, url: str) -> None:
        """Remove cached video for URL."""
        cache_path = self._get_cache_path(url)
        url_hash = self._get_url_hash(url)
        
        cache_path.unlink(missing_ok=True)
        self.metadata.pop(url_hash, None)
        self._save_metadata()
    
    def _cleanup_if_needed(self) -> None:
        """Clean up old cache entries if size limit exceeded."""
        total_size = sum(
            Path(self.cache_dir / f"{h}.mp4").stat().st_size
            for h, entry in self.metadata.items()
            if (self.cache_dir / f"{h}.mp4").exists()
        )
        
        if total_size <= self.max_size_bytes:
            return
        
        _logger.info(f"Cache size {total_size / 1024 / 1024 / 1024:.2f}GB exceeds limit, cleaning up...")
        
        # Sort by age (oldest first)
        entries_by_age = sorted(
            self.metadata.items(),
            key=lambda x: datetime.fromisoformat(x[1].get("cached_at", ""))
        )
        
        removed = 0
        for url_hash, entry in entries_by_age:
            if total_size <= self.max_size_bytes * 0.8:  # Clean to 80% of limit
                break
            
            cache_path = self.cache_dir / f"{url_hash}.mp4"
            if cache_path.exists():
                size = cache_path.stat().st_size
                cache_path.unlink()
                total_size -= size
                removed += 1
                self.metadata.pop(url_hash, None)
        
        self._save_metadata()
        _logger.info(f"Cleaned up {removed} cache entries")
    
    def clear(self) -> None:
        """Clear all cache entries."""
        for url_hash in list(self.metadata.keys()):
            cache_path = self.cache_dir / f"{url_hash}.mp4"
            cache_path.unlink(missing_ok=True)
        
        self.metadata.clear()
        self._save_metadata()
        _logger.info("Cache cleared")
    
    def get_stats(self) -> dict:
        """Get cache statistics."""
        total_size = 0
        entry_count = 0
        hit_rate = 0.0
        
        for url_hash, entry in self.metadata.items():
            cache_path = self.cache_dir / f"{url_hash}.mp4"
            if cache_path.exists():
                total_size += cache_path.stat().st_size
                entry_count += 1
        
        return {
            "entry_count": entry_count,
            "total_size_bytes": total_size,
            "total_size_mb": total_size / 1024 / 1024,
            "total_size_gb": total_size / 1024 / 1024 / 1024,
            "max_size_gb": self.max_size_bytes / 1024 / 1024 / 1024,
            "max_age_days": self.max_age.days,
        }


# Global cache instance
_global_cache: Optional[DownloadCache] = None


def get_download_cache() -> DownloadCache:
    """Get or create the global download cache instance."""
    global _global_cache
    if _global_cache is None:
        from universal_video_ai.config import TEMP_DIR
        cache_dir = TEMP_DIR / "download_cache"
        _global_cache = DownloadCache(
            cache_dir=cache_dir,
            max_age_days=7,
            max_size_gb=10.0,
        )
    return _global_cache


def configure_download_cache(
    cache_dir: Optional[Path] = None,
    max_age_days: int = 7,
    max_size_gb: float = 10.0,
) -> None:
    """Configure the global download cache."""
    global _global_cache
    if cache_dir is None:
        from universal_video_ai.config import TEMP_DIR
        cache_dir = TEMP_DIR / "download_cache"
    _global_cache = DownloadCache(
        cache_dir=cache_dir,
        max_age_days=max_age_days,
        max_size_gb=max_size_gb,
    )
