# tests/test_speech_service_cache.py
from pathlib import Path
from dataclasses import dataclass
from typing import Optional
import pytest

from universal_video_ai.speech.service import SpeechService
from universal_video_ai.speech.exceptions import SpeechBackendUnavailable, TranscriptionError
from universal_video_ai.cache.redis_cache import RedisCache


@dataclass
class DummyBackend:
    def transcribe(self, audio_path: Path, language: Optional[str] = None) -> str:
        return f"transcript:{audio_path.name}:{language}"


def test_speech_service_with_cache_hit(tmp_path: Path):
    """Test that cache is checked before backend."""
    audio = tmp_path / "test.wav"
    audio.write_bytes(b"\x00")
    
    cache = RedisCache(fallback=True)
    backend = DummyBackend()
    svc = SpeechService(backend=backend, cache=cache)
    
    # First call - cache miss
    text1 = svc.transcribe(audio, language="en")
    assert text1 == f"transcript:{audio.name}:en"
    
    # Second call - cache hit
    text2 = svc.transcribe(audio, language="en")
    assert text2 == f"transcript:{audio.name}:en"


def test_speech_service_cache_different_language(tmp_path: Path):
    """Test that different languages generate different cache keys."""
    audio = tmp_path / "test.wav"
    audio.write_bytes(b"\x00")
    
    cache = RedisCache(fallback=True)
    backend = DummyBackend()
    svc = SpeechService(backend=backend, cache=cache)
    
    text_en = svc.transcribe(audio, language="en")
    text_vi = svc.transcribe(audio, language="vi")
    
    assert text_en == f"transcript:{audio.name}:en"
    assert text_vi == f"transcript:{audio.name}:vi"


def test_speech_service_no_backend_raises_with_cache(tmp_path: Path):
    """Test that missing backend raises error even with cache."""
    audio = tmp_path / "test.wav"
    audio.write_bytes(b"\x00")
    
    cache = RedisCache(fallback=True)
    svc = SpeechService(backend=None, cache=cache)
    
    with pytest.raises(SpeechBackendUnavailable):
        svc.transcribe(audio)


def test_speech_service_backend_error_propagates(tmp_path: Path):
    """Test that backend errors are wrapped in TranscriptionError."""
    audio = tmp_path / "test.wav"
    audio.write_bytes(b"\x00")
    
    @dataclass
    class FailingBackend:
        def transcribe(self, audio_path: Path, language: Optional[str] = None) -> str:
            raise RuntimeError("backend failed")
    
    cache = RedisCache(fallback=True)
    backend = FailingBackend()
    svc = SpeechService(backend=backend, cache=cache)
    
    with pytest.raises(TranscriptionError):
        svc.transcribe(audio)
