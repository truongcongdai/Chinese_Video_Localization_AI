# src/universal_video_ai/render/text_detector.py
"""
On-screen text (hardcoded subtitle) detection.

Many reup/localization sources have the ORIGINAL language's subtitles burned
directly into the video pixels (e.g. Chinese subtitles baked into a short
drama). To fully localize such a video we must:

  1. find the pixel region where that text lives, per sentence/segment,
  2. cover it (so the original-language text is no longer visible), and
  3. draw the translated text in its place,

all synchronized to the same start/end timestamps as the sentence's audio.
This module is responsible for step 1: detecting the bounding box.

Optional dependency: `easyocr`. If it isn't installed, `OCR_AVAILABLE` is
False and `OnScreenTextDetector` raises a clear, actionable RuntimeError when
used — callers (renderer/orchestrator) should treat text-cover as a
best-effort feature and fall back to a manually configured static box, or
skip it, exactly like this codebase already does for optional providers
(Azure/Google TTS, DeepL, Demucs).
"""
from __future__ import annotations

from dataclasses import dataclass
import logging
import shutil
import subprocess
import tempfile
import warnings
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

# easyocr runs PyTorch on CPU in most deployments (no GPU in the container).
# PyTorch then emits two purely cosmetic warnings that have nothing to do
# with detection accuracy: 'pin_memory ... but no accelerator is found' and
# 'torch.quantize_per_tensor ... deprecated'. Both are internal
# tensor-memory/API-lifecycle notices, not indicators of a detection
# problem, so they're filtered here to keep logs readable without changing
# any behavior.
warnings.filterwarnings("ignore", message=".*pin_memory.*no accelerator.*")
warnings.filterwarnings("ignore", message=".*quantize_per_tensor.*deprecated.*")

__all__ = ["TextRegion", "OnScreenTextDetector", "OCR_AVAILABLE"]

_logger = logging.getLogger(__name__)


def _check_ocr_available() -> bool:
    try:
        import easyocr  # noqa: F401
        return True
    except Exception:
        return False


OCR_AVAILABLE = _check_ocr_available()


@dataclass(frozen=True)
class TextRegion:
    """A detected on-screen text bounding box, valid for a time window.

    Coordinates are pixel values in the source video's frame.
    """

    start: float
    end: float
    x: int
    y: int
    width: int
    height: int


