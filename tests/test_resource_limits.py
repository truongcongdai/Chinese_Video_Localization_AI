"""Regression checks for memory budgets and complete content fingerprints."""
import hashlib
from pathlib import Path
from unittest.mock import Mock

from universal_video_ai.cache.redis_cache import RedisCache
from universal_video_ai.file_hash import sha256_file
from universal_video_ai.speech.service import SpeechService


def memory_cache(monkeypatch, **kwargs):
    import redis
    monkeypatch.setattr(redis, "from_url", Mock(side_effect=ConnectionError("offline")))
    return RedisCache(**kwargs)


def test_memory_cache_evicts_by_bytes_and_rejects_oversize(monkeypatch):
    cache = memory_cache(monkeypatch, max_memory_bytes=12)
    assert cache.set("a", "1234")  # Six JSON bytes including quotes.
    assert cache.set("b", "5678")
    assert cache.get("a") == "1234"
    assert cache.set("c", "9012")
    assert cache.get("b") is None
    assert not cache.set("huge", "x" * 13)
    assert cache.get("a") == "1234"
    cache.delete("a")
    assert cache.set("d", "3456")
    cache.clear()
    assert cache.set("e", "x" * 10)


def test_memory_cache_stores_snapshot_and_accounts_for_replacement(monkeypatch):
    cache = memory_cache(monkeypatch, max_memory_bytes=32)
    value = ["original"]
    cache.set("a", value)
    value.append("changed")
    cached = cache.get("a")
    cached.append("also changed")
    assert cache.get("a") == ["original"]
    for _ in range(100):
        assert cache.set("a", "x" * 30)
        assert cache.get("a") == "x" * 30


def test_memory_cache_expiry_releases_budget(monkeypatch):
    cache = memory_cache(monkeypatch, max_memory_bytes=12)
    clock = Mock(return_value=100.0)
    monkeypatch.setattr("universal_video_ai.cache.redis_cache.time.monotonic", clock)
    cache.set("a", "1234", ttl_seconds=1)
    cache.set("b", "5678", ttl_seconds=10)
    clock.return_value = 102.0
    assert cache.get("a") is None
    assert cache.set("c", "9012")
    assert cache.get("b") == "5678"
    assert cache.get("c") == "9012"


def test_hash_reads_full_content_without_read_bytes(tmp_path, monkeypatch):
    audio = tmp_path / "audio.wav"
    content = b"a" * (1024 * 1024) + b"middle" + b"z" * (1024 * 1024)
    audio.write_bytes(content)
    monkeypatch.setattr(Path, "read_bytes", Mock(side_effect=AssertionError("full-file allocation")))
    assert sha256_file(audio) == hashlib.sha256(content).hexdigest()


def test_speech_hashes_once_on_cache_miss_and_middle_changes_invalidate(tmp_path, monkeypatch):
    cache = memory_cache(monkeypatch)
    backend = Mock()
    backend.transcribe.return_value = "first"
    service = SpeechService(backend=backend, cache=cache)
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"a" * (1024 * 1024) + b"x" + b"z" * (1024 * 1024))
    fingerprint = Mock(wraps=sha256_file)
    monkeypatch.setattr("universal_video_ai.speech.service.sha256_file", fingerprint)
    assert service.transcribe(audio) == "first"
    assert fingerprint.call_count == 1
    assert service.transcribe(audio) == "first"
    backend.transcribe.assert_called_once()
    with audio.open("r+b") as stream:
        stream.seek(1024 * 1024)
        stream.write(b"y")
    backend.transcribe.return_value = "second"
    assert service.transcribe(audio) == "second"
    assert backend.transcribe.call_count == 2


def test_unreadable_audio_never_hits_filename_cache(tmp_path, monkeypatch):
    cache = memory_cache(monkeypatch)
    service = SpeechService(backend=Mock(), cache=cache)
    assert service._cache_key("speech", tmp_path / "missing.wav", None) is None
