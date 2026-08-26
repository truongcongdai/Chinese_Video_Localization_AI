from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from universal_video_ai.postprocess.video_transform import TransformConfig, VideoTransformer


def test_split_screen_builds_a_labeled_filter_complex(tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"
    image = tmp_path / "split.png"
    output = tmp_path / "output.mp4"
    source.write_bytes(b"video")
    image.write_bytes(b"image")
    config = TransformConfig(
        target_width=1080,
        target_height=1920,
        enable_split_screen=True,
        overlay_image_path=image,
        split_mode="vertical",
    )

    def fake_run(command, **_kwargs):
        output.write_bytes(b"rendered")
        assert "-filter_complex" in command
        graph = command[command.index("-filter_complex") + 1]
        assert "[main][still]hstack=inputs=2[out]" in graph
        assert command[command.index("-map") + 1] == "[out]"
        assert "-loop" in command
        return SimpleNamespace(returncode=0, stderr="")

    with patch("universal_video_ai.postprocess.video_transform.subprocess.run", side_effect=fake_run):
        assert VideoTransformer(config).transform(source, output) is True
