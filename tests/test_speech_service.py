# tests/test_speech_service.py
from pathlib import Path
from dataclasses import dataclass
from typing import Optional
import pytest

from universal_video_ai.speech.service import SpeechService


@dataclass
class DummyBackend:
    def transcribe(self, audio_path: Path, language: Optional[str] = None) -> str:
        return "ok"

def test_speech_service_success(tmp_path: Path):
    backend = DummyBackend()
    svc = SpeechService(backend=backend)
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"\x00")
    text = svc.transcribe(audio, language="en")
    assert text == "ok"

def test_speech_service_no_backend_raises(tmp_path: Path):
    svc = SpeechService(backend=None)
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"\x00")
    with pytest.raises(RuntimeError):
        svc.transcribe(audio)