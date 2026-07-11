cat > src/universal_video_ai/render/renderer.py << 'RENDERER_EOF'
# src/universal_video_ai/render/renderer.py
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import logging
import shutil
import subprocess
import time
from typing import List, Optional

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
        timeout_seconds: ffmpeg timeout for rendering.
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
    timeout_seconds: int = 1800  # 30 minutes
    blur_text: bool = False
    blur_box: Optional[str] = None
    default_overlay_font_path: Optional[str] = None


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

    def _build_text_overlay_filters(self, overlays: List[TextOverlay]) -> List[str]:
        """
        Build one `drawbox` (cover the original text) + one `drawtext` (draw
        the translated text) filter pair per overlay, each gated to only be
        active during that sentence's `[start, end)` window via ffmpeg's
        `enable='between(t,start,end)'` expression. This is what makes the
        cover box and translated caption appear/disappear in sync with the
        original on-screen text instead of covering the whole video.
        """
        filters: List[str] = []
        for overlay in overlays:
            enable_expr = f"between(t\\,{overlay.start:.3f}\\,{overlay.end:.3f})"

            filters.append(
                f"drawbox=x={overlay.x}:y={overlay.y}:w={overlay.width}:h={overlay.height}"
                f":color={overlay.box_color}@1.0:t=fill:enable='{enable_expr}'"
            )

            font_path = overlay.font_path or self.config.default_overlay_font_path
            font_clause = f"fontfile='{font_path}':" if font_path else ""
            font_size = max(12, int(overlay.height * 0.6))
            text_y = overlay.y + max(0, (overlay.height - font_size) // 2)

            filters.append(
                "drawtext="
                f"{font_clause}"
                f"text='{_escape_drawtext(overlay.text)}':"
                f"x={overlay.x + 4}:y={text_y}:"
                f"fontsize={font_size}:fontcolor={overlay.font_color}:"
                f"enable='{enable_expr}'"
            )
        return filters

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

        text_overlays = text_overlays or []

        # Determine if we need video filters (subtitles, overlays, or blur)
        needs_reencode = subtitles is not None or self.config.blur_text or bool(text_overlays)

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
                    # Use custom blur box coordinates with crop+blur+overlay
                    # Format: x:y:w:h
                    blur_filter = f"crop={self.config.blur_box},boxblur=10:1,overlay={self.config.blur_box.split(':')[0]}:{self.config.blur_box.split(':')[1]}"
                    filters.append(blur_filter)
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
            filters.extend(self._build_text_overlay_filters(text_overlays))

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
                filters.append(f"subtitles={_escape_filter_path(str(subtitles))}")

            # Combine filters with comma
            vf = ",".join(filters) if filters else None

            cmd.extend(
                [
                    "-map", "0:v",
                    "-map", "1:a",
                ]
            )

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
            self.logger.exception("FFmpeg rendering timed out after %s seconds", self.config.timeout_seconds)
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
        visibly alive, and still enforces `timeout_seconds` by killing the
        process if it runs over.

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
        last_progress_line = ""
        lock = threading.Lock()

        def _reader() -> None:
            nonlocal last_heartbeat, last_progress_line
            assert process.stderr is not None
            for line in process.stderr:
                with lock:
                    stderr_lines.append(line)
                    stripped = line.strip()
                    if stripped:
                        last_progress_line = stripped
                    now = time.monotonic()
                    if now - last_heartbeat >= heartbeat_seconds:
                        last_heartbeat = now
                        self.logger.info("FFmpeg still running... %s", last_progress_line)

        reader_thread = threading.Thread(target=_reader, daemon=True)
        reader_thread.start()

        try:
            returncode = process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            raise

        reader_thread.join(timeout=5.0)
        with lock:
            stderr_text = "".join(stderr_lines) or "unknown error"
        return returncode, stderr_text
RENDERER_EOF
