# tests/test_speech_service.py
from pathlib import Path
from dataclasses import dataclass
from typing import Optional
import pytest

from universal_video_ai.speech.service import SpeechService


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
    with pytest.raises(RuntimeError):
        svc.transcribe(audio)