from pathlib import Path
import importlib.util


def _load_renderer():
    path = Path(__file__).parents[1] / "src/universal_video_ai/render/renderer.py"
    text = path.read_text(encoding="utf-8")
    assert "adaptive_text_residual_veil_opacity: float = 0.14" in text
    assert "adaptive_text_residual_veil_min_confidence: float = 0.72" in text
    assert "adaptive_text_residual_veil_max_frame_area_ratio: float = 0.055" in text
    assert "drawbox=x={region.x}:y={region.y}:w={region.width}:h={region.height}" in text
    assert "drawbox=x={cx}:y={cy}:w={cw}:h={ch}" not in text


def test_residual_veil_is_tight_and_subtle():
    _load_renderer()


def test_cleanup_expansion_is_not_excessive():
    path = Path(__file__).parents[1] / "src/universal_video_ai/render/subtitle_region_tracker.py"
    text = path.read_text(encoding="utf-8")
    assert "cleanup_extra_height_ratio: float = 0.42" in text
