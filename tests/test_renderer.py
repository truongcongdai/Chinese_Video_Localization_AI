# tests/test_renderer.py
from pathlib import Path
from unittest.mock import MagicMock

import subprocess
import time
import pytest

from universal_video_ai.render.renderer import (
    AnimatedSubtitleConfig,
    Renderer,
    RenderConfig,
    TextOverlay,
    _check_ffmpeg_available,
)
from universal_video_ai.postprocess.video_transform import FlipMode, TransformConfig


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

        def poll(self):
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


def test_default_render_does_not_add_platform_watermark(tmp_path: Path):
    video = tmp_path / "video.mp4"
    audio = tmp_path / "audio.mp3"
    output = tmp_path / "output.mp4"

    cmd = Renderer()._build_command(video, audio, output)

    assert "-filter_complex" not in cmd
    assert "-filter_complex_script" not in cmd
    assert "overlay=" not in " ".join(cmd)
    assert cmd[cmd.index("-c:v") + 1] == "copy"


def test_user_logo_overlay_is_opt_in_brand_watermark(tmp_path: Path):
    video = tmp_path / "video.mp4"
    audio = tmp_path / "audio.mp3"
    output = tmp_path / "output.mp4"
    logo = tmp_path / "my-logo.png"
    logo.write_bytes(b"logo")

    renderer = Renderer(
        RenderConfig(
            logo_path=str(logo),
            logo_corner="top_left",
            logo_size_px=96,
            logo_margin_px=12,
        )
    )

    cmd = renderer._build_command(video, audio, output)

    assert cmd.count("-i") == 3
    assert str(logo) in cmd
    assert "-filter_complex" in cmd
    filter_complex = cmd[cmd.index("-filter_complex") + 1]
    assert "[2:v]scale=96:-1[wm]" in filter_complex
    assert "overlay=12:12:shortest=1" in filter_complex


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


def test_many_animated_subtitles_use_filter_complex_script(tmp_path: Path):
    video = tmp_path / "video.mp4"
    audio = tmp_path / "audio.mp3"
    output = tmp_path / "output.mp4"
    segments = [
        {"text": f"Subtitle line {idx}", "start": float(idx), "end": float(idx) + 0.8}
        for idx in range(120)
    ]

    renderer = Renderer(
        RenderConfig(animated_subtitle_config=AnimatedSubtitleConfig(enabled=True))
    )

    cmd = renderer._build_command(video, audio, output, subtitle_segments=segments)

    assert "-filter_complex_script" in cmd
    script_path = Path(cmd[cmd.index("-filter_complex_script") + 1])
    assert script_path.exists()
    script_text = script_path.read_text(encoding="utf-8")
    assert script_text.startswith("[0:v]drawtext=")
    assert script_text.endswith("[outv]")
    assert script_text.count("drawtext=") == 120
    assert "-vf" not in cmd
    assert len(" ".join(cmd)) < 1000


def test_flip_runs_before_new_subtitles_are_drawn(tmp_path: Path):
    video = tmp_path / "video.mp4"
    audio = tmp_path / "audio.mp3"
    output = tmp_path / "output.mp4"
    segments = [{"text": "Readable subtitle", "start": 0.0, "end": 1.0}]

    renderer = Renderer(
        RenderConfig(
            animated_subtitle_config=AnimatedSubtitleConfig(enabled=True),
            transform_config=TransformConfig(
                enable_flip=True,
                flip_mode=FlipMode.HORIZONTAL,
            ),
        )
    )

    cmd = renderer._build_command(video, audio, output, subtitle_segments=segments)

    assert "-vf" in cmd
    vf = cmd[cmd.index("-vf") + 1]
    assert vf.startswith("hflip,drawtext=")
    assert renderer._post_subtitle_transform_config() is None


