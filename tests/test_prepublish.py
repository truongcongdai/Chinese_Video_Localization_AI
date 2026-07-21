"""Tests for read-only pre-publish video inspection."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from universal_video_ai.render.prepublish import inspect_for_publish, prepublish_report_to_dict


class PrepublishInspectionTests(unittest.TestCase):
    def _video_file(self, directory: str) -> Path:
        path = Path(directory) / "video.mp4"
        path.write_bytes(b"fake video")
        return path

    def test_portrait_h264_video_is_compatible_with_short_surfaces(self) -> None:
        probe = {
            "streams": [
                {"codec_type": "video", "width": 1080, "height": 1920, "codec_name": "h264", "pix_fmt": "yuv420p", "avg_frame_rate": "30/1"},
                {"codec_type": "audio", "codec_name": "aac"},
            ],
            "format": {"duration": "30.5"},
        }
        with tempfile.TemporaryDirectory() as directory, patch(
            "universal_video_ai.render.prepublish._probe_media", return_value=probe,
        ):
            report = inspect_for_publish(self._video_file(directory))

        self.assertTrue(report.ready)
        short_profiles = [item for item in report.platforms if item.format_name != "Video ngang"]
        self.assertTrue(all(item.status == "ready" for item in short_profiles))
        self.assertEqual(prepublish_report_to_dict(report)["facts"]["fps"], 30.0)

    def test_missing_audio_is_a_blocking_technical_error(self) -> None:
        probe = {
            "streams": [
                {"codec_type": "video", "width": 1280, "height": 720, "codec_name": "h264", "pix_fmt": "yuv420p", "avg_frame_rate": "25/1"},
            ],
            "format": {"duration": "12"},
        }
        with tempfile.TemporaryDirectory() as directory, patch(
            "universal_video_ai.render.prepublish._probe_media", return_value=probe,
        ):
            report = inspect_for_publish(self._video_file(directory))

        self.assertFalse(report.ready)
        self.assertIn("missing_audio", {issue.code for issue in report.issues})

    def test_low_resolution_and_unusual_fps_are_warnings(self) -> None:
        probe = {
            "streams": [
                {"codec_type": "video", "width": 360, "height": 640, "codec_name": "vp9", "pix_fmt": "yuv444p", "avg_frame_rate": "15/1"},
                {"codec_type": "audio", "codec_name": "opus"},
            ],
            "format": {"duration": "9"},
        }
        with tempfile.TemporaryDirectory() as directory, patch(
            "universal_video_ai.render.prepublish._probe_media", return_value=probe,
        ):
            report = inspect_for_publish(self._video_file(directory))

        self.assertTrue(report.ready)
        codes = {issue.code for issue in report.issues}
        self.assertTrue({"low_resolution", "unusual_fps", "uncommon_video_codec"}.issubset(codes))


if __name__ == "__main__":
    unittest.main()
