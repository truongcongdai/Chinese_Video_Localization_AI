"""Regression tests for OCR subtitle/ad region separation."""

import unittest

from universal_video_ai.orchestrator.service import LocalizationConfig
from universal_video_ai.orchestrator.factory import create_localization_service
from universal_video_ai.render.text_detector import OnScreenTextDetector


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
            lambda frame_path: 0.010
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
            return 0.010 if abs(timestamp - 0.55) < 0.001 else 0.003

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


if __name__ == "__main__":
    unittest.main()
