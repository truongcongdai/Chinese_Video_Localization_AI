from dataclasses import dataclass

from universal_video_ai.render.subtitle_region_tracker import AdaptiveSubtitleRegionTracker


@dataclass
class Overlay:
    start: float
    end: float
    x: int
    y: int
    width: int
    height: int


def test_one_off_tall_shifted_ocr_box_uses_local_consensus():
    overlays = [
        Overlay(100.0, 103.0, 300, 575, 650, 105),
        Overlay(103.0, 106.0, 350, 578, 580, 102),
        # OCR accidentally merged a nearby label into the subtitle box.
        Overlay(106.0, 109.0, 780, 468, 403, 251),
        Overlay(109.0, 112.0, 360, 576, 600, 104),
        Overlay(112.0, 115.0, 330, 574, 620, 106),
    ]
    tracked = AdaptiveSubtitleRegionTracker().track(overlays, 1280, 720)
    bad_region = tracked[2][1]
    neighbor_centers = [tracked[1][1].y + tracked[1][1].height / 2, tracked[3][1].y + tracked[3][1].height / 2]
    center = bad_region.y + bad_region.height / 2
    assert abs(center - sum(neighbor_centers) / 2) < 45
    assert bad_region.height < 190
