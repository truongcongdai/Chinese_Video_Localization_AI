from universal_video_ai.render.renderer import RenderConfig, Renderer, TextOverlay


def test_cleanup_window_extends_and_bridges_but_text_window_stays_exact():
    renderer = Renderer(RenderConfig())
    overlays = [
        TextOverlay(start=1.0, end=1.5, x=100, y=500, width=300, height=50, text="A"),
        TextOverlay(start=1.6, end=2.1, x=102, y=502, width=298, height=50, text="B"),
    ]

    filters = renderer._build_text_overlay_filters(overlays, frame_w=1280, frame_h=720)
    cleanup = [flt for flt in filters if flt.startswith("delogo=")]
    veils = [flt for flt in filters if flt.startswith("drawbox=")]
    text = [flt for flt in filters if flt.startswith("drawtext=")]

    assert cleanup
    assert veils
    assert text
    # Same learned band bridges the 100 ms boundary gap, preventing a flash.
    assert any("between(t\\,0.900\\,1.600)" in flt for flt in cleanup)
    # Replacement text still follows the exact canonical timing.
    assert any("between(t\\,1.000\\,1.500)" in flt for flt in text)


def test_residual_veil_is_translucent_and_uses_adaptive_region():
    renderer = Renderer(RenderConfig(
        adaptive_text_residual_veil_opacity=0.68,
        adaptive_text_residual_veil_min_confidence=0.0,
    ))
    overlay = TextOverlay(start=2.0, end=3.0, x=200, y=400, width=240, height=40, text="Translated")
    filters = renderer._build_text_overlay_filters([overlay], frame_w=1920, frame_h=1080)
    veils = [flt for flt in filters if flt.startswith("drawbox=")]

    assert len(veils) == 1
    assert "color=black@0.680" in veils[0]
    assert "between(t\\,1.800\\,3.200)" in veils[0]
