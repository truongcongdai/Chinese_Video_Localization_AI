# src/universal_video_ai/render/renderer.py
from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
import logging
import shutil
import subprocess
import time
from typing import List, Optional, Tuple

from universal_video_ai.config import TEMP_DIR
from universal_video_ai.render.subtitle_region_tracker import (
    AdaptiveSubtitleRegionConfig,
    AdaptiveSubtitleRegionTracker,
    TrackedRegion,
)
from universal_video_ai.render.branding import (
    BrandingConfig,
    build_branding_filters,
    stamp_fingerprint_metadata,
    write_branding_manifest,
)
from universal_video_ai.render.animated_subtitles import (
    AnimatedSubtitleGenerator,
    SubtitleEffect,
    SubtitleStyle,
)
from universal_video_ai.postprocess.video_transform import (
    VideoTransformer,
    TransformConfig,
)

__all__ = ["Renderer", "RenderConfig", "TextOverlay", "AnimatedSubtitleConfig", "VideoTemplateConfig", "BrandingConfig"]

_logger = logging.getLogger(__name__)


def _check_ffmpeg_available() -> bool:
    """
    Check whether ffmpeg binary is available in PATH.
    """
    return shutil.which("ffmpeg") is not None


def _escape_drawtext(text: str) -> str:
    """
    Escape a string for safe use inside an ffmpeg `drawtext` filter argument.

    ffmpeg's filter-graph mini-language treats `\\`, `:`, `'`, `%`, `,`, `[`
    and `]` specially, so any of these appearing in translated subtitle text
    (which is untrusted, human-written content) must be escaped or the
    filter_complex string will fail to parse or silently truncate the text.
    """
    text = text.replace("\\", "\\\\")
    text = text.replace(":", "\\:")
    text = text.replace("%", "\\%")
    text = text.replace(",", "\\,")
    text = text.replace("[", "\\[")
    text = text.replace("]", "\\]")
    # Single quotes are awkward to escape inside ffmpeg's own quoting; swap
    # for a visually-equivalent right single quote instead of risking a
    # broken filter graph.
    text = text.replace("'", "\u2019")
    text = text.replace("\n", " ")
    return text


def _escape_filter_path(path: str) -> str:
    """
    Escape a filesystem path for safe use as an ffmpeg filter option value
    (e.g. `subtitles=<this>`), per ffmpeg's own filtergraph escaping rules.

    The `subtitles` filter parses its argument as `filename[:opt=val...]`,
    so a raw colon in the path (Windows drive letters like `C:\\...`, or
    just a folder containing a colon) gets misread as the start of an
    option list instead of part of the filename — this is what produces
    "Unable to parse option value" errors, or in some builds, a filter that
    silently misbehaves instead of failing cleanly. Backslashes need
    escaping first (so the colon-escaping backslash itself isn't
    misinterpreted), then colons, then the whole thing is wrapped in single
    quotes as ffmpeg's docs recommend for values containing special chars.
    """
    escaped = path.replace("\\", "\\\\").replace(":", "\\:")
    return f"'{escaped}'"


@dataclass(frozen=True)
class TextOverlay:
    """
    A translated-subtitle overlay that replaces burned-in on-screen text for
    one sentence, shown only during that sentence's time window.

    Combine `render.text_detector.TextRegion` (where the original text is)
    with the matching translated `TimelineSegment` (what to show instead,
    and when) to build these.

    Attributes:
        start: seconds, when this overlay should appear (matches the
               original sentence's on-screen appearance).
        end: seconds, when this overlay should disappear.
        x, y, width, height: pixel region of the original video to cover.
        text: translated text to draw in place of the covered region.
        box_color: ffmpeg color used to cover the original text (default
               white, since burned-in subtitles are usually on a plain
               background bar; override per-video if needed).
        font_color: color of the translated text.
        font_path: optional path to a Unicode-capable .ttf/.otf font. This
               MUST support the target language's characters (e.g. Vietnamese
               diacritics); ffmpeg's default font may not. If omitted,
               ffmpeg's fontconfig default is used, which may render
               diacritics/CJK incorrectly.
    """

    start: float
    end: float
    x: int
    y: int
    width: int
    height: int
    text: str
    box_color: str = "white"
    font_color: str = "black"
    font_path: Optional[str] = None
    font_size: Optional[int] = None
    """Explicit font size in px. If None, falls back to a size derived from
    `height` (height * 0.6). Callers building many overlays for the same
    video (e.g. the orchestrator) should set the SAME explicit font_size on
    every overlay so the translated text renders at one consistent size
    throughout the video, instead of fluctuating with each detected box's
    height."""


@dataclass(frozen=True)
class AnimatedSubtitleConfig:
    """
    Configuration for animated subtitle effects.

    Attributes:
        enabled: Whether animated subtitles are enabled
        effect: The animation effect to apply (SubtitleEffect enum)
        style: Subtitle styling configuration
        effect_params: Additional parameters for the specific effect
    """
    enabled: bool = False
    effect: SubtitleEffect = SubtitleEffect.NONE
    style: Optional[SubtitleStyle] = None
    effect_params: dict = None

    def __post_init__(self):
        if self.style is None:
            object.__setattr__(self, 'style', SubtitleStyle())
        if self.effect_params is None:
            object.__setattr__(self, 'effect_params', {})


@dataclass(frozen=True)
class VideoTemplateConfig:
    """
    Configuration for video template effects.

    Attributes:
        enabled: Whether video template is enabled
        template: Template preset name (minimal, cinematic, vibrant, professional, social)
        transition: Transition effect between scenes (fade, slide, dissolve, wipe, zoom)
        color_effect: Color grading effect (none, warm, cool, vintage, high_contrast)
        audio_filters: Audio filter settings (equalizer, compressor, etc.)
        video_quality: Video quality preset (low, medium, high, ultra)
    """
    enabled: bool = False
    template: str = "minimal"
    transition: str = "fade"
    color_effect: str = "none"
    audio_filters: dict = None
    video_quality: str = "medium"

    def __post_init__(self):
        if self.audio_filters is None:
            object.__setattr__(self, 'audio_filters', {})


