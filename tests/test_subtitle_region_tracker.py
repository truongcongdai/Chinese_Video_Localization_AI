from dataclasses import dataclass

from universal_video_ai.render.subtitle_region_tracker import AdaptiveSubtitleRegionTracker


@dataclass(frozen=True)
class Overlay:
    start: float
    end: float
    x: int
    y: int
    width: int
    height: int


def test_tracker_rejects_isolated_title_and_keeps_bottom_band():
    overlays = [
        Overlay(0, 1, 120, 620, 500, 60),
        Overlay(1, 2, 130, 625, 480, 58),
        Overlay(2, 3, 140, 90, 300, 45),  # isolated title noise
        Overlay(3, 4, 125, 618, 510, 62),
    ]
    tracked = AdaptiveSubtitleRegionTracker().track(overlays, 1280, 720)
    _, noisy_region = tracked[2]
    assert noisy_region.y > 500


def test_tracker_supports_multiple_real_subtitle_bands():
    overlays = [
        Overlay(0, 1, 100, 600, 500, 50),
        Overlay(1, 2, 100, 605, 500, 50),
        Overlay(20, 21, 100, 300, 500, 50),
        Overlay(21, 22, 100, 305, 500, 50),
    ]
    tracked = AdaptiveSubtitleRegionTracker().track(overlays, 1280, 720)
    assert tracked[0][1].y > 550
    assert 250 < tracked[2][1].y < 350


def test_padding_scales_with_detected_line_height():
    small = [Overlay(0, 1, 100, 600, 300, 20)]
    large = [Overlay(0, 1, 100, 500, 300, 80)]
    tracker = AdaptiveSubtitleRegionTracker()
    small_region = tracker.track(small, 1280, 720)[0][1]
    large_region = tracker.track(large, 1280, 720)[0][1]
    assert (large_region.height - 80) > (small_region.height - 20)
