# tests/test_model_manager.py
from pathlib import Path
import importlib.util
import shutil

import pytest
from unittest.mock import MagicMock

from universal_video_ai.models.manager import ModelManager


def test_check_executables_and_packages(monkeypatch):
    manager = ModelManager()

    # Simulate ffmpeg present, ffprobe absent
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/ffmpeg" if name == "ffmpeg" else None)

    # Simulate 'whisper' package present, 'torch' absent
    orig_find_spec = importlib.util.find_spec

    def fake_find_spec(name):
        if name == "whisper":
            return MagicMock()
        if name == "torch":
            return None
        return orig_find_spec(name)

    monkeypatch.setattr("importlib.util.find_spec", fake_find_spec)

    assert manager.check_ffmpeg_available() is True
    assert manager.check_ffprobe_available() is False
    assert manager.check_whisper_available() is True
    assert manager.check_torch_available() is False

    summary = manager.summary()
    assert "ffmpeg: OK" in summary
    assert "ffprobe: MISSING" in summary


def test_explain_missing_messages(monkeypatch):
    manager = ModelManager()

    # Simulate everything missing
    monkeypatch.setattr("shutil.which", lambda name: None)
    monkeypatch.setattr("importlib.util.find_spec", lambda name: None)

    msgs = manager.explain_missing()
    # Should mention a few critical tools
    assert any("ffmpeg not found" in m or "ffmpeg not found" in m.lower() or "ffmpeg not found" for m in msgs)
    assert any("whisper not found" in m or "whisper not found" in m.lower() or "whisper not found" for m in msgs)


def test_report_structure(monkeypatch):
    manager = ModelManager()
    # Simulate typical mix
    def fake_which(name):
        if name in ("ffmpeg", "ffprobe"):
            return f"/usr/bin/{name}"
        return None

    monkeypatch.setattr("shutil.which", fake_which)
    monkeypatch.setattr("importlib.util.find_spec", lambda name: None)

    report = manager.report()
    assert "ffmpeg" in report
    assert report["ffmpeg"].available is True
    assert report["whisper"].available is False