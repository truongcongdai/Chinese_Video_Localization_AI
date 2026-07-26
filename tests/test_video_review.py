from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from universal_video_ai.render.video_review import review_finished_video, video_review_report_to_dict


def _video_file(directory: str) -> Path:
    path = Path(directory) / "video.mp4"
    path.write_bytes(b"fake video")
    return path


def test_video_review_flags_weak_hook_and_long_subtitle() -> None:
    probe = {
        "streams": [
            {
                "codec_type": "video",
                "width": 1080,
                "height": 1920,
                "codec_name": "h264",
                "pix_fmt": "yuv420p",
                "avg_frame_rate": "30/1",
            },
            {"codec_type": "audio", "codec_name": "aac"},
        ],
        "format": {"duration": "45"},
    }
    segments = [
        {
            "start": 0,
            "end": 2,
            "text": "Trong video này tôi sẽ nói về một chủ đề khá dài nhưng chưa có lợi ích rõ ràng cho người xem",
        }
    ]

    with TemporaryDirectory() as directory, patch(
        "universal_video_ai.render.prepublish._probe_media",
        return_value=probe,
    ), patch(
        "universal_video_ai.render.video_review.analyze_output_quality",
        return_value=[],
    ):
        report = review_finished_video(_video_file(directory), segments=segments, title="Test title")

    categories = {finding.category for finding in report.findings}
    assert {"hook", "subtitle"}.issubset(categories)
    assert report.score < 100


def test_video_review_surfaces_missing_audio_as_error() -> None:
    probe = {
        "streams": [
            {
                "codec_type": "video",
                "width": 1280,
                "height": 720,
                "codec_name": "h264",
                "pix_fmt": "yuv420p",
                "avg_frame_rate": "30/1",
            }
        ],
        "format": {"duration": "30"},
    }

    with TemporaryDirectory() as directory, patch(
        "universal_video_ai.render.prepublish._probe_media",
        return_value=probe,
    ), patch(
        "universal_video_ai.render.video_review.analyze_output_quality",
        return_value=[],
    ):
        report = review_finished_video(_video_file(directory), title="")

    payload = video_review_report_to_dict(report)
    assert payload["score"] <= 72
    assert any(item["severity"] == "error" for item in payload["findings"])
