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
from difflib import SequenceMatcher
import logging
import shutil
import subprocess
import tempfile
import warnings
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

# easyocr runs PyTorch on CPU in most deployments (no GPU in the container).
# PyTorch then emits two purely cosmetic warnings that have nothing to do
# with detection accuracy: 'pin_memory ... but no accelerator is found' and
# 'torch.quantize_per_tensor ... deprecated'. Both are internal
# tensor-memory/API-lifecycle notices, not indicators of a detection
# problem, so they're filtered here to keep logs readable without changing
# any behavior.
warnings.filterwarnings("ignore", message=".*pin_memory.*no accelerator.*")
warnings.filterwarnings("ignore", message=".*quantize_per_tensor.*deprecated.*")

__all__ = ["TextRegion", "SubtitleOffsetEstimate", "OnScreenTextDetector", "OCR_AVAILABLE"]

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


@dataclass(frozen=True)
class SubtitleOffsetEstimate:
    """Estimated visual subtitle timing offset relative to ASR/audio timing."""

    offset: float
    confidence: float
    matches: int
    apply_after: Optional[float] = None


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
        except Exception as exc:
            raise RuntimeError(
                "OnScreenTextDetector requires the 'easyocr' package, which is not installed. "
                "Install it with: pip install easyocr"
            ) from exc
        self.logger.debug("Loading easyocr reader for languages=%s", self.languages)
        self._reader = easyocr.Reader(self.languages, gpu=False)
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

    def _read_text_in_frame(
        self,
        frame_path: Path,
        frame_w: Optional[int],
        frame_h: Optional[int],
        subtitle_candidate_region_fractional: Optional[Tuple[float, float, float, float]],
        exclude_px: Sequence[Tuple[int, int, int, int]] = (),
    ) -> str:
        """Run OCR on one frame and return plausible subtitle text only."""
        frame_for_ocr = frame_path
        origin_x = 0
        origin_y = 0
        cropped_to_candidate = False
        if subtitle_candidate_region_fractional and frame_w and frame_h:
            try:
                from PIL import Image

                fx0, fy0, fx1, fy1 = subtitle_candidate_region_fractional
                x0 = max(0, int(fx0 * frame_w))
                y0 = max(0, int(fy0 * frame_h))
                x1 = min(frame_w, int(fx1 * frame_w))
                y1 = min(frame_h, int(fy1 * frame_h))
                if x1 > x0 and y1 > y0:
                    cropped = frame_path.with_name(f"{frame_path.stem}_subtitle_crop.png")
                    with Image.open(frame_path) as image:
                        image.crop((x0, y0, x1, y1)).save(cropped)
                    frame_for_ocr = cropped
                    origin_x = x0
                    origin_y = y0
                    cropped_to_candidate = True
            except Exception:
                frame_for_ocr = frame_path
                origin_x = 0
                origin_y = 0
                cropped_to_candidate = False

        reader = self._get_reader()
        try:
            results = reader.readtext(str(frame_for_ocr))
        except Exception as exc:
            self.logger.warning("OCR failed on frame %s: %s", frame_for_ocr, exc)
            return ""

        entries: List[Tuple[int, int, str]] = []
        for detection in results:
            points, text, confidence = detection
            if confidence < 0.25 or not str(text).strip():
                continue
            xs = [p[0] for p in points]
            ys = [p[1] for p in points]
            box = (
                int(min(xs)) + origin_x,
                int(min(ys)) + origin_y,
                int(max(xs)) + origin_x,
                int(max(ys)) + origin_y,
            )
            cx = (box[0] + box[2]) / 2.0
            cy = (box[1] + box[3]) / 2.0
            if exclude_px and any(ex0 <= cx <= ex1 and ey0 <= cy <= ey1 for (ex0, ey0, ex1, ey1) in exclude_px):
                continue
            if subtitle_candidate_region_fractional and frame_w and frame_h and not cropped_to_candidate:
                fx0, fy0, fx1, fy1 = subtitle_candidate_region_fractional
                if not (fx0 * frame_w <= cx <= fx1 * frame_w and fy0 * frame_h <= cy <= fy1 * frame_h):
                    continue
            entries.append((box[1], box[0], str(text).strip()))

        entries.sort()
        return " ".join(text for (_y, _x, text) in entries)

    def read_subtitle_text_at(
        self,
        video_path: Path,
        at_seconds: float,
        subtitle_candidate_region_fractional: Optional[Tuple[float, float, float, float]] = (
            0.08, 0.25, 0.92, 0.95,
        ),
        exclude_regions_fractional: Sequence[Tuple[float, float, float, float]] = (),
    ) -> str:
        """Return OCR text from the likely subtitle area at one timestamp."""
        video_path = Path(video_path).resolve()
        dims = self._get_video_dimensions(video_path)
        frame_w, frame_h = dims if dims else (None, None)
        exclude_px: List[Tuple[int, int, int, int]] = []
        if exclude_regions_fractional and frame_w and frame_h:
            for (fx0, fy0, fx1, fy1) in exclude_regions_fractional:
                exclude_px.append((
                    int(fx0 * frame_w), int(fy0 * frame_h),
                    int(fx1 * frame_w), int(fy1 * frame_h),
                ))

        with tempfile.TemporaryDirectory(prefix="ocr_text_") as tmp:
            frame_path = Path(tmp) / "frame.png"
            if not self._extract_frame(video_path, at_seconds, frame_path):
                return ""
            return self._read_text_in_frame(
                frame_path,
                frame_w,
                frame_h,
                subtitle_candidate_region_fractional,
                exclude_px,
            )

    def estimate_subtitle_time_offset(
        self,
        video_path: Path,
        source_segments: Sequence[Tuple[float, float, str]],
        search_radius: float = 12.0,
        coarse_step: float = 1.5,
        refine_step: float = 0.5,
        max_anchors: int = 2,
        min_score: float = 0.72,
        min_matches: int = 2,
        subtitle_candidate_region_fractional: Optional[Tuple[float, float, float, float]] = (
            0.08, 0.25, 0.92, 0.95,
        ),
        exclude_regions_fractional: Sequence[Tuple[float, float, float, float]] = (),
        use_text_ocr_fallback: bool = False,
        min_offset: float = 0.05,
        presence_search_radius: float = 1.0,
    ) -> Optional[SubtitleOffsetEstimate]:
        """Estimate hard-sub visual offset from ASR timestamps.

        Some short-video sources have audio/transcript timing that does not
        match the burned-in subtitle layer. This samples a few distinctive
        source-language segments, finds where their text actually appears on
        screen, and returns the median visual offset. It is best-effort: if
        OCR cannot find enough confident matches, callers should keep the
        original ASR timing.
        """
        visual_estimate = self._estimate_subtitle_time_offset_by_presence(
            video_path,
            source_segments,
            search_radius=min(search_radius, presence_search_radius),
            min_offset=min_offset,
        )
        if visual_estimate is not None:
            return visual_estimate
        if not use_text_ocr_fallback:
            return None

        video_path = Path(video_path).resolve()
        dims = self._get_video_dimensions(video_path)
        frame_w, frame_h = dims if dims else (None, None)
        exclude_px: List[Tuple[int, int, int, int]] = []
        if exclude_regions_fractional and frame_w and frame_h:
            for (fx0, fy0, fx1, fy1) in exclude_regions_fractional:
                exclude_px.append((
                    int(fx0 * frame_w), int(fy0 * frame_h),
                    int(fx1 * frame_w), int(fy1 * frame_h),
                ))

        anchors = self._select_offset_anchors(source_segments, max_anchors=max_anchors)
        if not anchors:
            return None

        offsets: List[float] = []
        scores: List[float] = []
        text_cache: Dict[float, str] = {}

        def text_at(t: float, tmp_dir: Path) -> str:
            key = round(max(0.0, t), 2)
            if key in text_cache:
                return text_cache[key]
            frame_path = tmp_dir / f"frame_{len(text_cache)}.png"
            if not self._extract_frame(video_path, key, frame_path):
                text_cache[key] = ""
                return ""
            text_cache[key] = self._read_text_in_frame(
                frame_path,
                frame_w,
                frame_h,
                subtitle_candidate_region_fractional,
                exclude_px,
            )
            return text_cache[key]

        coarse_offsets = self._offset_candidates(search_radius, coarse_step)
        with tempfile.TemporaryDirectory(prefix="ocr_offset_") as tmp:
            tmp_dir = Path(tmp)
            for start, end, source_text in anchors:
                midpoint = (start + end) / 2.0
                source_norm = _normalize_ocr_match_text(source_text)
                best_offset = 0.0
                best_score = 0.0

                for offset in coarse_offsets:
                    score = _subtitle_text_match_score(source_norm, text_at(midpoint + offset, tmp_dir))
                    if score > best_score:
                        best_score = score
                        best_offset = offset

                refined_offsets = [
                    best_offset + (i * refine_step)
                    for i in range(int(round(-1.0 / refine_step)), int(round(1.0 / refine_step)) + 1)
                ]
                for offset in refined_offsets:
                    if abs(offset) > search_radius:
                        continue
                    score = _subtitle_text_match_score(source_norm, text_at(midpoint + offset, tmp_dir))
                    if score > best_score:
                        best_score = score
                        best_offset = offset

                if best_score >= min_score:
                    offsets.append(best_offset)
                    scores.append(best_score)

        if len(offsets) < min_matches:
            self.logger.info(
                "OnScreenTextDetector: subtitle offset estimate skipped; only %d/%d anchor(s) matched",
                len(offsets), len(anchors),
            )
            return None

        sorted_offsets = sorted(offsets)
        median_offset = sorted_offsets[len(sorted_offsets) // 2]
        confidence = sum(scores) / len(scores)
        estimate = SubtitleOffsetEstimate(
            offset=round(median_offset, 3),
            confidence=round(confidence, 3),
            matches=len(offsets),
        )
        self.logger.info(
            "OnScreenTextDetector: estimated hard-sub offset %.2fs from %d anchor(s), confidence=%.2f",
            estimate.offset, estimate.matches, estimate.confidence,
        )
        return estimate

    def _estimate_subtitle_time_offset_by_presence(
        self,
        video_path: Path,
        source_segments: Sequence[Tuple[float, float, str]],
        search_radius: float = 12.0,
        step: float = 0.5,
        refine_step: float = 0.05,
        max_segments: int = 8,
        min_visual_score: float = 0.003,
        min_best_to_zero_delta: float = 0.004,
        min_visible_matches: int = 2,
        min_offset: float = 0.05,
    ) -> Optional[SubtitleOffsetEstimate]:
        """Fast visual hard-sub offset estimate without OCR text recognition."""
        anchors = self._select_presence_offset_anchors(source_segments, max_segments=max_segments)
        if not anchors:
            return None

        offsets = self._offset_candidates(search_radius, step)
        scores_by_offset: Dict[float, Tuple[float, int]] = {}
        frame_cache: Dict[float, float] = {}

        def score_at(t: float, tmp_dir: Path) -> float:
            key = round(max(0.0, t), 2)
            if key in frame_cache:
                return frame_cache[key]
            frame_path = tmp_dir / f"presence_{len(frame_cache)}.jpg"
            if not self._extract_frame(video_path, key, frame_path):
                frame_cache[key] = 0.0
                return 0.0
            frame_cache[key] = self._subtitle_presence_score(frame_path)
            return frame_cache[key]

        with tempfile.TemporaryDirectory(prefix="subtitle_presence_") as tmp:
            tmp_dir = Path(tmp)
            for offset in offsets:
                scores: List[float] = []
                visible = 0
                for start, end, _text in anchors:
                    score = score_at(((start + end) / 2.0) + offset, tmp_dir)
                    scores.append(score)
                    if score >= min_visual_score:
                        visible += 1
                avg_score = sum(scores) / len(scores) if scores else 0.0
                scores_by_offset[offset] = (avg_score, visible)

            coarse_best = max(scores_by_offset.items(), key=lambda item: item[1][0])[0]
            refine_count = int(round(1.5 / refine_step))
            for idx in range(-refine_count, refine_count + 1):
                offset = round(coarse_best + (idx * refine_step), 3)
                if abs(offset) > search_radius or offset in scores_by_offset:
                    continue
                scores = []
                visible = 0
                for start, end, _text in anchors:
                    score = score_at(((start + end) / 2.0) + offset, tmp_dir)
                    scores.append(score)
                    if score >= min_visual_score:
                        visible += 1
                avg_score = sum(scores) / len(scores) if scores else 0.0
                scores_by_offset[offset] = (avg_score, visible)

        zero_score, _zero_visible = scores_by_offset.get(0.0, (0.0, 0))
        best_offset, (best_score, best_visible) = max(
            scores_by_offset.items(),
            key=lambda item: item[1][0],
        )
        if abs(best_offset) < min_offset:
            return None
        if best_visible < min_visible_matches or best_score < min_visual_score:
            return None
        if (best_score - zero_score) < min_best_to_zero_delta:
            return None

        confidence = min(1.0, max(0.0, (best_score - zero_score) / 0.02))
        estimate = SubtitleOffsetEstimate(
            offset=round(best_offset, 3),
            confidence=round(confidence, 3),
            matches=best_visible,
            apply_after=None,
        )
        self.logger.info(
            "OnScreenTextDetector: estimated hard-sub visual offset %.2fs "
            "(presence %.4f vs zero %.4f, visible=%d/%d)",
            estimate.offset, best_score, zero_score, best_visible, len(anchors),
        )
        return estimate

    def _select_presence_offset_anchors(
        self,
        source_segments: Sequence[Tuple[float, float, str]],
        max_segments: int,
        min_gap: float = 4.0,
    ) -> List[Tuple[float, float, str]]:
        """Choose a compact subtitle cluster after a transcript gap.

        Presence-only matching cannot identify *which* subtitle is on screen;
        it only knows that a hard-sub line is visible. Anchoring on one compact
        cluster after a large gap avoids matching arbitrary later subtitles in
        dense dialogue sections.
        """
        cleaned = [
            (start, end, text)
            for start, end, text in source_segments
            if end > start and len(_normalize_ocr_match_text(text)) >= 2
        ]
        if not cleaned:
            return []

        best_index = 0
        best_gap = 0.0
        previous_end = cleaned[0][1]
        for index, (start, end, _text) in enumerate(cleaned[1:], start=1):
            gap = start - previous_end
            if gap > best_gap:
                best_gap = gap
                best_index = index
            previous_end = max(previous_end, end)

        if best_gap < min_gap:
            return self._select_offset_anchors(cleaned, max_anchors=max_segments)

        cluster: List[Tuple[float, float, str]] = []
        previous_end = cleaned[best_index][0]
        for start, end, text in cleaned[best_index:]:
            if cluster and (start - previous_end) > min_gap:
                break
            cluster.append((start, end, text))
            previous_end = max(previous_end, end)
            if len(cluster) >= max_segments:
                break
        return cluster

    def _subtitle_presence_score(
        self,
        frame_path: Path,
        region_fractional: Tuple[float, float, float, float] = (0.10, 0.55, 0.90, 0.92),
    ) -> float:
        """Measure likely white hard-sub text density in the subtitle band."""
        try:
            from PIL import Image

            with Image.open(frame_path) as image:
                rgb = image.convert("RGB")
                w, h = rgb.size
                fx0, fy0, fx1, fy1 = region_fractional
                crop = rgb.crop((
                    int(fx0 * w), int(fy0 * h),
                    int(fx1 * w), int(fy1 * h),
                ))
                data = (
                    crop.get_flattened_data()
                    if hasattr(crop, "get_flattened_data")
                    else crop.getdata()
                )
                bright = 0
                total = 0
                for r, g, b in data:
                    total += 1
                    if r > 185 and g > 185 and b > 185 and (max(r, g, b) - min(r, g, b)) < 80:
                        bright += 1
                return bright / total if total else 0.0
        except Exception:
            return 0.0

    def _select_offset_anchors(
        self,
        source_segments: Sequence[Tuple[float, float, str]],
        max_anchors: int,
    ) -> List[Tuple[float, float, str]]:
        candidates: List[Tuple[int, float, float, str]] = []
        seen: set[str] = set()
        for start, end, text in source_segments:
            norm = _normalize_ocr_match_text(text)
            if len(norm) < 5 or norm in seen:
                continue
            seen.add(norm)
            if end <= start:
                continue
            # Prefer distinctive, longer subtitle lines but avoid spending
            # all anchors on the same opening scene.
            score = len(norm) + int(start >= 8.0) * 4
            candidates.append((score, start, end, text))
        candidates.sort(key=lambda item: item[0], reverse=True)
        return [(start, end, text) for (_score, start, end, text) in candidates[:max_anchors]]

    @staticmethod
    def _offset_candidates(search_radius: float, step: float) -> List[float]:
        count = int(round(search_radius / step))
        offsets = [0.0]
        for idx in range(1, count + 1):
            value = round(idx * step, 3)
            offsets.extend([value, -value])
        return offsets

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
        subtitle_candidate_region_fractional: Optional[Tuple[float, float, float, float]] = (
            0.08, 0.25, 0.92, 0.95,
        ),
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
            A top-right exclusion is recommended for Chinese advertising
            banners, which are frequently present in every frame and can
            otherwise be mistaken for the subtitle band.
        :param subtitle_candidate_region_fractional: region used for
            learning the subtitle band, as (x0,y0,x1,y1) fractions of the
            frame. OCR text whose center is in the extreme corners/top UI
            is ignored before band clustering, because persistent labels,
            watermarks, and counters can otherwise out-vote the real
            subtitle. If this filter would remove every box, detection
            falls back to the unfiltered boxes for compatibility.
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
        fallback_per_window_boxes: List[Tuple[float, float, List[Tuple[int, int, int, int]]]] = []
        candidate_filter_active = bool(subtitle_candidate_region_fractional and frame_w and frame_h)

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
                fallback_per_window_boxes.append((start, end, list(raw_boxes)))
                if candidate_filter_active and subtitle_candidate_region_fractional and frame_w and frame_h:
                    candidate_boxes = self._keep_candidate_subtitle_boxes(
                        raw_boxes, frame_w, frame_h, subtitle_candidate_region_fractional
                    )
                    raw_boxes = candidate_boxes
                per_window_boxes.append((start, end, raw_boxes))

        # ---- Learn where this video's subtitle line actually sits ----
        all_boxes = [b for (_s, _e, boxes) in per_window_boxes for b in boxes]
        if not all_boxes and candidate_filter_active:
            self.logger.info(
                "OnScreenTextDetector: subtitle candidate region removed every OCR box; "
                "falling back to unfiltered OCR boxes for this video"
            )
            per_window_boxes = fallback_per_window_boxes
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

    def detect_persistent_text_regions(
        self,
        video_path: Path,
        duration: float,
        sample_count: int = 6,
        min_seen_ratio: float = 0.45,
        ignore_region_fractional: Tuple[float, float, float, float] = (0.08, 0.55, 0.92, 0.96),
        padding_fractional: float = 0.012,
    ) -> List[Tuple[float, float, float, float]]:
        """Detect static non-subtitle text/watermarks that persist across a video.

        This targets channel titles/logos and faint creator watermarks. It
        samples frames across the whole video, clusters OCR boxes by position,
        and returns only clusters seen in several sampled frames. The normal
        subtitle band is ignored so dialogue captions are not mistaken for a
        persistent watermark.
        """
        video_path = Path(video_path).resolve()
        dims = self._get_video_dimensions(video_path)
        if dims is None or duration <= 0:
            return []
        frame_w, frame_h = dims
        sample_count = max(3, sample_count)
        min_seen = max(2, int(round(sample_count * min_seen_ratio)))
        times = [duration * (idx + 1) / (sample_count + 1) for idx in range(sample_count)]

        def outside_ignored_region(box: Tuple[int, int, int, int]) -> bool:
            fx0, fy0, fx1, fy1 = ignore_region_fractional
            cx = (box[0] + box[2]) / 2.0 / frame_w
            cy = (box[1] + box[3]) / 2.0 / frame_h
            return not (fx0 <= cx <= fx1 and fy0 <= cy <= fy1)

        clusters: List[Dict[str, object]] = []

        def matches_cluster(box: Tuple[int, int, int, int], cluster: Dict[str, object]) -> bool:
            cx = (box[0] + box[2]) / 2.0
            cy = (box[1] + box[3]) / 2.0
            ccx, ccy = cluster["center"]  # type: ignore[misc]
            return abs(cx - ccx) <= frame_w * 0.08 and abs(cy - ccy) <= frame_h * 0.08

        with tempfile.TemporaryDirectory(prefix="persistent_text_") as tmp:
            tmp_dir = Path(tmp)
            for sample_index, t in enumerate(times):
                frame_path = tmp_dir / f"persistent_{sample_index}.png"
                if not self._extract_frame(video_path, t, frame_path):
                    continue
                boxes = self._detect_boxes_in_frame(frame_path)
                boxes = self._drop_implausible_boxes(
                    boxes,
                    frame_w,
                    frame_h,
                    max_single_box_width_ratio=0.55,
                    max_single_box_height_ratio=0.18,
                )
                boxes = [box for box in boxes if outside_ignored_region(box)]
                for box in boxes:
                    matched = None
                    for cluster in clusters:
                        if matches_cluster(box, cluster):
                            matched = cluster
                            break
                    if matched is None:
                        cx = (box[0] + box[2]) / 2.0
                        cy = (box[1] + box[3]) / 2.0
                        clusters.append({
                            "box": box,
                            "center": (cx, cy),
                            "seen": {sample_index},
                        })
                        continue
                    x0, y0, x1, y1 = matched["box"]  # type: ignore[misc]
                    merged = (
                        min(x0, box[0]),
                        min(y0, box[1]),
                        max(x1, box[2]),
                        max(y1, box[3]),
                    )
                    matched["box"] = merged
                    matched["center"] = ((merged[0] + merged[2]) / 2.0, (merged[1] + merged[3]) / 2.0)
                    matched["seen"].add(sample_index)  # type: ignore[union-attr]

        pad_x = frame_w * padding_fractional
        pad_y = frame_h * padding_fractional
        regions: List[Tuple[float, float, float, float]] = []
        for cluster in clusters:
            seen = cluster["seen"]  # type: ignore[assignment]
            if len(seen) < min_seen:
                continue
            x0, y0, x1, y1 = cluster["box"]  # type: ignore[misc]
            regions.append((
                max(0.0, (x0 - pad_x) / frame_w),
                max(0.0, (y0 - pad_y) / frame_h),
                min(1.0, (x1 + pad_x) / frame_w),
                min(1.0, (y1 + pad_y) / frame_h),
            ))

        self.logger.info("OnScreenTextDetector: detected %d persistent text/watermark region(s)", len(regions))
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

    def _keep_candidate_subtitle_boxes(
        self,
        boxes: List[Tuple[int, int, int, int]],
        frame_w: int,
        frame_h: int,
        region_fractional: Tuple[float, float, float, float],
    ) -> List[Tuple[int, int, int, int]]:
        """Keep boxes whose centers are in the plausible subtitle area.

        This is deliberately based on box center, not full overlap: real
        subtitles can be wide, but their center usually remains near the
        content-safe middle of the frame. Corner UI/watermark text has a
        center near the edge and should not participate in subtitle-band
        learning.
        """
        fx0, fy0, fx1, fy1 = region_fractional
        x0, y0 = fx0 * frame_w, fy0 * frame_h
        x1, y1 = fx1 * frame_w, fy1 * frame_h
        kept = []
        for b in boxes:
            cx = (b[0] + b[2]) / 2.0
            cy = (b[1] + b[3]) / 2.0
            if x0 <= cx <= x1 and y0 <= cy <= y1:
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


def _normalize_ocr_match_text(text: str) -> str:
    """Normalize OCR/source text for fuzzy subtitle matching."""
    lowered = text.lower()
    # Keep letters/numbers from any script, especially CJK. Drop spaces and
    # punctuation because OCR often inserts/removes them unpredictably.
    return "".join(ch for ch in lowered if ch.isalnum())


def _subtitle_text_match_score(source_norm: str, ocr_text: str) -> float:
    ocr_norm = _normalize_ocr_match_text(ocr_text)
    if not source_norm or not ocr_norm:
        return 0.0
    if source_norm in ocr_norm:
        return 1.0
    if ocr_norm in source_norm and len(ocr_norm) >= max(4, int(len(source_norm) * 0.55)):
        return 0.9
    ratio = SequenceMatcher(None, source_norm, ocr_norm).ratio()
    common_chars = sum(1 for ch in set(source_norm) if ch in ocr_norm)
    coverage = common_chars / max(1, len(set(source_norm)))
    return max(ratio, coverage * 0.85)
