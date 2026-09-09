"""Offline smoke check of real OCR geometry on generated video frames."""
from __future__ import annotations

import logging
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from universal_video_ai.render.text_detector import OnScreenTextDetector

__all__ = ["main"]
_logger = logging.getLogger(__name__)


def main() -> None:
    import easyocr

    # Never download models or use providers during this diagnostic.
    reader = easyocr.Reader(["ch_sim", "en"], gpu=False, download_enabled=False)
    detector = OnScreenTextDetector(device="cpu")
    detector._reader = reader
    font_paths = [
        Path("C:/Windows/Fonts/msyh.ttc"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ]
    font_path = next((path for path in font_paths if path.exists()), None)
    if font_path is None:
        raise RuntimeError("Install a TrueType font before running this smoke check.")
    font = ImageFont.truetype(str(font_path), 42)
    with tempfile.TemporaryDirectory(prefix="subtitle-smoke-") as directory:
        root = Path(directory)
        expected = []
        for index, (top, colour) in enumerate([(1050, "white"), (480, "yellow"), (0, None)]):
            frame = Image.new("RGB", (720, 1280), (25, 25, 30))
            if colour:
                draw = ImageDraw.Draw(frame)
                caption = "Source subtitle 123"
                box = draw.textbbox((140, top), caption, font=font, stroke_width=2)
                expected.append(box)
                draw.text((140, top), caption, font=font, fill=colour,
                          stroke_width=2, stroke_fill="black")
            frame.save(root / f"frame_{index}.png")
        video = root / "source.mp4"
        subprocess.run([
            "ffmpeg", "-v", "error", "-framerate", "1", "-i", str(root / "frame_%d.png"),
            "-c:v", "libx264", "-r", "10", "-pix_fmt", "yuv420p", str(video),
        ], check=True, timeout=30)
        regions = detector.detect_regions_for_windows(
            video, [(0, 1), (1, 2), (2, 3)], samples_per_window=1,
            fill_undetected_windows=False,
        )
        assert len(regions) == 2, regions
        for region, box in zip(regions, expected):
            assert region.x <= box[0] and region.y <= box[1], (region, box)
            assert region.x + region.width >= box[2], (region, box)
            assert region.y + region.height >= box[3], (region, box)
        _logger.info("PASS: white bottom caption, yellow middle caption, blank frame; regions=%s", regions)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
