from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Callable, Iterable, Tuple


class WatermarkInpaintError(RuntimeError):
    """Raised when source-watermark inpainting cannot produce a valid video."""


class SourceWatermarkCleaner:
    """Remove detected source-watermark regions before output branding.

    Frames are inpainted with OpenCV rather than blurred.  The cleaned frames
    are streamed directly to FFmpeg and encoded at a visually lossless CRF so
    no temporary uncompressed video is written to disk.  Audio is intentionally
    omitted: the localization renderer supplies its final mixed audio later.
    """

    def __init__(self, logger: logging.Logger | None = None) -> None:
        self.logger = logger or logging.getLogger(__name__)

    @staticmethod
    def _pixel_boxes(
        boxes: Iterable[Tuple[float, float, float, float]], width: int, height: int
    ) -> list[Tuple[int, int, int, int]]:
        result: list[Tuple[int, int, int, int]] = []
        for x0, y0, x1, y1 in boxes:
            left = max(0, min(width - 1, round(float(x0) * width)))
            top = max(0, min(height - 1, round(float(y0) * height)))
            right = max(left + 1, min(width, round(float(x1) * width)))
            bottom = max(top + 1, min(height, round(float(y1) * height)))
            result.append((left, top, right, bottom))
        return result

    def clean(
        self,
        video_path: Path,
        output_path: Path,
        boxes_fractional: Iterable[Tuple[float, float, float, float]],
        *,
        radius: float = 12.0,
        crf: int = 18,
        inpaint_passes: int = 1,
        post_blur: bool = True,
        blur_kernel_size: int = 5,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> Path:
        try:
            import cv2
            import numpy as np
        except ImportError as exc:  # pragma: no cover - installation dependent
            raise WatermarkInpaintError("OpenCV is required for watermark inpainting") from exc

        source = Path(video_path).resolve()
        destination = Path(output_path).resolve()
        boxes = tuple(boxes_fractional)
        if not boxes:
            return source

        capture = cv2.VideoCapture(str(source))
        if not capture.isOpened():
            raise WatermarkInpaintError(f"Cannot open source video: {source}")

        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
        total_frames = max(0, int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0))
        if width <= 0 or height <= 0 or fps <= 0:
            capture.release()
            raise WatermarkInpaintError("Source video has invalid dimensions or frame rate")

        pixel_boxes = self._pixel_boxes(boxes, width, height)

        destination.parent.mkdir(parents=True, exist_ok=True)
        command = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "rawvideo", "-pix_fmt", "bgr24",
            "-s", f"{width}x{height}", "-r", f"{fps:.6f}", "-i", "-",
            "-an", "-c:v", "libx264", "-preset", "fast", "-crf", str(int(crf)),
            "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(destination),
        ]
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
        frames = 0
        try:
            assert process.stdin is not None
            while True:
                ok, frame = capture.read()
                if not ok:
                    break
                cleaned = frame.copy()
                # OCR returns rectangles, but filling each entire rectangle is
                # what produced the obvious smeared blocks. Build a fresh mask
                # from high-contrast/bright logo strokes inside those ROIs and
                # inpaint only the glyph pixels. Scene content remains sharp.
                mask = np.zeros((height, width), dtype=np.uint8)
                for left, top, right, bottom in pixel_boxes:
                    roi = frame[top:bottom, left:right]
                    if roi.size == 0:
                        continue
                    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
                    smooth = cv2.GaussianBlur(gray, (0, 0), 5.0)
                    contrast = cv2.absdiff(gray, smooth)
                    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
                    # Tight OCR-derived regions let us keep the complete
                    # white glyph body; requiring a strong local contrast
                    # left the flat interior of large Chinese characters.
                    bright_stroke = gray > 168
                    colored_logo = (hsv[:, :, 1] > 85) & (hsv[:, :, 2] > 125) & (contrast > 12)
                    area_ratio = ((right - left) * (bottom - top)) / max(1.0, float(width * height))
                    touches_corner = (
                        (left <= width * 0.03 or right >= width * 0.97)
                        and (top <= height * 0.03 or bottom >= height * 0.97)
                    )
                    if area_ratio >= 0.07 and touches_corner:
                        # These are candidate zones for Douyin's watermark,
                        # which jumps from upper-left to lower-right. Only the
                        # active corner contains the cyan/red musical-note
                        # pixels. Gating here prevents any default corner blur.
                        min_colored_pixels = max(10, int(roi.shape[0] * roi.shape[1] * 0.00012))
                        if int(colored_logo.sum()) < min_colored_pixels:
                            continue
                    # Do not include generic dark edges: ceiling grids,
                    # window frames and character outlines are dark and were
                    # the direct cause of the large smeared blocks. Platform
                    # logos are bright/white or saturated; dilation below also
                    # captures their thin dark antialias/shadow boundary.
                    roi_mask = ((bright_stroke | colored_logo).astype(np.uint8) * 255)
                    roi_mask = cv2.morphologyEx(
                        roi_mask, cv2.MORPH_CLOSE, np.ones((3, 3), dtype=np.uint8)
                    )
                    roi_mask = cv2.dilate(roi_mask, np.ones((3, 3), dtype=np.uint8), iterations=2)
                    mask[top:bottom, left:right] = cv2.max(mask[top:bottom, left:right], roi_mask)
                # Multi-pass inpainting for better removal
                for pass_num in range(inpaint_passes):
                    # Alternate between TELEA and NS algorithms for different inpainting characteristics
                    if pass_num % 2 == 0:
                        cleaned = cv2.inpaint(cleaned, mask, float(radius), cv2.INPAINT_TELEA)
                    else:
                        cleaned = cv2.inpaint(cleaned, mask, float(radius * 0.8), cv2.INPAINT_NS)
                
                # Post-processing blur to smooth residual traces
                if post_blur:
                    # Apply selective blur only to masked regions
                    blurred = cv2.GaussianBlur(cleaned, (blur_kernel_size, blur_kernel_size), 0)
                    # Blend only in the masked regions
                    mask_float = mask.astype(float) / 255.0
                    if len(mask_float.shape) == 2:
                        mask_float = mask_float[:, :, np.newaxis]
                    cleaned = (cleaned * (1 - mask_float) + blurred * mask_float).astype(np.uint8)
                
                process.stdin.write(cleaned.tobytes())
                frames += 1
                # Inpainting is CPU-heavy and can take several times the video
                # duration. Report coarse progress so callers can keep the UI
                # visibly alive without doing a database write for every frame.
                if progress_callback and (
                    frames == 1 or frames % max(1, round(fps)) == 0 or frames == total_frames
                ):
                    progress_callback(frames, total_frames)
            process.stdin.close()
            stderr = process.stderr.read() if process.stderr is not None else b""
            return_code = process.wait()
        except Exception:
            process.kill()
            process.wait()
            raise
        finally:
            capture.release()

        if return_code != 0 or frames == 0 or not destination.exists() or destination.stat().st_size == 0:
            destination.unlink(missing_ok=True)
            detail = stderr.decode("utf-8", errors="replace").strip()
            raise WatermarkInpaintError(f"FFmpeg watermark-inpaint encode failed: {detail}")

        self.logger.info(
            "SourceWatermarkCleaner: inpainted %d region(s) across %d frame(s): %s",
            len(boxes), frames, destination,
        )
        return destination
