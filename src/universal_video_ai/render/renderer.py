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

__all__ = ["Renderer", "RenderConfig"]

_logger = logging.getLogger(__name__)


def _check_ffmpeg_available() -> bool:
    """
    Check whether ffmpeg binary is available in PATH.
    """
    return shutil.which("ffmpeg") is not None


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
        blur_box: coordinates for blur box as "x:y:w:h" (e.g., "0:0:1920:100" for top bar).
                  If None, defaults to covering bottom 15% of video.
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


class Renderer:
    """
    Combine video and audio into a final video file and optionally burn subtitles.

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

    def _build_command(self, input_video: Path, input_audio: Path, output: Path, subtitles: Optional[Path] = None) -> List[str]:
        """
        Build ffmpeg command list based on configuration and presence of subtitles.

        Strategy:
        - If subtitles or blur_text enabled: we need to re-encode video (apply -vf filters)
        - Otherwise: use stream copy for video (-c:v copy) and encode audio to target codec
                     (faster).
        """
        cmd: List[str] = ["ffmpeg"]

        if self.config.overwrite:
            cmd.append("-y")

        # Inputs
        cmd.extend(["-i", str(input_video)])
        cmd.extend(["-i", str(input_audio)])

        # Determine if we need video filters (subtitles or blur)
        needs_reencode = subtitles is not None or self.config.blur_text

        if needs_reencode:
            # Build video filter chain
            filters = []
            
            # Add blur filter if enabled
            if self.config.blur_text:
                if self.config.blur_box:
                    # Use custom blur box coordinates with crop+blur+overlay
                    # Format: x:y:w:h
                    blur_filter = f"crop={self.config.blur_box},boxblur=10:1,overlay={self.config.blur_box.split(':')[0]}:{self.config.blur_box.split(':')[1]}"
                else:
                    # Simple global blur (temporary fix - will blur entire video)
                    # TODO: Implement region-specific blur with crop+overlay
                    blur_filter = "boxblur=5:1"
                filters.append(blur_filter)
            
            # Add subtitles filter if provided
            if subtitles:
                filters.append(f"subtitles={str(subtitles)}")
            
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
    ) -> Path:
        """
        Render the final video.

        :param video_path: Path to original video file.
        :param audio_path: Path to audio file (e.g., TTS result).
        :param subtitles: Optional path to subtitles file (SRT). If provided, subtitles will be burned in.
        :param output_path: Optional target output path. If None, use default location.
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
        cmd = self._build_command(video_path, audio_path, output, subtitles)

        self.logger.info("Rendering output %s from video=%s audio=%s subtitles=%s", output, video_path, audio_path, subtitles)
        self.logger.debug("FFmpeg command: %s", " ".join(cmd))

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=self.config.timeout_seconds)

            if result.returncode != 0:
                err = result.stderr or result.stdout or "unknown error"
                self.logger.error("FFmpeg returned non-zero exit code: %s", err)
                raise RuntimeError(f"FFmpeg failed: {err}")

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