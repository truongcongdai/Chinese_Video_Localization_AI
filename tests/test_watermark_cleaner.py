from pathlib import Path

from universal_video_ai.render.watermark_cleaner import SourceWatermarkCleaner


def test_fractional_watermark_boxes_are_clamped_to_frame():
    boxes = SourceWatermarkCleaner._pixel_boxes(
        [(-0.2, -0.1, 0.25, 0.5), (0.8, 0.9, 1.4, 1.2)],
        width=1000,
        height=500,
    )

    assert boxes == [(0, 0, 250, 250), (800, 450, 1000, 500)]


def test_no_regions_returns_original_without_decoding(tmp_path: Path):
    source = tmp_path / "source.mp4"
    cleaner = SourceWatermarkCleaner()

    assert cleaner.clean(source, tmp_path / "cleaned.mp4", []) == source.resolve()


def test_orchestrator_inpaints_before_branding_and_does_not_silently_blur():
    script = Path("src/universal_video_ai/orchestrator/service.py").read_text(encoding="utf-8")

    assert "source_video_for_render" in script
    assert "SourceWatermarkCleaner" in script
    assert "preserving source without blur" in script
    assert "video_path=source_video_for_render" in script


def test_cleaner_masks_bright_logo_strokes_not_whole_ocr_rectangles():
    script = Path("src/universal_video_ai/render/watermark_cleaner.py").read_text(encoding="utf-8")

    assert "mask[top:bottom, left:right] = 255" not in script
    assert "bright_stroke = gray > 168" in script
    assert "min_colored_pixels" in script
    assert "post_blur=False" not in script  # caller controls this explicitly


def test_platform_download_cache_policy_rejects_old_watermarked_entries():
    service = Path("src/universal_video_ai/downloader/service.py").read_text(encoding="utf-8")
    downloader = Path("src/universal_video_ai/downloader/ytdlp_downloader.py").read_text(encoding="utf-8")

    assert 'download_policy_version") or 0) < 3' in service
    assert '"download_policy_version": 3' in service
    assert 'self.platform in {Platform.TIKTOK, Platform.DOUYIN}' in downloader
