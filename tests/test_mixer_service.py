# tests/test_mixer_service.py
from pathlib import Path
import pytest

from universal_video_ai.mixer.service import MixerService, MixerConfig, AudioMix


def test_mixer_no_secondary_returns_primary(tmp_path: Path):
    primary = tmp_path / "primary.wav"
    primary.write_bytes(b"\x00" * 1024)

    service = MixerService()
    spec = AudioMix(primary_audio=primary, secondary_audio=None)
    result = service.mix(spec, tmp_path / "output.wav")

    assert result == primary


def test_mixer_with_secondary_requires_ffmpeg(tmp_path: Path, monkeypatch):
    primary = tmp_path / "primary.wav"
    secondary = tmp_path / "secondary.wav"
    primary.write_bytes(b"\x00" * 1024)
    secondary.write_bytes(b"\x00" * 1024)

    # Mock ffmpeg unavailable
    import shutil
    monkeypatch.setattr(shutil, "which", lambda x: None)

    service = MixerService()
    spec = AudioMix(primary_audio=primary, secondary_audio=secondary)

    with pytest.raises(RuntimeError):
        service.mix(spec, tmp_path / "output.wav")