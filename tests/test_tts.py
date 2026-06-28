# tests/test_tts.py
from pathlib import Path
import subprocess
from unittest.mock import MagicMock

import pytest

from universal_video_ai.tts import TTSFactory, TTSConfig, NoOpTTS, EdgeTTS


def test_factory_default_noop():
    cfg = TTSConfig()
    tts = TTSFactory.create(cfg)
    assert isinstance(tts, NoOpTTS)


def test_noop_synthesize_creates_file(tmp_path: Path):
    tts = NoOpTTS()
    out = tmp_path / "out.mp3"
    text = "Hello world"
    path = tts.synthesize(text, out)
    assert path.exists()
    content = path.read_text(encoding="utf-8")
    assert "TTS_PLACEHOLDER" in content
    assert "Hello world" in content


def test_noop_invalid_input(tmp_path: Path):
    tts = NoOpTTS()
    with pytest.raises(ValueError):
        tts.synthesize("", tmp_path / "out.mp3")  # empty text not allowed
    with pytest.raises(ValueError):
        tts.synthesize(123, tmp_path / "out.mp3")  # type error


def test_edge_tts_subprocess_success(tmp_path: Path, monkeypatch):
    # Create dummy output path
    out = tmp_path / "out.mp3"
    text = "Hello from edge"

    # Mock subprocess.run to simulate success and create output file
    def mock_run(cmd, capture_output, text, check, timeout):
        # create the output file to simulate edge-tts behavior
        output_path = Path(cmd[cmd.index("--write-media") + 1])
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"audio data")
        result = MagicMock()
        result.returncode = 0
        result.stderr = ""
        result.stdout = ""
        return result

    monkeypatch.setattr("subprocess.run", mock_run)

    cfg = TTSConfig(provider="edge", voice="en-US-JennyNeural", output_format="mp3")
    tts = EdgeTTS(config=cfg)
    path = tts.synthesize(text, out)
    assert path.exists()
    assert path.read_bytes() == b"audio data"


def test_edge_tts_not_found(tmp_path: Path, monkeypatch):
    out = tmp_path / "out.mp3"
    text = "hi"

    # Simulate subprocess raising FileNotFoundError
    def mock_run_raise(*args, **kwargs):
        raise FileNotFoundError("edge-tts")

    monkeypatch.setattr("subprocess.run", mock_run_raise)

    cfg = TTSConfig(provider="edge")
    tts = EdgeTTS(config=cfg)
    with pytest.raises(RuntimeError):
        tts.synthesize(text, out)


def test_edge_tts_failure(tmp_path: Path, monkeypatch):
    out = tmp_path / "out.mp3"
    text = "hi"

    # Simulate subprocess returning non-zero code
    def mock_run_fail(cmd, capture_output, text, check, timeout):
        result = MagicMock()
        result.returncode = 1
        result.stderr = "some error"
        result.stdout = ""
        return result

    monkeypatch.setattr("subprocess.run", mock_run_fail)

    cfg = TTSConfig(provider="edge")
    tts = EdgeTTS(config=cfg)
    with pytest.raises(RuntimeError):
        tts.synthesize(text, out)