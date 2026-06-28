# tests/test_renderer.py
from pathlib import Path
from unittest.mock import MagicMock

import subprocess
import pytest

from universal_video_ai.render.renderer import Renderer, RenderConfig, _check_ffmpeg_available


def test_check_ffmpeg_available(monkeypatch):
    from shutil import which

    # Mock which to return None
    monkeypatch.setattr("shutil.which", lambda cmd: None)
    assert not _check_ffmpeg_available()

    # Mock which to return a path
    monkeypatch.setattr("shutil.which", lambda cmd: "/usr/bin/ffmpeg")
    assert _check_ffmpeg_available()


def test_render_missing_inputs(tmp_path: Path):
    renderer = Renderer()
    with pytest.raises(FileNotFoundError):
        renderer.render(tmp_path / "no_video.mp4", tmp_path / "no_audio.mp3")

    # create video but no audio
    video = tmp_path / "v.mp4"
    video.write_bytes(b"v")
    with pytest.raises(FileNotFoundError):
        renderer.render(video, tmp_path / "no_audio.mp3")


def test_render_success_copy_video(tmp_path: Path, monkeypatch):
    video = tmp_path / "video.mp4"
    audio = tmp_path / "audio.mp3"
    video.write_bytes(b"video")
    audio.write_bytes(b"audio")

    # Mock subprocess.run to simulate ffmpeg success and create output file
    def mock_run(cmd, capture_output, text, check, timeout):
        # last arg is output path
        output_path = Path(cmd[-1])
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"final video")
        result = MagicMock()
        result.returncode = 0
        result.stderr = ""
        result.stdout = ""
        return result

    monkeypatch.setattr("subprocess.run", mock_run)

    renderer = Renderer()
    out = renderer.render(video, audio)
    assert out.exists()
    assert out.read_bytes() == b"final video"


def test_render_with_subtitles(tmp_path: Path, monkeypatch):
    video = tmp_path / "video.mp4"
    audio = tmp_path / "audio.mp3"
    subs = tmp_path / "subs.srt"
    video.write_bytes(b"video")
    audio.write_bytes(b"audio")
    subs.write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n")

    def mock_run(cmd, capture_output, text, check, timeout):
        # Expect subtitles filter used; create output file
        output_path = Path(cmd[-1])
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"final video with subs")
        res = MagicMock()
        res.returncode = 0
        res.stderr = ""
        res.stdout = ""
        return res

    monkeypatch.setattr("subprocess.run", mock_run)

    renderer = Renderer()
    out = renderer.render(video, audio, subtitles=subs)
    assert out.exists()
    assert out.read_bytes() == b"final video with subs"


def test_render_ffmpeg_error(tmp_path: Path, monkeypatch):
    video = tmp_path / "video.mp4"
    audio = tmp_path / "audio.mp3"
    video.write_bytes(b"video")
    audio.write_bytes(b"audio")

    def mock_run_fail(cmd, capture_output, text, check, timeout):
        res = MagicMock()
        res.returncode = 1
        res.stderr = "ffmpeg error"
        res.stdout = ""
        return res

    monkeypatch.setattr("subprocess.run", mock_run_fail)

    renderer = Renderer()
    with pytest.raises(RuntimeError):
        renderer.render(video, audio)