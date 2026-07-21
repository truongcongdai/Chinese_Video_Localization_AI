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


if __name__ == "__main__":
    unittest.main()
