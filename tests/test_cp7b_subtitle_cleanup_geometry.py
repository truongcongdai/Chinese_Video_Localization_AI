from dataclasses import dataclass

from universal_video_ai.render.renderer import RenderConfig, Renderer, TextOverlay
from universal_video_ai.render.subtitle_region_tracker import (
    AdaptiveSubtitleRegionConfig,
    AdaptiveSubtitleRegionTracker,
)


@dataclass
class Overlay:
    start: float
    end: float
    x: int
    y: int
    width: int
    height: int


def test_cleanup_is_tightly_padded_and_clamped_to_frame():
    tracker = AdaptiveSubtitleRegionTracker()
    overlay = Overlay(0.0, 1.0, 100, 690, 1120, 28)
    region = tracker.track([overlay], 1280, 720)[0][1]
    assert region.cleanup_safe is True
    assert region.cleanup_height / 720 <= 0.12
    assert region.cleanup_y + region.cleanup_height <= 720
    assert region.cleanup_width / 1280 <= 0.96


def test_tall_ocr_merge_never_becomes_giant_cleanup_band():
    tracker = AdaptiveSubtitleRegionTracker()
    overlay = Overlay(1.0, 2.0, 200, 360, 800, 240)
    region = tracker.track([overlay], 1280, 720)[0][1]
    assert region.cleanup_safe is False
    assert region.safety_reason == "detected_glyph_height_exceeds_limit"
    renderer = Renderer(RenderConfig(adaptive_text_residual_veil_enabled=False))
    filters = renderer._build_text_overlay_filters(
        [TextOverlay(1.0, 2.0, 200, 360, 800, 240, "Vietnamese")],
        frame_w=1280,
        frame_h=720,
    )
    assert not any(item.startswith("delogo=") for item in filters)
    assert any(item.startswith("drawtext=") for item in filters)


def test_varying_positions_remain_temporal_boxes_not_global_union():
    overlays = [
        TextOverlay(0.0, 1.0, 200, 620, 700, 34, "A"),
        TextOverlay(4.0, 5.0, 250, 180, 650, 32, "B"),
        TextOverlay(5.1, 6.0, 245, 182, 660, 33, "B2"),
        TextOverlay(10.0, 11.0, 210, 618, 690, 35, "C"),
    ]
    diagnostics = Renderer().get_cleanup_geometry(overlays, 1280, 720)
    assert len(diagnostics) == 4
    assert diagnostics[0]["y"] > 500
    assert diagnostics[1]["y"] < 300
    assert diagnostics[2]["y"] < 300
    assert diagnostics[3]["y"] > 500
    assert max(item["height_ratio"] for item in diagnostics) <= 0.12


def test_debug_geometry_reports_source_and_guard_state():
    diagnostics = Renderer().get_cleanup_geometry(
        [TextOverlay(0.1, 0.8, 20, 600, 1200, 40, "")], 1280, 720
    )
    assert diagnostics[0]["start"] == 0.1
    assert diagnostics[0]["frame_height"] == 720
    assert diagnostics[0]["source_box"]["height"] == 40
    assert "cleanup_safe" in diagnostics[0]