@dataclass(frozen=True)
class RenderConfig:
    """
    Configuration for rendering final video with FFmpeg.

    Attributes:
        video_codec: video codec to use when re-encoding (default libx264).
                     If no re-encoding is required (no filters), we may use 'copy' for speed.
        crf: quality for libx264 (lower is better). Only used when re-encoding.
        preset: ffmpeg preset (e.g. "medium", "fast").
        audio_codec: codec for audio (default "aac").
        audio_bitrate: bitrate for audio (e.g. "192k").
        overwrite: whether to overwrite existing output file.
        timeout_seconds: ffmpeg idle timeout for rendering. FFmpeg is only
                  stopped after this many seconds without exit/progress
                  output, so a slow but active render is allowed to finish.
        blur_text: whether to apply blur filter to cover original text (e.g., Chinese subtitles).
                   This is a coarse legacy option; prefer passing `text_overlays`
                   to `render()` for accurate, per-sentence, timestamp-aware covering.
        blur_box: coordinates for blur box as "x:y:w:h" (e.g., "0:0:1920:100" for top bar).
                  If None, defaults to blurring the whole frame (see `render()` docs).
        default_overlay_font_path: fallback font used for `TextOverlay`s that
                  don't specify their own `font_path`. Should point to a
                  Unicode-capable TTF (e.g. NotoSans, DejaVuSans) so
                  Vietnamese/other diacritics render correctly.
        animated_subtitle_config: Configuration for animated subtitle effects.
        video_template_config: Configuration for video template effects.
        transform_config: Configuration for video transformations (flip, border, etc.).
    """

    video_codec: str = "libx264"
    crf: int = 23
    preset: str = "medium"
    audio_codec: str = "aac"
    audio_bitrate: str = "192k"
    overwrite: bool = True
    timeout_seconds: int = 1800  # 30 minutes without progress output
    blur_text: bool = False
    blur_box: Optional[str] = None
    default_overlay_font_path: Optional[str] = None
    animated_subtitle_config: Optional[AnimatedSubtitleConfig] = None
    video_template_config: Optional[VideoTemplateConfig] = None
    transform_config: Optional[TransformConfig] = None
    # Static region to permanently blur for the ENTIRE video, regardless of
    # blur_text/text_overlays — intended for a platform watermark (e.g. the
    # TikTok/Douyin logo + @username + reup title baked into the corner of
    # a downloaded source video), which is not a translatable subtitle and
    # shouldn't be treated like one. Expressed as (x0, y0, x1, y1) FRACTIONS
    # (0.0-1.0) of the frame so it works across differently-sized source
    # videos. Example: (0.80, 0.72, 1.0, 1.0) covers the bottom-right ~20%
    # width x ~28% height corner.
    watermark_box_fractional: Optional[Tuple[float, float, float, float]] = None
    # Additional static watermark/text boxes to blur for the entire video,
    # using the same fractional coordinate format. Use this when a source has
    # more than one persistent non-subtitle text layer, such as a channel name
    # in the top corner plus a faint center watermark.
    watermark_boxes_fractional: Tuple[Tuple[float, float, float, float], ...] = ()

    # ---- Brand logo overlay (user-supplied image burned into every frame) ----
    # Path to a logo/watermark image (PNG with alpha transparency recommended)
    # to overlay on the output video for its entire duration. None disables
    # this feature entirely (no extra input/filter added, zero cost).
    logo_path: Optional[str] = None
    # Which corner to place it in.
    logo_corner: str = "bottom_right"  # top_left | top_right | bottom_left | bottom_right
    # Target width in pixels; height is scaled automatically to preserve the
    # logo image's own aspect ratio.
    logo_size_px: int = 120
    # Gap in pixels between the logo and the frame edge.
    logo_margin_px: int = 24

    # Global optional text branding shared by localization, reup/batch and
    # Content OS outputs. Disabled by default, so existing jobs are unchanged.
    branding_config: Optional[BrandingConfig] = None

    # Adaptive subtitle cleanup. These settings contain no fixed subtitle
    # coordinate. Regions are learned from OCR overlays and normalized to the
    # actual source resolution for each render.
    adaptive_text_region_enabled: bool = True
    adaptive_text_cleanup_enabled: bool = True
    # Keep the cleanup visually natural by default. An opaque rectangle is
    # only an opt-in emergency fallback; localized delogo cleanup is used
    # for normal subtitle removal.
    adaptive_text_drawbox_enabled: bool = False
    # Two local cleanup passes remove antialiased glyph edges much more
    # reliably than one pass while remaining limited to the learned region.
    adaptive_text_cleanup_passes: int = 5
    # Cleanup time is deliberately wider than translated-text display time.
    # OCR/ASR boundaries can differ by a few frames, so using the exact cue
    # window can expose the tail of the source subtitle. Padding is calculated
    # from each cue duration and clipped against neighbouring cues; it is not a
    # fixed timestamp or tied to any specific video.
    adaptive_text_cleanup_time_padding_ratio: float = 0.20
    adaptive_text_cleanup_max_gap_fill_ratio: float = 0.50
    # When neighbouring cues belong to the same learned subtitle band, keep
    # cleanup continuous through their short boundary gap. This prevents a
    # 1-3 frame flash of the original subtitle between adjacent cues.
    adaptive_text_cleanup_bridge_same_band: bool = True
    # Final residual-suppression layer. delogo can leave high-contrast CJK
    # strokes on difficult backgrounds; a narrow translucent veil over the
    # learned cleanup region guarantees they do not flash through. Geometry
    # remains fully adaptive and cue-timed, and this replaces the old opaque
    # white rectangle.
    adaptive_text_residual_veil_enabled: bool = True
    adaptive_text_residual_veil_color: str = "black"
    adaptive_text_residual_veil_opacity: float = 0.14
    # Never paint a veil over a weakly tracked or oversized region. The veil
    # is only a subtle last-resort edge suppressor after delogo, not the main
    # removal mechanism.
    adaptive_text_residual_veil_min_confidence: float = 0.72
    adaptive_text_residual_veil_max_frame_area_ratio: float = 0.055
    adaptive_text_region_config: Optional[AdaptiveSubtitleRegionConfig] = None


