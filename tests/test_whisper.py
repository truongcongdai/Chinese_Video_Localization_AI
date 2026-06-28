# tests/test_whisper.py
from pathlib import Path
import pytest

from universal_video_ai.speech.whisper import WhisperTranscriber, WhisperConfig


def test_transcribe_file_not_found():
    transcriber = WhisperTranscriber()
    with pytest.raises(FileNotFoundError):
        transcriber.transcribe(Path("this_file_does_not_exist.wav"))


def test_transcribe_with_mocked_backend(tmp_path: Path, monkeypatch):
    # Create dummy audio file
    audio_file = tmp_path / "audio.wav"
    audio_file.write_bytes(b"fake audio")

    # Prepare a fake transcribe function and monkeypatch the internal backend method
    def fake_transcribe(self, audio_path, language=None):
        assert audio_path == audio_file.resolve()
        return "this is a mocked transcript"

    monkeypatch.setattr(WhisperTranscriber, "_transcribe_with_python_whisper", fake_transcribe)

    transcriber = WhisperTranscriber(config=WhisperConfig(model="tiny"))
    text = transcriber.transcribe(audio_file)
    assert text == "this is a mocked transcript"


def test_transcribe_backend_not_installed(monkeypatch, tmp_path: Path):
    # Create dummy audio file
    audio_file = tmp_path / "audio.wav"
    audio_file.write_bytes(b"fake audio")

    # Make the internal backend method raise a RuntimeError simulating missing package
    def raise_missing(self, audio_path, language=None):
        raise RuntimeError("not installed")

    monkeypatch.setattr(WhisperTranscriber, "_transcribe_with_python_whisper", raise_missing)

    transcriber = WhisperTranscriber()
    with pytest.raises(RuntimeError):
        transcriber.transcribe(audio_file)