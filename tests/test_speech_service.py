# tests/test_speech_service.py
from pathlib import Path
from dataclasses import dataclass
from typing import Optional
import pytest

from universal_video_ai.speech.service import SpeechService
from universal_video_ai.speech.exceptions import SpeechBackendUnavailable


@dataclass
class DummyBackend:
    def transcribe(self, audio_path: Path, language: Optional[str] = None) -> str:
        return f"ok:{audio_path.name}:{language}"


def test_speech_service_transcribe_success(tmp_path: Path):
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"\x00")
    backend = DummyBackend()
    svc = SpeechService(backend=backend)
    text = svc.transcribe(audio, language="en")
    assert text == f"ok:{audio.name}:en"


def test_speech_service_no_backend_raises(tmp_path: Path):
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"\x00")
    svc = SpeechService(backend=None)
    with pytest.raises(SpeechBackendUnavailable):
        svc.transcribe(audio)

def test_speech_cache_is_reused_across_job_directories(tmp_path: Path):
    class FakeCache:
        def __init__(self):
            self.values = {}

        def make_key(self, prefix, *parts):
            return "|".join((prefix, *parts))

        def get(self, key):
            return self.values.get(key)

        def set(self, key, value, ttl_seconds=0):
            self.values[key] = value
            return True

    class CountingBackend:
        def __init__(self):
            self.calls = 0

        def transcribe(self, audio_path: Path, language=None):
            self.calls += 1
            return "cached transcript"

    first_dir = tmp_path / "job-a"
    second_dir = tmp_path / "job-b"
    first_dir.mkdir()
    second_dir.mkdir()
    first_audio = first_dir / "source.wav"
    second_audio = second_dir / "source.wav"
    first_audio.write_bytes(b"same audio bytes")
    second_audio.write_bytes(b"same audio bytes")

    backend = CountingBackend()
    service = SpeechService(backend=backend, cache=FakeCache())

    assert service.transcribe(first_audio, language="zh") == "cached transcript"
    assert service.transcribe(second_audio, language="zh") == "cached transcript"
    assert backend.calls == 1