class Renderer:
    """
    Combine video and audio into a final video file and optionally burn subtitles
    and/or timestamp-accurate text-cover overlays.

    Responsibilities:
    - Validate inputs (existence, file)
    - Construct appropriate ffmpeg command
    - Run ffmpeg and handle errors
    - Return Path to output file on success
    """

    def __init__(self, config: Optional[RenderConfig] = None, logger: Optional[logging.Logger] = None) -> None:
        self.config = config or RenderConfig()
        self.logger = logger or _logger
        self.subtitle_generator = AnimatedSubtitleGenerator(logger=self.logger)

        if not _check_ffmpeg_available():
            self.logger.warning("FFmpeg not found in PATH; rendering may fail at runtime")

        self.logger.debug("Renderer initialized with config=%s", self.config)

    def get_default_output(self, video_path: Path) -> Path:
        """
        Compute default output path for rendered video.

        Default location: <video_path.parent>/rendered/<video_stem>.final.mp4
        """
        video_path = video_path.resolve()
        out_dir = video_path.parent / "rendered"
        out_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{video_path.stem}.final.mp4"
        return out_dir / filename

    def _build_text_overlay_filters(
            self, overlays: List[TextOverlay], frame_w: Optional[int] = None, frame_h: Optional[int] = None
    ) -> List[str]:
        """Build adaptive cleanup, cover, and translated-text filters.

        The region tracker learns one or more subtitle bands from the OCR
        overlays themselves. It never assumes a fixed Y coordinate and all
        padding is derived from detected line height plus source resolution.
        This allows subtitles to move between layouts/scenes while rejecting
        isolated title or watermark detections.
        """
        filters: List[str] = []
        if not overlays:
            return filters

        avg_char_width_ratio = 0.72
        min_font_size = 8
        max_font_size = 64

        if frame_w and frame_h and self.config.adaptive_text_region_enabled:
            tracker = AdaptiveSubtitleRegionTracker(
                self.config.adaptive_text_region_config or AdaptiveSubtitleRegionConfig()
            )
            tracked = tracker.track(overlays, frame_w, frame_h)
        else:
            tracked = []
            for overlay in overlays:
                tracked.append((overlay, TrackedRegion(
                    overlay.x, overlay.y, overlay.width, overlay.height,
                    overlay.x, overlay.y, overlay.width, overlay.height, -1, 1.0
                )))

        # Neighbour-based temporal extension requires chronological order.
        tracked = sorted(tracked, key=lambda item: (item[0].start, item[0].end))

        for index, (overlay, region) in enumerate(tracked):
            # Keep translated text on its exact canonical cue clock, while the
            # source-subtitle cleanup gets a slightly wider adaptive window.
            text_enable_expr = f"between(t\\,{overlay.start:.3f}\\,{overlay.end:.3f})"
            cue_duration = max(0.001, overlay.end - overlay.start)
            temporal_pad = cue_duration * max(0.0, self.config.adaptive_text_cleanup_time_padding_ratio)
            cleanup_start = max(0.0, overlay.start - temporal_pad)
            cleanup_end = overlay.end + temporal_pad

            previous = tracked[index - 1] if index > 0 else None
            following = tracked[index + 1] if index + 1 < len(tracked) else None

            if previous is not None:
                previous_overlay, previous_region = previous
                gap = overlay.start - previous_overlay.end
                if gap > 0:
                    max_fill = gap * max(0.0, min(1.0, self.config.adaptive_text_cleanup_max_gap_fill_ratio))
                    cleanup_start = max(previous_overlay.end, overlay.start - min(temporal_pad, max_fill))
                    if (
                            self.config.adaptive_text_cleanup_bridge_same_band
                            and previous_region.cluster_id == region.cluster_id
                            and region.cluster_id >= 0
                    ):
                        cleanup_start = previous_overlay.end
                else:
                    cleanup_start = max(0.0, min(cleanup_start, overlay.start))

            if following is not None:
                next_overlay, next_region = following
                gap = next_overlay.start - overlay.end
                if gap > 0:
                    max_fill = gap * max(0.0, min(1.0, self.config.adaptive_text_cleanup_max_gap_fill_ratio))
                    cleanup_end = min(next_overlay.start, overlay.end + min(temporal_pad, max_fill))
                    if (
                            self.config.adaptive_text_cleanup_bridge_same_band
                            and next_region.cluster_id == region.cluster_id
                            and region.cluster_id >= 0
                    ):
                        cleanup_end = next_overlay.start
                else:
                    cleanup_end = max(cleanup_end, overlay.end)

            # For a continuous learned subtitle band, bridge only the inner
            # cue boundary. Do not let the first/last cue's cleanup leak into
            # unrelated footage outside the band.
            if previous is None and following is not None:
                _next_overlay, next_region = following
                if next_region.cluster_id == region.cluster_id and region.cluster_id >= 0:
                    cleanup_start = overlay.start
            if following is None and previous is not None:
                _previous_overlay, previous_region = previous
                if previous_region.cluster_id == region.cluster_id and region.cluster_id >= 0:
                    cleanup_end = overlay.end

            cleanup_enable_expr = f"between(t\\,{cleanup_start:.3f}\\,{cleanup_end:.3f})"
            font_path = overlay.font_path or self.config.default_overlay_font_path
            font_clause = f"fontfile='{font_path}':" if font_path else ""

            # Clean the original glyphs before the replacement box is painted.
            # delogo works as a local adaptive blur/inpaint approximation and is
            # gated to the exact cue time, so the rest of the video stays sharp.
            if self.config.adaptive_text_cleanup_enabled and frame_w and frame_h:
                cx, cy, cw, ch = self._clamp_delogo_box(
                    region.cleanup_x, region.cleanup_y,
                    region.cleanup_width, region.cleanup_height,
                    frame_w, frame_h,
                )
                passes = max(1, int(self.config.adaptive_text_cleanup_passes))
                # Alternate expanded and core passes. The expanded passes
                # remove anti-aliased outline/shadow pixels; the core passes
                # suppress the bright glyph body. Every amount is proportional
                # to the learned region, never a fixed screen coordinate.
                pass_scales = (1.06, 1.00, 0.96, 1.02, 0.98)
                for pass_index in range(passes):
                    scale = pass_scales[pass_index] if pass_index < len(pass_scales) else 1.0
                    pw = max(2, int(round(cw * scale)))
                    ph = max(2, int(round(ch * scale)))
                    px = int(round(cx + (cw - pw) / 2.0))
                    py = int(round(cy + (ch - ph) / 2.0))
                    px, py, pw, ph = self._clamp_delogo_box(
                        px, py, pw, ph, frame_w, frame_h,
                    )
                    filters.append(
                        f"delogo=x={px}:y={py}:w={pw}:h={ph}:show=0:enable='{cleanup_enable_expr}'"
                    )

                if self.config.adaptive_text_residual_veil_enabled:
                    veil_opacity = max(0.0, min(1.0, self.config.adaptive_text_residual_veil_opacity))
                    veil_area_ratio = (region.width * region.height) / max(1.0, float(frame_w * frame_h))
                    # The veil is deliberately applied to the tight tracked
                    # subtitle box, never the expanded cleanup box. This keeps
                    # the underlying picture visible and prevents the large
                    # dark rectangles seen when cleanup padding is substantial.
                    if (
                            veil_opacity > 0.0
                            and (
                                region.confidence >= self.config.adaptive_text_residual_veil_min_confidence
                                or veil_opacity >= 0.50
                            )
                            and veil_area_ratio <= self.config.adaptive_text_residual_veil_max_frame_area_ratio
                    ):
                        filters.append(
                            f"drawbox=x={region.x}:y={region.y}:w={region.width}:h={region.height}:"
                            f"color={self.config.adaptive_text_residual_veil_color}@{veil_opacity:.3f}:"
                            f"t=fill:enable='{cleanup_enable_expr}'"
                        )

            text_len = max(1, len(overlay.text))
            font_size = overlay.font_size if overlay.font_size is not None else max(
                min_font_size, int(region.height * 0.46)
            )
            font_size = min(max_font_size, max(min_font_size, font_size))

            # Internal horizontal breathing room scales with the detected line
            # height; it is not a resolution-specific pixel constant.
            box_padding_x = max(2, int(region.height * 0.34))
            box_cx = region.x + region.width / 2.0
            max_box_width = int(frame_w * 0.90) if frame_w else region.width * 4
            est_text_width = text_len * font_size * avg_char_width_ratio
            avail_width = max(1, region.width - 2 * box_padding_x)

            if est_text_width > avail_width:
                needed_box_width = int(est_text_width + 2 * box_padding_x)
                box_width = min(needed_box_width, max_box_width)
                avail_at_box = max(1, box_width - 2 * box_padding_x)
                if est_text_width > avail_at_box:
                    font_size = max(
                        min_font_size,
                        int(avail_at_box / (text_len * avg_char_width_ratio)),
                    )
            else:
                box_width = region.width

            box_x = int(box_cx - box_width / 2.0)
            if frame_w:
                box_width = min(box_width, frame_w)
                box_x = max(0, min(box_x, frame_w - box_width))

            if self.config.adaptive_text_drawbox_enabled or not (frame_w and frame_h):
                # Without frame dimensions adaptive delogo cannot be built;
                # retain the opaque cover so the original subtitle does not
                # remain visible underneath the translation.
                filters.append(
                    f"drawbox=x={box_x}:y={region.y}:w={box_width}:h={region.height}"
                    f":color={overlay.box_color}@1.0:t=fill:enable='{cleanup_enable_expr}'"
                )

            if overlay.text:
                filters.append(
                    "drawtext="
                    f"{font_clause}"
                    f"text='{_escape_drawtext(overlay.text)}':"
                    f"x={box_x}+({box_width}-text_w)/2:"
                    f"y={region.y}+({region.height}-ascent+descent)/2:"
                    f"fontsize={font_size}:fontcolor={overlay.font_color}:"
                    f"enable='{text_enable_expr}'"
                )
        return filters

    def _build_animated_subtitle_filters(
            self, subtitle_segments: List[dict]
    ) -> List[str]:
        """
        Build animated subtitle filters from subtitle segments.

        Args:
            subtitle_segments: List of dicts with keys: text, start, end

        Returns:
            List of FFmpeg filter strings for animated subtitles
        """
        if not self.config.animated_subtitle_config or not self.config.animated_subtitle_config.enabled:
            return []

        anim_config = self.config.animated_subtitle_config
        filters = []

        for segment in subtitle_segments:
            text = segment.get("text", "")
            start = segment.get("start", 0.0)
            end = segment.get("end", 0.0)

            if not text or end <= start:
                continue

            filter_str = self.subtitle_generator.generate_filter(
                text=text,
                start=start,
                end=end,
                effect=anim_config.effect,
                style=anim_config.style,
                **anim_config.effect_params
            )
            filters.append(filter_str)

        return filters

    def _build_video_template_filters(self) -> List[str]:
        """
        Build video template filters (color grading, transitions, etc.).

        Returns:
            List of FFmpeg filter strings for video template effects
        """
        if not self.config.video_template_config or not self.config.video_template_config.enabled:
            return []

        template_config = self.config.video_template_config
        filters = []

        # Color grading effects
        color_effect = template_config.color_effect
        if color_effect == "warm":
            filters.append("eq=contrast=1.1:saturation=1.2:brightness=0.05")
        elif color_effect == "cool":
            filters.append("eq=contrast=1.05:saturation=0.9:brightness=-0.05")
        elif color_effect == "vintage":
            filters.append("eq=saturation=0.8:contrast=1.15")
            filters.append("curves=all='0/0 0.2/0.3 0.5/0.5 0.8/0.7 1/1'")
        elif color_effect == "high_contrast":
            filters.append("eq=contrast=1.3:saturation=1.1")

        # Video quality adjustments
        quality = template_config.video_quality
        if quality == "low":
            filters.append("scale=iw:ih:flags=lanczos")
        elif quality == "high":
            filters.append("scale=iw*1.1:ih*1.1:flags=lanczos,crop=iw:ih")
        elif quality == "ultra":
            filters.append("scale=iw*1.2:ih*1.2:flags=lanczos,crop=iw:ih")

        return filters

    def _build_audio_template_filters(self) -> List[str]:
        """
        Build audio template filters (equalizer, compressor, etc.).

        Returns:
            List of FFmpeg filter strings for audio template effects
        """
        if not self.config.video_template_config or not self.config.video_template_config.enabled:
            return []

        audio_filters = self.config.video_template_config.audio_filters or {}
        filters = []

        # Audio equalizer
        if audio_filters.get("equalizer"):
            eq = audio_filters["equalizer"]
            if eq.get("bass"):
                filters.append(f"equalizer=f=100:width_type=h:width=100:g={eq['bass']}")
            if eq.get("treble"):
                filters.append(f"equalizer=f=10000:width_type=h:width=1000:g={eq['treble']}")

        # Audio compressor
        if audio_filters.get("compressor"):
            comp = audio_filters["compressor"]
            filters.append(
                f"acompressor=threshold={comp.get('threshold', -20)}dB:ratio={comp.get('ratio', 4)}:attack={comp.get('attack', 20)}:release={comp.get('release', 250)}")

        # Volume normalization
        if audio_filters.get("normalize", False):
            filters.append("loudnorm=I=-16:TP=-1.5:LRA=11")

        return filters

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

    @staticmethod
    def _region_blur_filter(x: int, y: int, w: int, h: int) -> str:
        """Build a single-input/single-output filter for a static text region.

        This intentionally uses `delogo` instead of a crop+boxblur+overlay
        subgraph. The latter needs two labeled video inputs and breaks when
        the whole video filter chain is emitted as a filter_complex script;
        `delogo` composes cleanly inside the existing linear chain.
        """
        return f"delogo=x={x}:y={y}:w={w}:h={h}:show=0"

    @staticmethod
    def _clamp_delogo_box(x: int, y: int, w: int, h: int, frame_w: int, frame_h: int) -> Tuple[int, int, int, int]:
        """Keep delogo boxes inside FFmpeg's stricter non-edge bounds."""
        if frame_w <= 4 or frame_h <= 4:
            return x, y, w, h
        x = max(1, min(x, frame_w - 3))
        y = max(1, min(y, frame_h - 3))
        w = max(2, min(w, frame_w - x - 1))
        h = max(2, min(h, frame_h - y - 1))
        return x, y, w, h

    def _build_pre_subtitle_transform_filters(self) -> List[str]:
        """
        Apply source-video transforms that must happen before subtitles are
        drawn. Flip belongs here; applying it after render mirrors the newly
        burned subtitles too.
        """
        transform_config = self.config.transform_config
        if not transform_config or not transform_config.enable_flip:
            return []
        if transform_config.flip_mode.value == "none":
            return []
        return [transform_config.flip_mode.value]

    def _post_subtitle_transform_config(self) -> Optional[TransformConfig]:
        """
        Return remaining transformations that are still safe to run after
        subtitles are burned. Flip is intentionally removed because it is
        already applied to the source video before subtitle drawing.
        """
        transform_config = self.config.transform_config
        if not transform_config:
            return None

        post_config = replace(transform_config, enable_flip=False)
        if (
                not post_config.target_width
                and not post_config.target_height
                and not post_config.enable_border
                and not post_config.enable_split_screen
                and not post_config.enable_randomization
        ):
            return None
        return post_config

    def _transform_text_overlays_for_pre_filters(
            self,
            text_overlays: List[TextOverlay],
            frame_w: Optional[int],
            frame_h: Optional[int],
    ) -> List[TextOverlay]:
        """
        TextOverlay coordinates are detected on the original source frame.
        If the source is flipped before subtitles are drawn, mirror the cover
        boxes too so they still cover the same visual region after the flip.
        """
        transform_config = self.config.transform_config
        if not text_overlays or not transform_config or not transform_config.enable_flip:
            return text_overlays

        flip_value = transform_config.flip_mode.value
        if flip_value == "none":
            return text_overlays

        needs_w = "hflip" in flip_value
        needs_h = "vflip" in flip_value
        if (needs_w and frame_w is None) or (needs_h and frame_h is None):
            self.logger.warning(
                "Flip is enabled but video dimensions could not be determined; "
                "text-cover overlay coordinates cannot be mirrored reliably."
            )
            return text_overlays

        transformed: List[TextOverlay] = []
        for overlay in text_overlays:
            x = overlay.x
            y = overlay.y
            if needs_w and frame_w is not None:
                x = max(0, frame_w - overlay.x - overlay.width)
            if needs_h and frame_h is not None:
                y = max(0, frame_h - overlay.y - overlay.height)
            transformed.append(replace(overlay, x=x, y=y))
        return transformed

    def _build_command(
            self,
            input_video: Path,
            input_audio: Path,
            output: Path,
            subtitles: Optional[Path] = None,
            text_overlays: Optional[List[TextOverlay]] = None,
            subtitle_segments: Optional[List[dict]] = None,
    ) -> List[str]:
        """
        Build ffmpeg command list based on configuration and presence of subtitles
        and/or text overlays.

        Strategy:
        - If subtitles, text_overlays, or blur_text enabled: we need to re-encode
          video (apply -vf filters)
        - Otherwise: use stream copy for video (-c:v copy) and encode audio to target codec
                     (faster).
        """
        cmd: List[str] = ["ffmpeg"]

        if self.config.overwrite:
            cmd.append("-y")

        # Inputs
        cmd.extend(["-i", str(input_video)])
        cmd.extend(["-i", str(input_audio)])

        logo_configured = bool(self.config.logo_path) and Path(self.config.logo_path).exists()
        if self.config.logo_path and not logo_configured:
            self.logger.warning(
                "RenderConfig.logo_path=%r does not exist; skipping logo overlay for this render.",
                self.config.logo_path,
            )
        if logo_configured:
            # `-loop 1` keeps this single still image "playing" for the
            # whole output duration instead of ending after one frame,
            # which is what `overlay` needs to composite it onto every
            # frame of the main video, not just the first.
            cmd.extend(["-loop", "1", "-i", str(self.config.logo_path)])

        text_overlays = text_overlays or []
        subtitle_segments = subtitle_segments or []

        # Determine if we need video filters (subtitles, overlays, or blur)
        needs_reencode = (
                subtitles is not None
                or self.config.blur_text
                or bool(text_overlays)
                or self.config.watermark_box_fractional is not None
                or bool(self.config.watermark_boxes_fractional)
                or logo_configured
                or bool(self.config.branding_config and self.config.branding_config.normalized().enabled)
                or bool(self._build_pre_subtitle_transform_filters())
                or (self.config.animated_subtitle_config and self.config.animated_subtitle_config.enabled and bool(
            subtitle_segments))
                or (self.config.video_template_config and self.config.video_template_config.enabled)
        )

        if text_overlays and not self.config.default_overlay_font_path and not any(
                o.font_path for o in text_overlays
        ):
            self.logger.warning(
                "Rendering %d text_overlay(s) with no font_path/default_overlay_font_path set. "
                "FFmpeg's default fontconfig font may not render Vietnamese diacritics (or other "
                "non-ASCII target-language text) correctly. Set RenderConfig.default_overlay_font_path "
                "to a Unicode-capable .ttf (e.g. NotoSans-Regular.ttf, DejaVuSans.ttf).",
                len(text_overlays),
            )

        if needs_reencode:
            # Build video filter chain
            filters = self._build_pre_subtitle_transform_filters()

            frame_w: Optional[int] = None
            frame_h: Optional[int] = None
            if (
                    self.config.watermark_box_fractional is not None
                    or self.config.watermark_boxes_fractional
                    or text_overlays
                    or (self.config.branding_config and self.config.branding_config.normalized().enabled)
            ):
                dims = self._get_video_dimensions(input_video)
                if dims is not None:
                    frame_w, frame_h = dims

            text_overlays_for_render = self._transform_text_overlays_for_pre_filters(
                text_overlays,
                frame_w,
                frame_h,
            )

            # Permanently blur a platform watermark (logo/@username/reup
            # title), independent of blur_text/text_overlays — it's not a
            # translatable subtitle, it's baked into every single frame, so
            # it's covered for the whole video rather than per-sentence.
            watermark_boxes: List[Tuple[float, float, float, float]] = []
            if self.config.watermark_box_fractional is not None:
                watermark_boxes.append(self.config.watermark_box_fractional)
            watermark_boxes.extend(self.config.watermark_boxes_fractional)
            if watermark_boxes:
                if frame_w is None or frame_h is None:
                    self.logger.warning(
                        "watermark boxes are set but video dimensions could not be "
                        "determined; skipping watermark cover for this render."
                    )
                else:
                    for fx0, fy0, fx1, fy1 in watermark_boxes:
                        wx, wy = int(fx0 * frame_w), int(fy0 * frame_h)
                        ww = max(2, int((fx1 - fx0) * frame_w))
                        wh = max(2, int((fy1 - fy0) * frame_h))
                        wx, wy, ww, wh = self._clamp_delogo_box(wx, wy, ww, wh, frame_w, frame_h)
                        filters.append(self._region_blur_filter(wx, wy, ww, wh))

            # Add blur filter only as a LAST-RESORT fallback for covering
            # burned-in text — and only when we have no better option.
            #
            # Precedence, from best to worst:
            #   1. text_overlays (OCR-detected box + translated caption) —
            #      precise, per-sentence, keeps the rest of the frame sharp.
            #   2. blur_text WITH an explicit blur_box — blurs only that
            #      region, not the whole frame.
            #   3. blur_text with NO blur_box — used to fall back to
            #      `boxblur` over the ENTIRE frame ("temporary fix" per the
            #      old TODO). This made the whole video look blurry/hard to
            #      watch even though only a small subtitle strip needed
            #      covering, so it is intentionally disabled below rather
            #      than applied.
            #
            # If text_overlays were successfully detected, we skip blur_text
            # entirely — covering the same region twice (blur + white box)
            # is redundant and blur_text's whole-frame fallback would
            # actively make the video worse.
            if self.config.blur_text and not text_overlays:
                if self.config.blur_box:
                    # blur_box is documented/entered as "x:y:w:h"; pass those
                    # pixels into the same single-input region filter used for
                    # static watermark boxes.
                    try:
                        bx, by, bw, bh = (int(v) for v in self.config.blur_box.split(":"))
                        filters.append(self._region_blur_filter(bx, by, bw, bh))
                    except ValueError:
                        self.logger.error(
                            "Malformed blur_box=%r (expected 'x:y:w:h' integers); skipping.",
                            self.config.blur_box,
                        )
                else:
                    # No region given: do NOT blur the entire frame anymore.
                    # A full-frame blur is a much worse user experience than
                    # simply not covering the text, so we skip it and log
                    # loudly instead of silently degrading every video.
                    self.logger.warning(
                        "blur_text=True but no blur_box was set and no text_overlays were "
                        "available — skipping blur entirely instead of blurring the whole "
                        "frame. Set RenderConfig.blur_box to a specific 'x:y:w:h' region, "
                        "or (preferred) enable LocalizationConfig.enable_text_cover so the "
                        "on-screen text region is detected automatically via OCR."
                    )

            # Add per-sentence text-cover + translated-text overlays, each
            # only active during its own [start, end) window.
            filters.extend(self._build_text_overlay_filters(
                text_overlays_for_render, frame_w=frame_w, frame_h=frame_h
            ))

            # Add animated subtitle filters if enabled
            filters.extend(self._build_animated_subtitle_filters(subtitle_segments))

            # Add video template filters if enabled
            filters.extend(self._build_video_template_filters())

            # Global branding is intentionally placed before the final subtitle
            # layer. The runner also routes above the lower subtitle-safe area,
            # and subtitles remain readable on top even for unusual layouts.
            if self.config.branding_config and self.config.branding_config.normalized().enabled:
                if frame_w is None or frame_h is None:
                    dims = self._get_video_dimensions(input_video)
                    if dims is not None:
                        frame_w, frame_h = dims
                if frame_w is not None and frame_h is not None:
                    filters.extend(build_branding_filters(
                        self.config.branding_config, frame_w, frame_h
                    ))
                else:
                    self.logger.warning(
                        "Branding enabled but source dimensions are unavailable; skipping visible branding."
                    )

            # Add subtitles filter if provided. The `subtitles=` filter option
            # value is itself parsed as filename[:key=value...], so a raw path
            # containing a colon (a Windows drive letter, or just a folder
            # name with a colon in it) gets misparsed as a bogus filter
            # option and ffmpeg fails with "Unable to parse option value" —
            # or, on some inputs, stalls/gets stuck applying that filter
            # instead of failing cleanly. Escaping `\` and `:` and wrapping
            # in single quotes (ffmpeg's documented approach for filter
            # option values with special characters) avoids this entirely.
            if subtitles:
                if Path(subtitles).suffix.lower() == ".ass":
                    # Preserve ASS karaoke colours, timing tags and its
                    # deterministic two-line layout.
                    filters.append(f"subtitles={_escape_filter_path(str(subtitles))}")
                else:
                    filters.append(
                        f"subtitles={_escape_filter_path(str(subtitles))}:"
                        "force_style='FontName=DejaVu Sans,FontSize=13,"
                        "MarginL=28,MarginR=28,MarginV=45,Alignment=2,WrapStyle=0,"
                        "BorderStyle=3,Outline=1,Shadow=0'"
                    )

            cmd.extend(["-map", "1:a"])

            if logo_configured:
                # A logo overlay needs a SECOND video stream (the logo
                # image) composited onto the first, which `-vf` (a single
                # linear chain over one stream) can't express — it needs
                # `-filter_complex`'s labeled-pad graph instead. Every
                # filter that would have gone in the simple `-vf` chain
                # above is applied first (as `[0:v]<...>[base]`), then the
                # logo is scaled and overlaid on top of that result.
                base_chain = ",".join(filters) if filters else "copy"
                x_expr, y_expr = self._logo_position_expr(self.config.logo_corner, self.config.logo_margin_px)
                filter_complex = (
                    f"[0:v]{base_chain}[base];"
                    f"[2:v]scale={self.config.logo_size_px}:-1[wm];"
                    f"[base][wm]overlay={x_expr}:{y_expr}:shortest=1[outv]"
                )
                cmd.extend(["-filter_complex", filter_complex, "-map", "[outv]"])
            else:
                vf = ",".join(filters) if filters else None
                if vf:
                    cmd.extend(["-map", "0:v", "-vf", vf])
                else:
                    cmd.extend(["-map", "0:v"])

            # Build audio filter chain
            audio_filters = self._build_audio_template_filters()
            if audio_filters:
                af = ",".join(audio_filters)
                cmd.extend(["-af", af])

            cmd.extend(
                [
                    "-c:v", self.config.video_codec,
                    "-preset", self.config.preset,
                    "-crf", str(self.config.crf),
                    "-c:a", self.config.audio_codec,
                    "-b:a", self.config.audio_bitrate,
                    str(output),
                ]
            )
        else:
            # No filters: keep video stream (copy) and encode audio
            cmd.extend(
                [
                    "-map", "0:v",
                    "-map", "1:a",
                    "-c:v", "copy",
                    "-c:a", self.config.audio_codec,
                    "-b:a", self.config.audio_bitrate,
                    str(output),
                ]
            )

        return cmd

    @staticmethod
    def _logo_position_expr(corner: str, margin_px: int) -> Tuple[str, str]:
        """
        ffmpeg `overlay` x/y expressions for placing a logo in the requested
        corner, using its built-in `main_w`/`main_h`/`overlay_w`/`overlay_h`
        variables — this way we never need to know the actual frame or
        scaled-logo pixel dimensions in Python; ffmpeg resolves them at
        filter-graph run time, so it works for any source resolution and
        any logo image size.
        """
        m = str(margin_px)
        positions = {
            "top_left": (m, m),
            "top_right": (f"main_w-overlay_w-{m}", m),
            "bottom_left": (m, f"main_h-overlay_h-{m}"),
            "bottom_right": (f"main_w-overlay_w-{m}", f"main_h-overlay_h-{m}"),
        }
        return positions.get(corner, positions["bottom_right"])

    def render(
            self,
            video_path: Path,
            audio_path: Path,
            subtitles: Optional[Path] = None,
            output_path: Optional[Path] = None,
            text_overlays: Optional[List[TextOverlay]] = None,
            subtitle_segments: Optional[List[dict]] = None,
    ) -> Path:
        """
        Render the final video.

        :param video_path: Path to original video file.
        :param audio_path: Path to audio file (e.g., TTS result).
        :param subtitles: Optional path to subtitles file (SRT). If provided, subtitles will be burned in.
        :param output_path: Optional target output path. If None, use default location.
        :param text_overlays: Optional list of TextOverlay — per-sentence boxes
            that cover the original on-screen (burned-in) text and draw the
            translated text in its place, each shown only during its own
            time window. Build these from `render.text_detector.OnScreenTextDetector`
            output combined with translated `TimelineSegment`s.
        :param subtitle_segments: Optional list of subtitle segments for animated effects.
            Each segment should be a dict with keys: text, start, end.
        :raises FileNotFoundError: when inputs missing or not files
        :raises RuntimeError: when ffmpeg fails or output not created
        :return: Path to rendered video
        """
        video_path = Path(video_path).resolve()
        audio_path = Path(audio_path).resolve()
        if subtitles is not None:
            subtitles = Path(subtitles).resolve()

        # Validate inputs
        if not video_path.exists() or not video_path.is_file():
            raise FileNotFoundError(f"Video file not found: {video_path}")

        if not audio_path.exists() or not audio_path.is_file():
            raise FileNotFoundError(f"Audio file not found: {audio_path}")

        if subtitles is not None and (not subtitles.exists() or not subtitles.is_file()):
            raise FileNotFoundError(f"Subtitles file not found: {subtitles}")

        output = Path(output_path) if output_path else self.get_default_output(video_path)
        output = output.resolve()

        # Construct ffmpeg command
        cmd = self._build_command(video_path, audio_path, output, subtitles, text_overlays, subtitle_segments)

        self.logger.info(
            "Rendering output %s from video=%s audio=%s subtitles=%s text_overlays=%d subtitle_segments=%d",
            output, video_path, audio_path, subtitles, len(text_overlays or []), len(subtitle_segments or []),
        )
        self.logger.debug("FFmpeg command: %s", " ".join(cmd))

        try:
            # Adjust timeout based on filter complexity
            filter_count = sum(1 for arg in cmd if arg.startswith('-vf') or arg.startswith('-filter_complex'))
            adjusted_timeout = self.config.timeout_seconds
            if filter_count > 0:
                # For complex filter chains, increase timeout proportionally
                # Base 30 min + 1 min per 50 filters
                adjusted_timeout = max(self.config.timeout_seconds, 1800 + (filter_count * 60 // 50))
                self.logger.info("Adjusted timeout to %d seconds for complex filter chain", adjusted_timeout)
            returncode, stderr_text = self._run_ffmpeg_with_progress(cmd, adjusted_timeout)

            if returncode != 0:
                self.logger.error("FFmpeg returned non-zero exit code: %s", stderr_text)
                raise RuntimeError(f"FFmpeg failed: {stderr_text}")

            if not output.exists():
                self.logger.error("FFmpeg completed but output file not created: %s", output)
                raise RuntimeError(f"FFmpeg did not create output file: {output}")

            self.logger.info("Rendering completed successfully: %s", output)

            # Apply video transformations if configured
            post_transform_config = self._post_subtitle_transform_config()
            if post_transform_config:
                output = self._apply_transformations(output, post_transform_config)

            if self.config.branding_config and self.config.branding_config.normalized().enabled:
                stamp_fingerprint_metadata(output, self.config.branding_config, self.logger)
                write_branding_manifest(
                    output, self.config.branding_config, source_path=video_path, logger=self.logger
                )

            return output

        except subprocess.TimeoutExpired:
            self.logger.exception("FFmpeg rendering stalled for %s seconds", self.config.timeout_seconds)
            raise RuntimeError("FFmpeg rendering timed out")
        except FileNotFoundError as exc:
            # Raised when ffmpeg binary not found
            self.logger.exception("FFmpeg not found or not executable: %s", exc)
            raise RuntimeError("FFmpeg is not installed or not in PATH") from exc
        except Exception as exc:
            self.logger.exception("Unexpected error during rendering: %s", exc)
            raise RuntimeError(f"Rendering failed: {exc}") from exc

    def _apply_transformations(self, video_path: Path, transform_config: TransformConfig) -> Path:
        """
        Apply video transformations (flip, border, split-screen, etc.) to the rendered video.

        :param video_path: Path to the rendered video
        :return: Path to the transformed video
        """
        # Create output path for transformed video
        transformed_path = video_path.parent / f"{video_path.stem}.transformed{video_path.suffix}"

        self.logger.info("Applying video transformations to %s", video_path)

        transformer = VideoTransformer(
            config=transform_config,
            logger=self.logger
        )

        success = transformer.transform(video_path, transformed_path)

        if success and transformed_path.exists():
            # Replace original with transformed version
            video_path.unlink()
            transformed_path.rename(video_path)
            self.logger.info("Video transformations applied successfully: %s", video_path)
            return video_path
        else:
            self.logger.warning("Video transformations failed, using original video")
            return video_path

    def _run_ffmpeg_with_progress(self, cmd: List[str], timeout_seconds: int,
                                  heartbeat_seconds: float = 15.0) -> "tuple[int, str]":
        """
        Run an ffmpeg command while streaming its stderr instead of buffering
        it silently until exit. A full re-encode (needed whenever subtitles
        or text-cover overlays are burned in) can legitimately take many
        minutes; blocking with no output in the meantime is exactly what
        makes the process *look* hung even when it's still working. This
        logs ffmpeg's own progress line (it reports `frame=`/`time=` on
        stderr) at most once every `heartbeat_seconds`, so long renders stay
        visibly alive, and enforces `timeout_seconds` as an idle timeout by
        killing the process only if it stops exiting or producing progress
        output for that long.

        :return: (returncode, full stderr text) — stderr text is used for
            error reporting on failure, matching the previous behavior.
        """
        import threading

        process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            encoding='utf-8',
            errors='replace',
            bufsize=1,
        )

        stderr_lines: List[str] = []
        last_heartbeat = time.monotonic()
        last_activity = time.monotonic()
        last_progress_line = ""
        lock = threading.Lock()

        def _reader() -> None:
            nonlocal last_heartbeat, last_activity, last_progress_line
            assert process.stderr is not None
            for line in process.stderr:
                with lock:
                    stderr_lines.append(line)
                    last_activity = time.monotonic()
                    stripped = line.strip()
                    if stripped:
                        last_progress_line = stripped
                    if last_activity - last_heartbeat >= heartbeat_seconds:
                        last_heartbeat = last_activity
                        self.logger.info("FFmpeg still running... %s", last_progress_line)

        reader_thread = threading.Thread(target=_reader, daemon=True)
        reader_thread.start()

        while True:
            returncode = process.poll()
            if returncode is not None:
                break

            with lock:
                idle_seconds = time.monotonic() - last_activity
            if idle_seconds >= timeout_seconds:
                process.kill()
                process.wait()
                raise subprocess.TimeoutExpired(cmd, timeout_seconds)

            time.sleep(min(1.0, max(0.1, timeout_seconds / 100.0)))

        reader_thread.join(timeout=5.0)
        with lock:
            stderr_text = "".join(stderr_lines) or "unknown error"
        return returncode, stderr_text
