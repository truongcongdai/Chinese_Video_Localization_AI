# tests/test_audio_extractor_commit11.py
from pathlib import Path
import json
import subprocess
from unittest.mock import MagicMock

import pytest

from universal_video_ai.audio.extractor import AudioExtractor, AudioConfig
from universal_video_ai.audio.exceptions import AudioExtractionError
from universal_video_ai.audio.ffprobe import FFprobeResult


def _ffprobe_stdout(sample_rate=44100, channels=1, duration=2.4, bit_rate=128000, format_name="wav"):
    payload = {
        "streams": [
            {"sample_rate": str(sample_rate), "channels": channels, "bit_rate": str(bit_rate)}
        ],
        "format": {"duration": str(duration), "bit_rate": str(bit_rate), "format_name": format_name},
    }
    return json.dumps(payload)


def test_successful_extraction_with_ffprobe(tmp_path: Path, monkeypatch):
    video = tmp_path / "video.mp4"
    video.write_bytes(b"fake-video")

    out_dir = tmp_path / "out"
    cfg = AudioConfig(sample_rate=16000, channels=1)
    extractor = AudioExtractor(config=cfg)

    def fake_run(cmd, capture_output, text, check, timeout):
        if cmd[0] == "ffmpeg":
            # create output file path (last arg)
            out_path = Path(cmd[-1])
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(b"audio-bytes")
            r = MagicMock()
            r.returncode = 0
            r.stdout = ""
            r.stderr = ""
            return r
        elif cmd[0] == "ffprobe":
            r = MagicMock()
            r.returncode = 0
            r.stdout = _ffprobe_stdout(sample_rate=16000, channels=1, duration=1.5, bit_rate=64000)
            r.stderr = ""
            return r
        else:
            raise RuntimeError("unexpected cmd")

    monkeypatch.setattr("subprocess.run", fake_run)

    result = extractor.extract(video_path=video, output_dir=out_dir)
    assert result.success
    assert result.audio_path.exists()
    assert result.sample_rate == 16000
    assert result.channels == 1
    assert abs(result.duration - 1.5) < 1e-6
    assert result.format in ("wav", "wav")  # format name may be 'wav' or similar


def test_missing_input_raises(tmp_path: Path):
    extractor = AudioExtractor()
    missing = tmp_path / "missing.mp4"
    with pytest.raises(FileNotFoundError):
        extractor.extract(missing)


def test_ffmpeg_failure_raises(tmp_path: Path, monkeypatch):
    video = tmp_path / "in.mp4"
    video.write_bytes(b"v")

    def fake_fail(cmd, capture_output, text, check, timeout):
        r = MagicMock()
        r.returncode = 1
        r.stderr = "error"
        r.stdout = ""
        return r

    monkeypatch.setattr("subprocess.run", fake_fail)

    extractor = AudioExtractor()
    with pytest.raises(AudioExtractionError):
        extractor.extract(video)


def test_ffprobe_unavailable_fallback(tmp_path: Path, monkeypatch):
    video = tmp_path / "v2.mp4"
    video.write_bytes(b"v")

    out_dir = tmp_path / "out2"

    # ffmpeg success but ffprobe missing: simulate ffprobe raising FileNotFoundError
    def fake_run(cmd, capture_output, text, check, timeout):
        if cmd[0] == "ffmpeg":
            out_path = Path(cmd[-1])
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(b"audio")
            r = MagicMock()
            r.returncode = 0
            r.stdout = ""
            r.stderr = ""
            return r
        elif cmd[0] == "ffprobe":
            # ffprobe not available at runtime
            raise FileNotFoundError("ffprobe")
        else:
            raise RuntimeError("unexpected")

    monkeypatch.setattr("subprocess.run", fake_run)

    # Also simulate that FFprobe reports not available by making shutil.which return None
    import shutil as _shutil
    monkeypatch.setattr("shutil.which", lambda name: True if name == "ffmpeg" else None)

    # recreate extractor to pick up changed availability
    extractor = AudioExtractor(config=AudioConfig(sample_rate=22050, channels=2))
    # ensure ffprobe helper believes it is not available
    extractor._ffprobe = extractor._ffprobe  # object exists; the ffprobe.probe will catch FileNotFoundError

    result = extractor.extract(video_path=video, output_dir=out_dir)
    assert result.success
    # when ffprobe unavailable, extractor should fallback to requested sample_rate and channels
    assert result.sample_rate == 22050
    assert result.channels == 2


def test_invalid_output_zero_size(tmp_path: Path, monkeypatch):
    video = tmp_path / "v3.mp4"
    video.write_bytes(b"v")
    out_dir = tmp_path / "out3"

    # simulate ffmpeg returning success but creating zero-byte file
    def fake_run_zero(cmd, capture_output, text, check, timeout):
        if cmd[0] == "ffmpeg":
            out_path = Path(cmd[-1])
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(b"")  # zero bytes
            r = MagicMock()
            r.returncode = 0
            r.stdout = ""
            r.stderr = ""
            return r
        elif cmd[0] == "ffprobe":
            r = MagicMock()
            r.returncode = 0
            r.stdout = _ffprobe_stdout(sample_rate=44100, channels=1, duration=0.0, bit_rate=0)
            r.stderr = ""
            return r
        else:
            raise RuntimeError("unexpected")

    monkeypatch.setattr("subprocess.run", fake_run_zero)

    extractor = AudioExtractor()
    with pytest.raises(AudioExtractionError):
        extractor.extract(video, output_dir=out_dir)