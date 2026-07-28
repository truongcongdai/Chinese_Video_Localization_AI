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


if __name__ == "__main__":
    unittest.main()
