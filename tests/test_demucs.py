# tests/test_demucs.py
from pathlib import Path
from unittest.mock import MagicMock, patch
import subprocess

import pytest

from universal_video_ai.audio.demucs import DemucsProcessor, DemucsOutput, DemucsConfig


def test_demucs_config_defaults():
    config = DemucsConfig()
    assert config.model == "htdemucs"
    assert config.output_format == "wav"
    assert config.device == "cpu"
    assert config.segment_length is None


def test_get_output_dir(tmp_path: Path):
    processor = DemucsProcessor()
    audio_path = tmp_path / "audio.wav"

    output_dir = processor.get_output_dir(audio_path)
    assert output_dir == tmp_path / "demucs_output"

    # Test with custom output directory
    custom_dir = tmp_path / "custom_out"
    output_dir = processor.get_output_dir(audio_path, custom_dir)
    assert output_dir == custom_dir


def test_separate_file_not_found():
    processor = DemucsProcessor()
    audio_path = Path("/nonexistent/audio.wav")

    with pytest.raises(FileNotFoundError):
        processor.separate(audio_path)


def test_separate_is_directory(tmp_path: Path):
    processor = DemucsProcessor()
    audio_dir = tmp_path / "audio_dir"
    audio_dir.mkdir()

    with pytest.raises(FileNotFoundError):
        processor.separate(audio_dir)


def test_separate_success(tmp_path: Path, monkeypatch):
    # Create dummy audio file
    audio_file = tmp_path / "audio.wav"
    audio_file.write_bytes(b"dummy audio")

    # Mock subprocess.run to simulate successful demucs
    def mock_run(*args, **kwargs):
        result = MagicMock()
        result.returncode = 0
        result.stderr = ""
        result.stdout = ""

        # Create expected demucs output structure
        output_dir = Path(kwargs.get("args", args[0])[kwargs.get("args", args[0]).index("-o") + 1])
        model = "htdemucs"
        audio_stem = "audio"
        stems_dir = output_dir / model / audio_stem
        stems_dir.mkdir(parents=True, exist_ok=True)

        # Create stem files
        for stem in ["vocals", "drums", "bass", "other"]:
            (stems_dir / f"{stem}.wav").write_bytes(b"stem content")

        return result

    monkeypatch.setattr("subprocess.run", mock_run)

    processor = DemucsProcessor()
    output = processor.separate(audio_file)

    assert isinstance(output, DemucsOutput)
    assert output.vocals.exists()
    assert output.drums.exists()
    assert output.bass.exists()
    assert output.other.exists()
    assert output.vocals.name == "vocals.wav"


def test_separate_demucs_error(tmp_path: Path, monkeypatch):
    audio_file = tmp_path / "audio.wav"
    audio_file.write_bytes(b"dummy audio")

    # Mock subprocess.run to simulate demucs failure
    def mock_run_fail(*args, **kwargs):
        result = MagicMock()
        result.returncode = 1
        result.stderr = "demucs error: model not found"
        result.stdout = ""
        return result

    monkeypatch.setattr("subprocess.run", mock_run_fail)

    processor = DemucsProcessor()
    with pytest.raises(RuntimeError) as exc_info:
        processor.separate(audio_file)

    assert "Demucs separation failed" in str(exc_info.value)


def test_separate_missing_stems(tmp_path: Path, monkeypatch):
    audio_file = tmp_path / "audio.wav"
    audio_file.write_bytes(b"dummy audio")

    # Mock subprocess.run to return success but only create some stems
    def mock_run_incomplete(*args, **kwargs):
        result = MagicMock()
        result.returncode = 0
        result.stderr = ""
        result.stdout = ""

        # Create incomplete output (missing some stems)
        output_dir = Path(kwargs.get("args", args[0])[kwargs.get("args", args[0]).index("-o") + 1])
        model = "htdemucs"
        audio_stem = "audio"
        stems_dir = output_dir / model / audio_stem
        stems_dir.mkdir(parents=True, exist_ok=True)

        # Only create vocals, missing others
        (stems_dir / "vocals.wav").write_bytes(b"vocal content")

        return result

    monkeypatch.setattr("subprocess.run", mock_run_incomplete)

    processor = DemucsProcessor()
    with pytest.raises(RuntimeError) as exc_info:
        processor.separate(audio_file)

    assert "stem files" in str(exc_info.value).lower()


def test_separate_demucs_timeout(tmp_path: Path, monkeypatch):
    audio_file = tmp_path / "audio.wav"
    audio_file.write_bytes(b"dummy audio")

    # Mock subprocess.run to raise TimeoutExpired
    def mock_run_timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("demucs", timeout=3600)

    monkeypatch.setattr("subprocess.run", mock_run_timeout)

    processor = DemucsProcessor()
    with pytest.raises(RuntimeError) as exc_info:
        processor.separate(audio_file)

    assert "timed out" in str(exc_info.value).lower()


def test_separate_demucs_not_found(tmp_path: Path, monkeypatch):
    audio_file = tmp_path / "audio.wav"
    audio_file.write_bytes(b"dummy audio")

    # Mock subprocess.run to raise FileNotFoundError
    def mock_run_not_found(*args, **kwargs):
        raise FileNotFoundError("demucs")

    monkeypatch.setattr("subprocess.run", mock_run_not_found)

    processor = DemucsProcessor()
    with pytest.raises(RuntimeError) as exc_info:
        processor.separate(audio_file)

    assert "not installed" in str(exc_info.value).lower()


def test_check_demucs_available(monkeypatch):
    from universal_video_ai.audio.demucs import _check_demucs_available

    # Mock shutil.which to return None
    monkeypatch.setattr("shutil.which", lambda cmd: None)
    assert not _check_demucs_available()

    # Mock to return path
    monkeypatch.setattr("shutil.which", lambda cmd: "/usr/bin/demucs")
    assert _check_demucs_available()


def test_demucs_output_frozen():
    output = DemucsOutput(
        vocals=Path("/tmp/vocals.wav"),
        drums=Path("/tmp/drums.wav"),
        bass=Path("/tmp/bass.wav"),
        other=Path("/tmp/other.wav"),
    )

    # Should not be able to modify frozen dataclass
    with pytest.raises(AttributeError):
        output.vocals = Path("/tmp/different.wav")