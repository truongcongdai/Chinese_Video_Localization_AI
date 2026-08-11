from datetime import datetime
from pathlib import Path

from universal_video_ai.downloader.download_cache import DownloadCache
from universal_video_ai.downloader.media_validation import validate_video_file


def test_video_validation_rejects_tiny_placeholder_without_running_ffprobe(tmp_path, monkeypatch):
    video = tmp_path / "placeholder.mp4"
    video.write_bytes(b"not a real mp4")

    monkeypatch.setattr(
        "universal_video_ai.downloader.media_validation.subprocess.run",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("ffprobe must not run")),
    )

    valid, reason = validate_video_file(video)

    assert valid is False
    assert "too small" in reason


def test_cache_evicts_video_that_fails_validation(tmp_path, monkeypatch):
    cache = DownloadCache(tmp_path / "cache")
    url = "https://example.com/bad-video"
    cache_path = cache._get_cache_path(url)
    cache_path.write_bytes(b"bad")
    cache.metadata[cache._get_url_hash(url)] = {
        "url": url,
        "cached_at": datetime.now().isoformat(),
    }
    cache._save_metadata()
    monkeypatch.setattr(
        "universal_video_ai.downloader.download_cache.validate_video_file",
        lambda path: (False, "moov atom not found"),
    )

    assert cache.get_entry(url) is None
    assert not cache_path.exists()
    assert cache._get_url_hash(url) not in cache.metadata
