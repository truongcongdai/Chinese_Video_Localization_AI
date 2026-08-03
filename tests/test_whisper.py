# tests/test_whisper.py
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from universal_video_ai.speech import whisper as whisper_module
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


def test_cached_model_inference_is_serialized(monkeypatch, tmp_path: Path):
    """A shared openai-whisper model must never decode on two threads at once."""
    active = 0
    max_active = 0
    state_lock = threading.Lock()

    class FakeModel:
        device = SimpleNamespace(type="cpu")

        def transcribe(self, _audio_path, **_kwargs):
            nonlocal active, max_active
            with state_lock:
                active += 1
                max_active = max(max_active, active)
            time.sleep(0.05)
            with state_lock:
                active -= 1
            return {"text": "ok", "segments": [], "language": "en"}

    fake_model = FakeModel()
    fake_whisper = SimpleNamespace(load_model=lambda *_args, **_kwargs: fake_model)
    monkeypatch.setitem(sys.modules, "whisper", fake_whisper)
    whisper_module._MODEL_CACHE.clear()
    whisper_module._MODEL_INFERENCE_LOCKS.clear()

    audio_files = [tmp_path / f"audio-{index}.wav" for index in range(3)]
    for audio_file in audio_files:
        audio_file.write_bytes(b"fake audio")

    transcribers = [WhisperTranscriber(WhisperConfig(model="tiny", device="cpu")) for _ in audio_files]
    threads = [
        threading.Thread(target=transcriber.transcribe, args=(audio_file,))
        for transcriber, audio_file in zip(transcribers, audio_files)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert max_active == 1


def test_whisper_passes_fp16_false_when_device_is_auto(monkeypatch, tmp_path: Path):
    audio_file = tmp_path / "audio.wav"
    audio_file.write_bytes(b"fake audio")
    captured = {}

    class FakeModel:
        device = SimpleNamespace(type="cuda")

        def transcribe(self, _audio_path, **kwargs):
            captured["kwargs"] = kwargs
            return {"text": "ok", "segments": [], "language": "en"}

    fake_whisper = SimpleNamespace(load_model=lambda *_args, **_kwargs: FakeModel())
    monkeypatch.setitem(sys.modules, "whisper", fake_whisper)
    whisper_module._MODEL_CACHE.clear()
    whisper_module._MODEL_INFERENCE_LOCKS.clear()

    WhisperTranscriber(WhisperConfig(model="tiny", device=None)).transcribe(audio_file)

    assert captured["kwargs"]["fp16"] is False


def test_whisper_retries_cpu_fp32_after_cuda_invalid_values(monkeypatch, tmp_path: Path):
    audio_file = tmp_path / "audio.wav"
    audio_file.write_bytes(b"fake audio")
    loaded_devices = []

    class FailingCudaModel:
        device = SimpleNamespace(type="cuda")

        def transcribe(self, _audio_path, **_kwargs):
            raise ValueError("invalid values: tensor([[nan]], device='cuda:0')")

    class CpuModel:
        device = SimpleNamespace(type="cpu")

        def transcribe(self, _audio_path, **kwargs):
            assert kwargs["fp16"] is False
            return {"text": "cpu ok", "segments": [], "language": "en"}

    def load_model(_model_name, device=None):
        loaded_devices.append(device)
        return CpuModel() if device == "cpu" else FailingCudaModel()

    fake_whisper = SimpleNamespace(load_model=load_model)
    monkeypatch.setitem(sys.modules, "whisper", fake_whisper)
    whisper_module._MODEL_CACHE.clear()
    whisper_module._MODEL_INFERENCE_LOCKS.clear()

    text = WhisperTranscriber(WhisperConfig(model="tiny", device=None)).transcribe(audio_file)

    assert text == "cpu ok"
    assert loaded_devices == [None, "cpu"]
