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


def test_separate_reuses_completed_stems(tmp_path: Path, monkeypatch):
    audio_file = tmp_path / "audio.wav"
    audio_file.write_bytes(b"dummy audio")
    stems_dir = tmp_path / "demucs_output" / "htdemucs" / "audio"
    stems_dir.mkdir(parents=True)
    for stem in ("vocals", "drums", "bass", "other"):
        (stems_dir / f"{stem}.wav").write_bytes(b"completed stem")

    def unexpected_run(*args, **kwargs):
        raise AssertionError("Demucs should not run when all completed stems exist")

    monkeypatch.setattr("subprocess.run", unexpected_run)
    output = DemucsProcessor().separate(audio_file)

    assert output.vocals == stems_dir / "vocals.wav"
    assert output.other.stat().st_size > 0


def test_separate_uses_short_alias_when_demucs_output_path_is_too_long(tmp_path: Path, monkeypatch):
    audio_file = tmp_path / "very_long_source_title.wav"
    audio_file.write_bytes(b"dummy audio")
    captured = {}

    def mock_run(*args, **kwargs):
        cmd = kwargs.get("args", args[0])
        input_path = Path(cmd[-1])
        captured["input_path"] = input_path
        assert input_path.exists()

        output_dir = Path(cmd[cmd.index("-o") + 1])
        stems_dir = output_dir / "htdemucs" / input_path.stem
        stems_dir.mkdir(parents=True, exist_ok=True)
        for stem in ["vocals", "drums", "bass", "other"]:
            (stems_dir / f"{stem}.wav").write_bytes(b"stem content")

        result = MagicMock()
        result.returncode = 0
        result.stderr = ""
        result.stdout = ""
        return result

    monkeypatch.setattr("subprocess.run", mock_run)
    processor = DemucsProcessor()
    monkeypatch.setattr(processor, "_needs_short_input_alias", lambda audio, output: audio == audio_file.resolve())

    output = processor.separate(audio_file)

    assert captured["input_path"].stem.startswith("demucs_input_")
    assert not captured["input_path"].exists()
    assert output.vocals.exists()
    assert output.vocals.parent.name.startswith("demucs_input_")


def test_wav_command_uses_demucs_default_format_and_segment_flag(tmp_path: Path, monkeypatch):
    audio_file = tmp_path / "audio.wav"
    audio_file.write_bytes(b"dummy audio")
    captured = {}

    def mock_run(*args, **kwargs):
        cmd = kwargs.get("args", args[0])
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        result = MagicMock()
        result.returncode = 0
        result.stderr = ""
        result.stdout = ""

        output_dir = Path(cmd[cmd.index("-o") + 1])
        stems_dir = output_dir / "htdemucs" / "audio"
        stems_dir.mkdir(parents=True, exist_ok=True)
        for stem in ["vocals", "drums", "bass", "other"]:
            (stems_dir / f"{stem}.wav").write_bytes(b"stem content")
        return result

    monkeypatch.setattr("subprocess.run", mock_run)

    processor = DemucsProcessor(DemucsConfig(output_format="wav", segment_length=12))
    processor.separate(audio_file)

    cmd = captured["cmd"]
    assert "--format" not in cmd
    assert "--segment-length" not in cmd
    assert cmd[cmd.index("--segment") + 1] == "12"
    assert captured["kwargs"]["encoding"] == "utf-8"
    assert captured["kwargs"]["errors"] == "replace"


def test_mp3_command_uses_demucs_mp3_flag(tmp_path: Path, monkeypatch):
    audio_file = tmp_path / "audio.wav"
    audio_file.write_bytes(b"dummy audio")
    captured = {}

    def mock_run(*args, **kwargs):
        cmd = kwargs.get("args", args[0])
        captured["cmd"] = cmd
        result = MagicMock()
        result.returncode = 0
        result.stderr = ""
        result.stdout = ""

        output_dir = Path(cmd[cmd.index("-o") + 1])
        stems_dir = output_dir / "htdemucs" / "audio"
        stems_dir.mkdir(parents=True, exist_ok=True)
        for stem in ["vocals", "drums", "bass", "other"]:
            (stems_dir / f"{stem}.mp3").write_bytes(b"stem content")
        return result

    monkeypatch.setattr("subprocess.run", mock_run)

    processor = DemucsProcessor(DemucsConfig(output_format="mp3"))
    processor.separate(audio_file)

    cmd = captured["cmd"]
    assert "--mp3" in cmd
    assert "--format" not in cmd


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


def test_regular_demucs_uses_configured_long_timeout(tmp_path: Path, monkeypatch):
    audio_file = tmp_path / "long.wav"
    audio_file.write_bytes(b"audio")
    captured = {}

    def mock_run(cmd, **kwargs):
        captured["timeout"] = kwargs["timeout"]
        output_dir = Path(cmd[cmd.index("-o") + 1])
        stems_dir = output_dir / "htdemucs" / "long"
        stems_dir.mkdir(parents=True, exist_ok=True)
        for stem in ("vocals", "drums", "bass", "other"):
            (stems_dir / f"{stem}.wav").write_bytes(b"stem")
        result = MagicMock()
        result.returncode = 0
        result.stderr = ""
        result.stdout = ""
        return result

    monkeypatch.setattr("subprocess.run", mock_run)
    processor = DemucsProcessor(DemucsConfig(long_audio_timeout_seconds=12345))

    processor.separate(audio_file)

    assert captured["timeout"] == 12345


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


def test_large_audio_is_split_separated_and_concatenated(tmp_path: Path, monkeypatch):
    audio_file = tmp_path / "long_audio.wav"
    audio_file.write_bytes(b"large-enough-for-test")
    calls = []

    def successful_result():
        result = MagicMock()
        result.returncode = 0
        result.stderr = ""
        result.stdout = ""
        return result

    def mock_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        if cmd[0] == "ffmpeg" and "segment" in cmd:
            pattern = str(cmd[-1])
            Path(pattern.replace("%05d", "00000")).write_bytes(b"chunk-0")
            Path(pattern.replace("%05d", "00001")).write_bytes(b"chunk-1")
        elif cmd[0] == "demucs":
            separated_dir = Path(cmd[cmd.index("-o") + 1])
            chunk_paths = [Path(value) for value in cmd if str(value).endswith(".wav")]
            for chunk_path in chunk_paths:
                stems_dir = separated_dir / "htdemucs" / chunk_path.stem
                stems_dir.mkdir(parents=True, exist_ok=True)
                for stem in ("vocals", "drums", "bass", "other"):
                    (stems_dir / f"{stem}.wav").write_bytes(b"stem")
        elif cmd[0] == "ffmpeg" and "concat" in cmd:
            Path(cmd[-1]).write_bytes(b"joined-stem")
        return successful_result()

    monkeypatch.setattr("subprocess.run", mock_run)
    monkeypatch.setattr("shutil.which", lambda command: command)

    processor = DemucsProcessor(
        DemucsConfig(chunk_threshold_bytes=1, chunk_length_seconds=60)
    )
    output = processor.separate(audio_file)

    assert output.vocals.read_bytes() == b"joined-stem"
    assert output.drums.exists()
    demucs_calls = [cmd for cmd, _ in calls if cmd[0] == "demucs"]
    assert len(demucs_calls) == 1
    assert sum(str(value).endswith(".wav") for value in demucs_calls[0]) == 2