def test_flip_mirrors_text_cover_overlay_coordinates(tmp_path: Path, monkeypatch):
    video = tmp_path / "video.mp4"
    audio = tmp_path / "audio.mp3"
    output = tmp_path / "output.mp4"
    overlay = TextOverlay(
        start=0.0,
        end=1.0,
        x=50,
        y=20,
        width=100,
        height=40,
        text="Hi",
    )

    renderer = Renderer(
        RenderConfig(
            transform_config=TransformConfig(
                enable_flip=True,
                flip_mode=FlipMode.HORIZONTAL,
            ),
        )
    )
    monkeypatch.setattr(renderer, "_get_video_dimensions", lambda path: (640, 360))

    cmd = renderer._build_command(video, audio, output, text_overlays=[overlay])

    vf = cmd[cmd.index("-vf") + 1]
    assert vf.startswith("hflip,drawbox=x=490:y=20:w=100:h=40")


def test_text_overlay_drawtext_uses_baseline_vertical_center(tmp_path: Path, monkeypatch):
    video = tmp_path / "video.mp4"
    audio = tmp_path / "audio.mp3"
    output = tmp_path / "output.mp4"
    overlay = TextOverlay(
        start=0.0,
        end=1.0,
        x=50,
        y=20,
        width=220,
        height=80,
        text="Xin chào",
    )

    renderer = Renderer()
    monkeypatch.setattr(renderer, "_get_video_dimensions", lambda path: (640, 360))

    cmd = renderer._build_command(video, audio, output, text_overlays=[overlay])

    vf = cmd[cmd.index("-vf") + 1]
    assert "y=20+(80-ascent+descent)/2" in vf


def test_multiple_fractional_watermark_boxes_are_blurred(tmp_path: Path, monkeypatch):
    video = tmp_path / "video.mp4"
    audio = tmp_path / "audio.mp3"
    output = tmp_path / "output.mp4"

    renderer = Renderer(
        RenderConfig(
            watermark_boxes_fractional=(
                (0.0, 0.0, 0.25, 0.10),
                (0.30, 0.40, 0.70, 0.55),
            )
        )
    )
    monkeypatch.setattr(renderer, "_get_video_dimensions", lambda path: (1000, 500))

    cmd = renderer._build_command(video, audio, output)

    vf = cmd[cmd.index("-vf") + 1]
    assert "delogo=x=1:y=1:w=250:h=50:show=0" in vf
    assert "delogo=x=300:y=200:w=399:h=75:show=0" in vf


def test_fractional_watermark_box_at_frame_edge_is_clamped_for_delogo(tmp_path: Path, monkeypatch):
    video = tmp_path / "video.mp4"
    audio = tmp_path / "audio.mp3"
    output = tmp_path / "output.mp4"

    renderer = Renderer(
        RenderConfig(
            watermark_boxes_fractional=((0.0, 0.0, 1.0, 1.0),)
        )
    )
    monkeypatch.setattr(renderer, "_get_video_dimensions", lambda path: (1280, 720))

    cmd = renderer._build_command(video, audio, output)

    vf = cmd[cmd.index("-vf") + 1]
    assert "delogo=x=1:y=1:w=1278:h=718:show=0" in vf


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


def test_ffmpeg_timeout_allows_active_progress(monkeypatch):
    class ActiveFakePopen:
        def __init__(self, cmd, stdout=None, stderr=None, text=None, bufsize=None):
            self.started_at = time.monotonic()
            self.killed = False
            self.stderr = self._stderr()

        def _stderr(self):
            for idx in range(6):
                time.sleep(0.05)
                yield f"frame={idx * 100} time=00:00:0{idx}.00\n"

        def poll(self):
            return 0 if time.monotonic() - self.started_at >= 0.3 else None

        def wait(self):
            return 0

        def kill(self):
            self.killed = True

    monkeypatch.setattr("universal_video_ai.render.renderer.subprocess.Popen", ActiveFakePopen)

    renderer = Renderer()
    returncode, stderr_text = renderer._run_ffmpeg_with_progress(
        ["ffmpeg", "-version"],
        timeout_seconds=0.12,
        heartbeat_seconds=10.0,
    )

    assert returncode == 0
    assert "frame=500" in stderr_text
