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