class OnScreenTextDetector:
    """Detects burned-in on-screen text regions using OCR (easyocr).

    Usage:
        detector = OnScreenTextDetector(languages=["ch_sim", "en"])
        regions = detector.detect_regions_for_windows(video_path, [(0.0, 3.2), (3.2, 6.0)])
    """

    def __init__(
        self,
        languages: Sequence[str] = ("ch_sim", "en"),
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.languages = list(languages)
        self.logger = logger or _logger
        self._reader = None
        self.last_typical_line_height: Optional[int] = None

        if not OCR_AVAILABLE:
            self.logger.warning(
                "easyocr not installed; OnScreenTextDetector will raise if used. "
                "Install it with: pip install easyocr"
            )
        if shutil.which("ffmpeg") is None:
            self.logger.warning("ffmpeg not found in PATH; frame sampling for OCR will fail")

    def _get_reader(self):
        if self._reader is not None:
            return self._reader
        try:
            import easyocr  # type: ignore
            import torch
        except Exception as exc:
            raise RuntimeError(
                "OnScreenTextDetector requires the 'easyocr' package, which is not installed. "
                "Install it with: pip install easyocr"
            ) from exc
        
        # Auto-detect GPU availability
        use_gpu = torch.cuda.is_available()
        if use_gpu:
            self.logger.info("GPU detected, using CUDA for EasyOCR")
        else:
            self.logger.info("No GPU detected, using CPU for EasyOCR")
        
        self.logger.debug("Loading easyocr reader for languages=%s", self.languages)
        self._reader = easyocr.Reader(self.languages, gpu=use_gpu)
        return self._reader

    def _get_video_dimensions(self, video_path: Path) -> Optional[Tuple[int, int]]:
        """Return (width, height) of the video via ffprobe, or None if unavailable."""
        cmd = [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "csv=s=x:p=0", str(video_path),
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            if result.returncode != 0:
                return None
            w_str, h_str = result.stdout.strip().split("x")
            return int(w_str), int(h_str)
        except Exception as exc:
            self.logger.warning("Could not determine video dimensions: %s", exc)
            return None

    def _extract_frame(self, video_path: Path, at_seconds: float, out_path: Path) -> bool:
        """Extract a single frame at `at_seconds` into `out_path` (PNG). Returns success."""
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-ss", f"{max(0.0, at_seconds):.3f}",
            "-i", str(video_path),
            "-frames:v", "1",
            "-y", str(out_path),
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            return result.returncode == 0 and out_path.exists()
        except Exception as exc:
            self.logger.warning("Frame extraction failed at t=%.2f: %s", at_seconds, exc)
            return False

    def _detect_boxes_in_frame(self, frame_path: Path) -> List[Tuple[int, int, int, int]]:
        """Run OCR on one frame; return list of (x0, y0, x1, y1) axis-aligned boxes."""
        reader = self._get_reader()
        try:
            results = reader.readtext(str(frame_path))
        except Exception as exc:
            self.logger.warning("OCR failed on frame %s: %s", frame_path, exc)
            return []

        boxes: List[Tuple[int, int, int, int]] = []
        for detection in results:
            # easyocr returns (points, text, confidence); points = 4 (x, y) corners
            points, _text, confidence = detection
            if confidence < 0.35:
                continue
            xs = [p[0] for p in points]
            ys = [p[1] for p in points]
            boxes.append((int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys))))
        return boxes

    def detect_regions_for_windows(
        self,
        video_path: Path,
        windows: Sequence[Tuple[float, float]],
        samples_per_window: int = 2,
        padding_px: int = 6,
        band_tolerance: float = 1.6,
        max_single_box_width_ratio: float = 0.92,
        max_single_box_height_ratio: float = 0.3,
        max_lines_per_region: float = 2.4,
        exclude_regions_fractional: Sequence[Tuple[float, float, float, float]] = (),
        fill_undetected_windows: bool = True,
    ) -> List[TextRegion]:
        """
        For each (start, end) time window, sample a few frames, run OCR, and
        union the detected text boxes into a single covering region.

        Windows with no detected text are skipped (nothing to cover there).

        Burned-in subtitles don't always sit at the bottom of the frame —
        different sources place them at the bottom, mid-screen, or wherever
        fits the scene. So instead of assuming a fixed screen zone, this
        works in two passes over this specific video:

          Pass 1 (learn): OCR every sampled frame across every window and
            look at where the resulting text boxes actually cluster
            vertically. The densest cluster is treated as "the subtitle
            line" for this video — this could be near the bottom, the
            middle, wherever the source actually burns text in. This also
            gives us `typical_line_height`: the real height of a single
            line of this video's on-screen text (median of individual,
            not-yet-merged OCR boxes near that cluster), which is what a
            translated overlay's font size should match.

          Pass 2 (apply): for each window, keep only the OCR boxes that
            fall within that learned band (plus a tolerance, so a wrapped
            2-line caption isn't excluded) and union just those into the
            window's cover box. Stray text elsewhere on screen (character
            dialogue graphics, watermarks, UI text) no longer pollutes the
            box, and legitimate subtitle text isn't dropped just because it
            isn't in a hardcoded region.

        Implausibly large individual boxes (bigger than a real subtitle
        line could be) are dropped before either pass, and a window's final
        merged box height is capped at `max_lines_per_region` lines so an
        OCR merge mistake can't balloon a box for one window.

        After a successful call, `self.last_typical_line_height` holds the
        learned single-line text height in px (or None if nothing was
        detected), for callers that want to size the translated overlay's
        font to match the original text's size.

        :param video_path: source video file
        :param windows: list of (start_seconds, end_seconds) time windows,
            typically one per translated sentence, so the cover box only
            appears while that sentence's dialogue (and its original
            on-screen text) is on screen.
        :param samples_per_window: number of frames sampled per window
        :param padding_px: extra pixels added around the detected text box
        :param band_tolerance: how many `typical_line_height`s above/below
            the learned band center still count as "the subtitle line"
            (>1 so a 2-line caption is fully included).
        :param max_single_box_width_ratio: reject an individual OCR box
            wider than this fraction of the frame width (implausible for
            one text line; almost certainly a false positive).
        :param max_single_box_height_ratio: reject an individual OCR box
            taller than this fraction of the frame height.
        :param max_lines_per_region: cap a window's merged box height at
            this many `typical_line_height`s, so one bad OCR merge can't
            produce an oversized box for that sentence.
        :param exclude_regions_fractional: static screen areas to ignore
            entirely, as (x0, y0, x1, y1) fractions (0.0-1.0) of the frame.
            Use this for things that are NOT the burned-in subtitle but do
            contain on-screen text every frame — e.g. a TikTok/Douyin
            watermark (logo + @username + video title) in a corner — so
            they can't pollute the learned subtitle band or get
            mistakenly unioned into a sentence's cover box. Since the
            watermark is present in nearly every sampled frame (unlike the
            subtitle, which only appears during its own window), it would
            otherwise often win the band-clustering vote.
        :param fill_undetected_windows: when a window's own sampled frames
            produced NO in-band OCR box (e.g. the on-screen text was on
            screen too briefly, in motion, or just missed by OCR on the
            sampled frames) but this video's subtitle band was already
            learned from *other* windows, still emit a cover TextRegion for
            it — sized from the typical x-range/height of the windows that
            WERE detected — instead of silently skipping it. Skipping is
            what causes the translated overlay for that sentence to be
            drawn with nothing covering the original burned-in text
            underneath it. Defaults to True; set False to restore the old
            "skip windows with no OCR hits" behavior.
        :return: list of TextRegion (shorter than `windows` only if the
            video has no detectable subtitle band at all, or
            `fill_undetected_windows=False` and some windows had no text)
        """
        video_path = Path(video_path).resolve()
        dims = self._get_video_dimensions(video_path)
        frame_w, frame_h = dims if dims else (None, None)

        if dims is None:
            self.logger.warning(
                "Could not determine video dimensions; individual-box size "
                "limits are disabled for this run."
            )

        self.last_typical_line_height = None

        exclude_px: List[Tuple[int, int, int, int]] = []
        if exclude_regions_fractional and frame_w and frame_h:
            for (fx0, fy0, fx1, fy1) in exclude_regions_fractional:
                exclude_px.append((
                    int(fx0 * frame_w), int(fy0 * frame_h),
                    int(fx1 * frame_w), int(fy1 * frame_h),
                ))

        # ---- Pass 1: sample every window's frames once, keep raw per-window boxes ----
        per_window_boxes: List[Tuple[float, float, List[Tuple[int, int, int, int]]]] = []

        with tempfile.TemporaryDirectory(prefix="ocr_frames_") as tmp:
            tmp_dir = Path(tmp)
            for w_idx, (start, end) in enumerate(windows):
                if end <= start:
                    continue
                sample_count = max(1, samples_per_window)
                step = (end - start) / (sample_count + 1)
                sample_times = [start + step * (i + 1) for i in range(sample_count)]

                raw_boxes: List[Tuple[int, int, int, int]] = []
                for s_idx, t in enumerate(sample_times):
                    frame_path = tmp_dir / f"frame_{w_idx}_{s_idx}.png"
                    if not self._extract_frame(video_path, t, frame_path):
                        continue
                    raw_boxes.extend(self._detect_boxes_in_frame(frame_path))

                raw_boxes = self._drop_implausible_boxes(
                    raw_boxes, frame_w, frame_h,
                    max_single_box_width_ratio, max_single_box_height_ratio,
                )
                if exclude_px:
                    raw_boxes = self._drop_excluded_boxes(raw_boxes, exclude_px)
                per_window_boxes.append((start, end, raw_boxes))

        # ---- Learn where this video's subtitle line actually sits ----
        all_boxes = [b for (_s, _e, boxes) in per_window_boxes for b in boxes]
        band_center, typical_line_height = self._learn_subtitle_band(all_boxes)
        self.last_typical_line_height = typical_line_height

        regions: List[TextRegion] = []
        if band_center is None or typical_line_height is None:
            self.logger.info(
                "OnScreenTextDetector: no on-screen text detected across %d window(s)",
                len(windows),
            )
            return regions

        band_half = band_tolerance * typical_line_height
        max_region_height = int(round(max_lines_per_region * typical_line_height))

        # ---- Pass 2: keep only in-band boxes per window, union, cap height ----
        undetected_windows: List[Tuple[float, float]] = []
        # Track the x-extent actually seen in windows that DID have a hit,
        # so any undetected window can fall back to "the typical horizontal
        # extent of this video's subtitle line" rather than being skipped.
        detected_x0s: List[int] = []
        detected_x1s: List[int] = []
        detected_heights: List[int] = []

        for (start, end, boxes) in per_window_boxes:
            in_band = [
                b for b in boxes
                if abs(((b[1] + b[3]) / 2.0) - band_center) <= band_half
            ]
            if not in_band:
                undetected_windows.append((start, end))
                continue

            x0 = max(0, min(b[0] for b in in_band) - padding_px)
            y0 = max(0, min(b[1] for b in in_band) - padding_px)
            x1 = max(b[2] for b in in_band) + padding_px
            y1 = max(b[3] for b in in_band) + padding_px
            if frame_w is not None:
                x1 = min(x1, frame_w)
            if frame_h is not None:
                y1 = min(y1, frame_h)

            if (y1 - y0) > max_region_height:
                center_y = (y0 + y1) / 2.0
                y0 = int(round(center_y - max_region_height / 2.0))
                y1 = y0 + max_region_height
                if frame_h is not None:
                    y0 = max(0, min(y0, frame_h - max_region_height))
                    y1 = y0 + max_region_height

            detected_x0s.append(x0)
            detected_x1s.append(x1)
            detected_heights.append(y1 - y0)

            regions.append(
                TextRegion(start=start, end=end, x=x0, y=y0, width=int(x1 - x0), height=int(y1 - y0))
            )

        # ---- Fallback pass: cover windows OCR missed on their own sampled
        # frames, using the union x-range + median height of windows that
        # WERE detected. We deliberately use the union (widest observed
        # left edge to widest observed right edge) rather than an average,
        # so the fallback box errs toward over-covering the band instead of
        # leaving a sliver of original text peeking out — for a cover box,
        # too wide is a minor cosmetic issue, too narrow reproduces the bug
        # this fallback exists to fix.
        if fill_undetected_windows and undetected_windows and detected_x0s:
            fb_x0 = max(0, min(detected_x0s))
            fb_x1 = max(detected_x1s)
            if frame_w is not None:
                fb_x1 = min(fb_x1, frame_w)
            sorted_h = sorted(detected_heights)
            fb_height = sorted_h[len(sorted_h) // 2]

            fb_y0 = int(round(band_center - fb_height / 2.0))
            fb_y1 = fb_y0 + fb_height
            if frame_h is not None:
                fb_y0 = max(0, min(fb_y0, frame_h - fb_height))
                fb_y1 = fb_y0 + fb_height

            for (start, end) in undetected_windows:
                regions.append(
                    TextRegion(
                        start=start, end=end,
                        x=fb_x0, y=fb_y0,
                        width=int(fb_x1 - fb_x0), height=int(fb_y1 - fb_y0),
                    )
                )
            regions.sort(key=lambda r: r.start)

        self.logger.info(
            "OnScreenTextDetector: detected %d text region(s) across %d window(s) "
            "(subtitle band center=%.0fpx, typical line height=%dpx, "
            "%d window(s) had no direct OCR hit and were %s)",
            len(regions), len(windows), band_center, typical_line_height,
            len(undetected_windows),
            "filled via fallback" if (fill_undetected_windows and detected_x0s) else "skipped",
        )
        return regions

    def _drop_implausible_boxes(
        self,
        boxes: List[Tuple[int, int, int, int]],
        frame_w: Optional[int],
        frame_h: Optional[int],
        max_single_box_width_ratio: float,
        max_single_box_height_ratio: float,
    ) -> List[Tuple[int, int, int, int]]:
        """Drop individual OCR boxes too large to plausibly be one text line
        (almost always an OCR false positive / merge error), independent of
        where on screen they are."""
        if frame_w is None or frame_h is None:
            return boxes

        max_w = max_single_box_width_ratio * frame_w
        max_h = max_single_box_height_ratio * frame_h
        return [
            b for b in boxes
            if (b[2] - b[0]) <= max_w and (b[3] - b[1]) <= max_h
        ]

    def _drop_excluded_boxes(
        self,
        boxes: List[Tuple[int, int, int, int]],
        exclude_px: List[Tuple[int, int, int, int]],
    ) -> List[Tuple[int, int, int, int]]:
        """Drop any box whose center falls inside a static excluded region
        (e.g. a platform watermark corner)."""
        kept = []
        for b in boxes:
            cx = (b[0] + b[2]) / 2.0
            cy = (b[1] + b[3]) / 2.0
            if any(ex0 <= cx <= ex1 and ey0 <= cy <= ey1 for (ex0, ey0, ex1, ey1) in exclude_px):
                continue
            kept.append(b)
        return kept

    def _learn_subtitle_band(
        self,
        boxes: List[Tuple[int, int, int, int]],
    ) -> Tuple[Optional[float], Optional[int]]:
        """
        Find the vertical band where this video's burned-in subtitle text
        actually sits, from the full set of individual OCR boxes gathered
        across every sampled frame/window.

        Approach: bucket every box's vertical center into coarse bins, find
        the densest run of bins (the subtitle line recurs in roughly the
        same place far more often than any stray on-screen text), and
        report that bin's weighted-average y-center plus the median height
        of the boxes that fall in it (the "typical single line height").

        :return: (band_center_y, typical_line_height) in px, or (None, None)
            if no boxes were given.
        """
        if not boxes:
            return None, None

        heights = [b[3] - b[1] for b in boxes]
        centers = [(b[1] + b[3]) / 2.0 for b in boxes]

        # Bin height: coarse enough to group a subtitle's slightly-varying
        # per-frame detections together, fine enough to separate genuinely
        # different areas of the screen (e.g. bottom captions vs. a
        # mid-screen on-screen graphic).
        median_h = sorted(heights)[len(heights) // 2]
        bin_size = max(10.0, median_h * 0.8)

        bins: dict = {}
        for c, h in zip(centers, heights):
            key = int(c // bin_size)
            bucket = bins.setdefault(key, {"count": 0, "centers": [], "heights": []})
            bucket["count"] += 1
            bucket["centers"].append(c)
            bucket["heights"].append(h)

        # Merge each bin with its immediate neighbors when scoring, so a
        # cluster that straddles a bin boundary isn't undercounted.
        def neighborhood_count(key: int) -> int:
            return sum(bins.get(k, {"count": 0})["count"] for k in (key - 1, key, key + 1))

        best_key = max(bins.keys(), key=neighborhood_count)

        combined_centers: List[float] = []
        combined_heights: List[int] = []
        for k in (best_key - 1, best_key, best_key + 1):
            if k in bins:
                combined_centers.extend(bins[k]["centers"])
                combined_heights.extend(bins[k]["heights"])

        band_center = sum(combined_centers) / len(combined_centers)
        sorted_heights = sorted(combined_heights)
        typical_line_height = sorted_heights[len(sorted_heights) // 2]
        typical_line_height = max(int(typical_line_height), 10)

        return band_center, typical_line_height

