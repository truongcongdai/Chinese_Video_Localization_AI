# tests/test_tts_service.py
from pathlib import Path
from dataclasses import dataclass
from typing import Optional

import pytest

from universal_video_ai.tts.service import TTSService
from universal_video_ai.tts.exceptions import TTSBackendUnavailable, SynthesisError


@dataclass
class DummyTTSBackend:
    def synthesize(self, text: str, output_path: Path, language: str = "en", voice: Optional[str] = None) -> Path:
        output_path = Path(output_path)
        output_path.write_bytes(b"audio_data")
        return output_path


def test_tts_service_success(tmp_path: Path):
    backend = DummyTTSBackend()
    svc = TTSService(backend=backend)
    output = tmp_path / "output.wav"
    result = svc.synthesize("hello", output_path=output, language="en")
    assert result == output
    assert output.exists()


def test_tts_service_no_backend_raises(tmp_path: Path):
    svc = TTSService(backend=None)
    with pytest.raises(TTSBackendUnavailable):
        svc.synthesize("hello", tmp_path / "output.wav")


def test_cache_uses_full_text_and_rejects_overwritten_audio(tmp_path):
    class Cache:
        def __init__(self):
            self.data = {}
        def make_key(self, *parts):
            return ":".join(parts)
        def get(self, key):
            return self.data.get(key)
        def set(self, key, value, **kwargs):
            self.data[key] = value
    class Backend:
        def __init__(self):
            self.calls = []
        def synthesize(self, text, output_path, **kwargs):
            self.calls.append(text)
            output_path.write_bytes(text.encode())
            return output_path
    backend = Backend()
    service = TTSService(backend=backend, cache=Cache())
    first = "a" * 60 + "first"
    second = "a" * 60 + "second"
    output = tmp_path / "same.wav"
    service.synthesize(first, output_path=output)
    service.synthesize(first, output_path=output)
    assert backend.calls == [first]
    service.synthesize(second, output_path=output)
    assert output.read_bytes() == second.encode()
    service.synthesize(first, output_path=output)
    assert backend.calls == [first, second, first]
    assert output.read_bytes() == first.encode()
