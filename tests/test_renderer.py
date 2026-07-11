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


def _make_fake_popen(returncode: int, stderr_text: str, output_writer=None):
    """Build a fake Popen-like object matching what _run_ffmpeg_with_progress expects."""

    class FakePopen:
        def __init__(self, cmd, stdout=None, stderr=None, text=None, bufsize=None):
            self.cmd = cmd
            self.stderr = iter(stderr_text.splitlines(keepends=True))
            self._returncode = returncode
            if output_writer:
                output_writer(cmd)

        def wait(self, timeout=None):
            return self._returncode

        def kill(self):
            pass

    return FakePopen


def test_render_success_copy_video(tmp_path: Path, monkeypatch):
    video = tmp_path / "video.mp4"
    audio = tmp_path / "audio.mp3"
    video.write_bytes(b"video")
    audio.write_bytes(b"audio")

    def write_output(cmd):
        output_path = Path(cmd[-1])
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"final video")

    monkeypatch.setattr(
        "universal_video_ai.render.renderer.subprocess.Popen",
        _make_fake_popen(0, "", output_writer=write_output),
    )

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

    def write_output(cmd):
        output_path = Path(cmd[-1])
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"final video with subs")

    monkeypatch.setattr(
        "universal_video_ai.render.renderer.subprocess.Popen",
        _make_fake_popen(0, "", output_writer=write_output),
    )

    renderer = Renderer()
    out = renderer.render(video, audio, subtitles=subs)
    assert out.exists()
    assert out.read_bytes() == b"final video with subs"


def test_render_ffmpeg_error(tmp_path: Path, monkeypatch):
    video = tmp_path / "video.mp4"
    audio = tmp_path / "audio.mp3"
    video.write_bytes(b"video")
    audio.write_bytes(b"audio")

    monkeypatch.setattr(
        "universal_video_ai.render.renderer.subprocess.Popen",
        _make_fake_popen(1, "ffmpeg error"),
    )

    renderer = Renderer()
    with pytest.raises(RuntimeError):
        renderer.render(video, audio)