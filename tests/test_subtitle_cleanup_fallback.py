from universal_video_ai.orchestrator.service import LocalizationService
from universal_video_ai.render.renderer import RenderConfig, Renderer, TextOverlay
from universal_video_ai.render.text_detector import TextRegion


def test_missing_subtitle_window_uses_local_interpolated_region():
    windows = [(0.0, 1.0), (1.0, 2.0), (2.0, 3.0)]
    detected = [
        TextRegion(0.0, 1.0, 100, 700, 400, 60),
        TextRegion(2.0, 3.0, 120, 704, 420, 64),
    ]
    regions = LocalizationService._fill_missing_text_regions(windows, detected, 62.0)
    assert len(regions) == 3
    middle = regions[1]
    assert middle.start == 1.0 and middle.end == 2.0
    assert 100 <= middle.x <= 120
    assert 700 <= middle.y <= 704
    assert 400 <= middle.width <= 420


def test_missing_window_uses_nearest_band_when_layout_changes():
    windows = [(0.0, 1.0), (5.0, 6.0), (10.0, 11.0)]
    detected = [
        TextRegion(0.0, 1.0, 100, 700, 400, 60),
        TextRegion(10.0, 11.0, 200, 250, 500, 70),
    ]
    regions = LocalizationService._fill_missing_text_regions(windows, detected, 65.0)
    middle = regions[1]
    # Different bands must not be averaged into a bogus in-between position.
    assert middle.y in {250, 700}


def test_renderer_defaults_to_natural_cleanup_without_white_box():
    config = RenderConfig()
    assert config.adaptive_text_drawbox_enabled is False
    assert config.adaptive_text_cleanup_passes >= 2


def test_cleanup_filters_use_multiple_delogo_passes_and_no_drawbox():
    renderer = Renderer.__new__(Renderer)
    renderer.config = RenderConfig()
    overlay = TextOverlay(0.0, 1.0, 100, 700, 400, 60, "")
    filters = renderer._build_text_overlay_filters([overlay], 1920, 1080)
    assert sum(item.startswith("delogo=") for item in filters) >= 2
    assert not any(item.startswith("drawbox=") for item in filters)
