"""Regression tests for OCR subtitle/ad region separation."""

import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from universal_video_ai.orchestrator.service import LocalizationConfig
from universal_video_ai.orchestrator.factory import create_localization_service
from universal_video_ai.render.text_detector import OnScreenTextDetector, SubtitleTimingWindow


class TextDetectorExclusionTests(unittest.TestCase):
    def test_default_config_excludes_top_right_ad_corner(self) -> None:
        regions = LocalizationConfig().watermark_exclude_regions_fractional

        self.assertIn((0.65, 0.00, 1.00, 0.35), regions)

    def test_factory_does_not_override_top_right_exclusion(self) -> None:
        service = create_localization_service()

        self.assertIn(
            (0.65, 0.00, 1.00, 0.35),
            service.config.watermark_exclude_regions_fractional,
        )

    def test_top_right_ad_is_dropped_but_center_subtitle_is_kept(self) -> None:
        boxes = [
            (780, 40, 1050, 130),   # persistent advertisement, upper-right
            (240, 1400, 840, 1460), # actual centred subtitle
        ]
        exclusions = [(702, 0, 1080, 672)]

        kept = OnScreenTextDetector._drop_excluded_boxes(None, boxes, exclusions)

        self.assertEqual(kept, [(240, 1400, 840, 1460)])

    def test_upper_center_caption_is_not_removed(self) -> None:
        boxes = [(270, 250, 700, 315)]
        exclusions = [(702, 0, 1080, 672)]

        kept = OnScreenTextDetector._drop_excluded_boxes(None, boxes, exclusions)

        self.assertEqual(kept, boxes)

    def test_corner_text_is_not_used_as_subtitle_candidate(self) -> None:
        boxes = [
            (20, 40, 220, 95),       # top-left UI/title
            (875, 90, 1060, 150),    # top-right ad/watermark
            (230, 1320, 850, 1390),  # centered subtitle
        ]

        kept = OnScreenTextDetector._keep_candidate_subtitle_boxes(
            None, boxes, frame_w=1080, frame_h=1920,
            region_fractional=(0.08, 0.25, 0.92, 0.95),
        )

        self.assertEqual(kept, [(230, 1320, 850, 1390)])

    def test_mid_screen_center_subtitle_candidate_is_kept(self) -> None:
        boxes = [(260, 620, 820, 690)]

        kept = OnScreenTextDetector._keep_candidate_subtitle_boxes(
            None, boxes, frame_w=1080, frame_h=1920,
            region_fractional=(0.08, 0.25, 0.92, 0.95),
        )

        self.assertEqual(kept, boxes)

    def test_presence_offset_estimator_accepts_point_one_second_offset(self) -> None:
        detector = OnScreenTextDetector()
        detector._select_presence_offset_anchors = (
            lambda source_segments, max_segments: [(0.0, 1.0, "柳夫人"), (2.0, 3.0, "你糊弄我")]
        )
        detector._offset_candidates = lambda search_radius, step: [0.0, 0.1]
        detector._extract_frame = lambda video_path, at_seconds, out_path: out_path.write_bytes(b"x") or True
        detector._subtitle_presence_score = (
            lambda frame_path: 0.020
            if frame_path.name in {"presence_2.jpg", "presence_3.jpg"} else 0.003
        )

        estimate = detector._estimate_subtitle_time_offset_by_presence(
            __file__,
            [(0.0, 1.0, "柳夫人"), (2.0, 3.0, "你糊弄我")],
            search_radius=0.2,
            step=0.1,
            refine_step=0.1,
            min_offset=0.05,
            min_best_to_zero_delta=0.001,
        )

        self.assertIsNotNone(estimate)
        self.assertEqual(estimate.offset, 0.1)
        self.assertIsNone(estimate.apply_after)

    def test_presence_score_rejects_bright_object_but_accepts_subtitle_text(self) -> None:
        detector = OnScreenTextDetector()

        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            bright_object = tmp_dir / "bright_object.png"
            subtitle_text = tmp_dir / "subtitle_text.png"

            image = Image.new("RGB", (1280, 720), (25, 25, 30))
            draw = ImageDraw.Draw(image)
            draw.rectangle((360, 610, 920, 660), fill=(235, 235, 235))
            image.save(bright_object)

            image = Image.new("RGB", (1280, 720), (25, 25, 30))
            draw = ImageDraw.Draw(image)
            try:
                font = ImageFont.truetype("DejaVuSans-Bold.ttf", 56)
            except Exception:
                font = ImageFont.load_default()
            draw.text(
                (300, 600),
                "Subtitle text here now",
                fill=(255, 255, 255),
                font=font,
                stroke_width=4,
                stroke_fill=(0, 0, 0),
            )
            image.save(subtitle_text)

            self.assertLess(detector._subtitle_presence_score(bright_object), 0.012)
            self.assertGreater(detector._subtitle_presence_score(subtitle_text), 0.012)

    def test_presence_offset_estimator_accepts_point_zero_five_second_offset(self) -> None:
        detector = OnScreenTextDetector()
        detector._select_presence_offset_anchors = (
            lambda source_segments, max_segments: [(0.0, 1.0, "柳夫人")]
        )
        detector._offset_candidates = lambda search_radius, step: [0.0]
        detector._extract_frame = lambda video_path, at_seconds, out_path: out_path.write_text(
            f"{at_seconds:.2f}", encoding="utf-8"
        ) or True

        def score(frame_path):
            timestamp = float(frame_path.read_text(encoding="utf-8"))
            return 0.020 if abs(timestamp - 0.55) < 0.001 else 0.003

        detector._subtitle_presence_score = score

        estimate = detector._estimate_subtitle_time_offset_by_presence(
            __file__,
            [(0.0, 1.0, "柳夫人")],
            search_radius=0.1,
            step=0.1,
            refine_step=0.05,
            min_offset=0.03,
            min_best_to_zero_delta=0.001,
            min_visible_matches=1,
        )

        self.assertIsNotNone(estimate)
        self.assertEqual(estimate.offset, 0.05)

    def test_presence_offset_estimator_is_limited_to_small_offsets(self) -> None:
        detector = OnScreenTextDetector()
        captured = {}

        detector._estimate_subtitle_time_offset_by_presence = (
            lambda video_path, source_segments, **kwargs: captured.update(kwargs) or None
        )

        estimate = detector.estimate_subtitle_time_offset(
            __file__,
            [(0.0, 1.0, "柳夫人"), (8.0, 9.0, "你糊弄我")],
            search_radius=12.0,
            use_text_ocr_fallback=False,
            min_offset=0.05,
        )

        self.assertIsNone(estimate)
        self.assertEqual(captured["search_radius"], 1.0)
        self.assertEqual(captured["min_offset"], 0.05)

    def test_detect_subtitle_windows_for_segments_finds_first_cue_at_point_four(self) -> None:
        detector = OnScreenTextDetector()
        detector._extract_frame = lambda video_path, at_seconds, out_path: out_path.write_text(
            f"{at_seconds:.2f}", encoding="utf-8"
        ) or True

        def score(frame_path):
            timestamp = float(frame_path.read_text(encoding="utf-8"))
            return 0.020 if 0.4 <= timestamp <= 1.2 else 0.0

        detector._subtitle_presence_score = score

        windows = detector.detect_subtitle_windows_for_segments(
            __file__,
            [(0.0, 1.0, "æŸ³å¤«äºº")],
            audio_duration=5.0,
            search_radius=0.6,
            step=0.1,
            min_visible_samples=2,
        )

        self.assertIsNotNone(windows[0])
        self.assertAlmostEqual(windows[0].start, 0.35)
        self.assertAlmostEqual(windows[0].end, 1.25)

    def test_ocr_boundary_refine_keeps_first_cue_out_of_zero_false_positive(self) -> None:
        detector = OnScreenTextDetector()
        detector._get_video_dimensions = lambda video_path: (1280, 720)
        detector._extract_frame = lambda video_path, at_seconds, out_path: out_path.write_text(
            f"{at_seconds:.2f}", encoding="utf-8"
        ) or True
        detector._subtitle_presence_score = lambda frame_path: 0.020

        def detect_boxes(frame_path):
            timestamp = float(frame_path.read_text(encoding="utf-8"))
            return [(640, 390, 730, 420)] if timestamp >= 0.3 else []

        detector._detect_boxes_in_frame = detect_boxes

        windows = detector.detect_subtitle_windows_for_segments(
            __file__,
            [(0.0, 1.0, "æŸ³å¤«äºº")],
            audio_duration=5.0,
            search_radius=0.6,
            step=0.1,
            min_visible_samples=2,
        )

        self.assertIsNotNone(windows[0])
        self.assertAlmostEqual(windows[0].start, 0.25)
        self.assertGreater(windows[0].end, windows[0].start)

    def test_detected_subtitle_windows_are_trimmed_to_avoid_overlap(self) -> None:
        windows = OnScreenTextDetector._trim_overlapping_subtitle_windows([
            SubtitleTimingWindow(start=0.25, end=2.85, confidence=0.8),
            SubtitleTimingWindow(start=1.15, end=4.85, confidence=0.8),
        ])

        self.assertEqual(windows[0], SubtitleTimingWindow(start=0.25, end=1.12, confidence=0.8))
        self.assertEqual(windows[1], SubtitleTimingWindow(start=1.15, end=4.85, confidence=0.8))

    def test_learn_subtitle_band_prefers_horizontal_dialogue_over_vertical_watermark(self) -> None:
        detector = OnScreenTextDetector()
        boxes = [
            # Persistent vertical/side text: many narrow boxes.
            (12, 210, 36, 236),
            (12, 230, 36, 256),
            (12, 250, 36, 276),
            (12, 270, 36, 296),
            # Actual dialogue subtitle: fewer but horizontal/wide boxes.
            (632, 386, 782, 416),
            (640, 392, 736, 418),
        ]

        band_center, line_height = detector._learn_subtitle_band(boxes)

        self.assertGreater(band_center, 370)
        self.assertLess(band_center, 430)
        self.assertGreaterEqual(line_height, 26)

    def test_detect_regions_ignores_upper_noise_and_keeps_subtitle_band(self) -> None:
        detector = OnScreenTextDetector()
        detector._get_video_dimensions = lambda video_path: (1280, 720)
        detector._extract_frame = lambda video_path, at_seconds, out_path: out_path.write_text(
            f"{at_seconds:.2f}", encoding="utf-8"
        ) or True

        def detect_boxes(frame_path):
            timestamp = float(frame_path.read_text(encoding="utf-8"))
            if timestamp < 1.0:
                return [
                    (20, 60, 300, 104),     # title/watermark noise
                    (632, 620, 780, 652),   # first cue, subtitle band
                ]
            return [
                (80, 80, 520, 124),       # upper horizontal noise
                (426, 618, 576, 650),     # later cue, subtitle band
            ]

        detector._detect_boxes_in_frame = detect_boxes

        regions = detector.detect_regions_for_windows(
            __file__,
            [(0.25, 0.95), (3.5, 4.5)],
            samples_per_window=1,
            fill_undetected_windows=False,
        )

        self.assertEqual(len(regions), 2)
        self.assertEqual(regions[0].y, 610)
        self.assertEqual(regions[0].height, 52)
        self.assertEqual(regions[1].y, 608)
        self.assertEqual(regions[1].height, 52)


if __name__ == "__main__":
    unittest.main()


def test_region_detector_never_falls_back_to_upper_title_boxes(tmp_path: Path) -> None:
    detector = OnScreenTextDetector()
    detector._get_video_dimensions = lambda _video: (1280, 720)
    detector._extract_frame = lambda _video, _time, out_path: out_path.write_bytes(b"frame") or True
    detector._subtitle_presence_score = lambda _frame: 0.02
    detector._detect_boxes_in_frame = lambda _frame: [(200, 90, 900, 150)]

    regions = detector.detect_regions_for_windows(
        tmp_path / "video.mp4",
        [(0.0, 1.0)],
        samples_per_window=1,
    )

    assert regions == []
