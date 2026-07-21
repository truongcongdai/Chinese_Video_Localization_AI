# src/universal_video_ai/render/renderer.py
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import logging
import shutil
import subprocess
import time
from typing import List, Optional, Tuple

from universal_video_ai.config import TEMP_DIR

__all__ = ["Renderer", "RenderConfig", "TextOverlay"]

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
    # Static region to permanently blur for the ENTIRE video, regardless of
    # blur_text/text_overlays — intended for a platform watermark (e.g. the
    # TikTok/Douyin logo + @username + reup title baked into the corner of
    # a downloaded source video), which is not a translatable subtitle and
    # shouldn't be treated like one. Expressed as (x0, y0, x1, y1) FRACTIONS
    # (0.0-1.0) of the frame so it works across differently-sized source
    # videos. Example: (0.80, 0.72, 1.0, 1.0) covers the bottom-right ~20%
    # width x ~28% height corner.
    watermark_box_fractional: Optional[Tuple[float, float, float, float]] = None

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
        self, overlays: List[TextOverlay], frame_w: Optional[int] = None
    ) -> List[str]:
        """
        Build one `drawbox` (cover the original text) + one `drawtext` (draw
        the translated text) filter pair per overlay, each gated to only be
        active during that sentence's `[start, end)` window via ffmpeg's
        `enable='between(t,start,end)'` expression. This is what makes the
        cover box and translated caption appear/disappear in sync with the
        original on-screen text instead of covering the whole video.

        The translated sentence is very often wider than the ORIGINAL
        on-screen text's OCR-detected box (e.g. Vietnamese needs more
        horizontal space per "word" than Chinese characters do), so sizing
        the box/font purely from the detected box's height — ignoring how
        long the translated string actually is — let long sentences spill
        text outside the white cover box or off the edge of the frame.
        Below, both the font size and the box width adapt to the text
        length: first try to fit at a height-based font size by widening
        the box (kept centered on the original detected box and clamped to
        the frame); only if that would make the box unreasonably wide do we
        shrink the font instead.
        """
        filters: List[str] = []
        # Average glyph width as a fraction of font size for a typical bold
        # sans-serif font (incl. Vietnamese diacritics/wide glyphs) — a
        # deliberately conservative estimate so text more often fits with
        # room to spare than overflows.
        avg_char_width_ratio = 0.58
        box_padding_x = 20  # px of breathing room inside the box, each side
        # Allow long translated OCR overlays to shrink enough to stay
        # inside the horizontal safe area instead of clipping off-screen.
        min_font_size = 8
        max_font_size = 64

        for overlay in overlays:
            enable_expr = f"between(t\\,{overlay.start:.3f}\\,{overlay.end:.3f})"

            font_path = overlay.font_path or self.config.default_overlay_font_path
            font_clause = f"fontfile='{font_path}':" if font_path else ""

            text_len = max(1, len(overlay.text))
            font_size = (
                overlay.font_size
                if overlay.font_size is not None
                else max(min_font_size, int(overlay.height * 0.6))
            )
            font_size = min(font_size, max_font_size)

            box_cx = overlay.x + overlay.width / 2.0
            # Don't let the cover box balloon past ~92% of the frame width
            # even for very long sentences — beyond that we shrink the font
            # instead so it still reads as a caption, not a banner.
            max_box_width = int(frame_w * 0.86) if frame_w else overlay.width * 4

            est_text_width = text_len * font_size * avg_char_width_ratio
            avail_width = overlay.width - 2 * box_padding_x

            if est_text_width > avail_width:
                needed_box_width = int(est_text_width + 2 * box_padding_x)
                box_width = min(needed_box_width, max_box_width)
                avail_at_box = box_width - 2 * box_padding_x
                est_text_width_at_box = text_len * font_size * avg_char_width_ratio
                if est_text_width_at_box > avail_at_box > 0:
                    # Even the widened (capped) box isn't enough — shrink
                    # the font until the estimated text width fits.
                    font_size = max(
                        min_font_size,
                        int(avail_at_box / (text_len * avg_char_width_ratio)),
                    )
            else:
                box_width = overlay.width

            box_x = int(box_cx - box_width / 2.0)
            if frame_w:
                box_x = max(0, min(box_x, frame_w - box_width))

            filters.append(
                f"drawbox=x={box_x}:y={overlay.y}:w={box_width}:h={overlay.height}"
                f":color={overlay.box_color}@1.0:t=fill:enable='{enable_expr}'"
            )

            # Center the translated text both horizontally and vertically
            # inside the (possibly widened) cover box. `text_w`/`text_h` are
            # ffmpeg drawtext's built-in expressions for the rendered text's
            # own pixel size, so this stays centered regardless of length.
            if overlay.text:
                filters.append(
                    "drawtext="
                    f"{font_clause}"
                    f"text='{_escape_drawtext(overlay.text)}':"
                    f"x={box_x}+({box_width}-text_w)/2:"
                    f"y={overlay.y}+({overlay.height}-text_h)/2:"
                    f"fontsize={font_size}:fontcolor={overlay.font_color}:"
                    f"enable='{enable_expr}'"
                )
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
        """Build an ffmpeg filter that blurs only the pixel region
        (x, y, w, h) for the whole video, leaving everything else sharp.

        IMPORTANT: ffmpeg's `crop` filter takes `w:h:x:y` (width, height,
        x, y) — NOT `x:y:w:h`. Passing our x/y/w/h straight through in the
        wrong order silently crops (and then blurs/overlays) the wrong
        region of the frame.
        """
        return f"crop={w}:{h}:{x}:{y},boxblur=10:1,overlay={x}:{y}"

    def _build_command(
        self,
        input_video: Path,
        input_audio: Path,
        output: Path,
        subtitles: Optional[Path] = None,
        text_overlays: Optional[List[TextOverlay]] = None,
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

        # Determine if we need video filters (subtitles, overlays, or blur)
        needs_reencode = (
            subtitles is not None
            or self.config.blur_text
            or bool(text_overlays)
            or self.config.watermark_box_fractional is not None
            or logo_configured
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
            filters = []

            frame_w: Optional[int] = None
            frame_h: Optional[int] = None
            if self.config.watermark_box_fractional is not None or text_overlays:
                dims = self._get_video_dimensions(input_video)
                if dims is not None:
                    frame_w, frame_h = dims

            # Permanently blur a platform watermark (logo/@username/reup
            # title), independent of blur_text/text_overlays — it's not a
            # translatable subtitle, it's baked into every single frame, so
            # it's covered for the whole video rather than per-sentence.
            if self.config.watermark_box_fractional is not None:
                if frame_w is None or frame_h is None:
                    self.logger.warning(
                        "watermark_box_fractional is set but video dimensions could not be "
                        "determined; skipping watermark cover for this render."
                    )
                else:
                    fx0, fy0, fx1, fy1 = self.config.watermark_box_fractional
                    wx, wy = int(fx0 * frame_w), int(fy0 * frame_h)
                    ww = max(2, int((fx1 - fx0) * frame_w))
                    wh = max(2, int((fy1 - fy0) * frame_h))
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
                    # blur_box is documented/entered as "x:y:w:h"; convert to
                    # the crop filter's actual w:h:x:y order (see
                    # _region_blur_filter docstring).
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
            filters.extend(self._build_text_overlay_filters(text_overlays, frame_w=frame_w))

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
                cmd.extend(["-map", "0:v"])
                vf = ",".join(filters) if filters else None
                if vf:
                    cmd.extend(["-vf", vf])

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
        cmd = self._build_command(video_path, audio_path, output, subtitles, text_overlays)

        self.logger.info(
            "Rendering output %s from video=%s audio=%s subtitles=%s text_overlays=%d",
            output, video_path, audio_path, subtitles, len(text_overlays or []),
        )
        self.logger.debug("FFmpeg command: %s", " ".join(cmd))

        try:
            returncode, stderr_text = self._run_ffmpeg_with_progress(cmd, self.config.timeout_seconds)

            if returncode != 0:
                self.logger.error("FFmpeg returned non-zero exit code: %s", stderr_text)
                raise RuntimeError(f"FFmpeg failed: {stderr_text}")

            if not output.exists():
                self.logger.error("FFmpeg completed but output file not created: %s", output)
                raise RuntimeError(f"FFmpeg did not create output file: {output}")

            self.logger.info("Rendering completed successfully: %s", output)
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

    def _run_ffmpeg_with_progress(self, cmd: List[str], timeout_seconds: int, heartbeat_seconds: float = 15.0) -> "tuple[int, str]":
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
