"""Read-only technical checks before a finished video is published."""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
from pathlib import Path
import subprocess
from typing import Any, Dict, Optional, Tuple

__all__ = [
    "MediaFacts",
    "PlatformCompatibility",
    "PrepublishIssue",
    "PrepublishReport",
    "inspect_for_publish",
    "prepublish_report_to_dict",
]

_logger = logging.getLogger(__name__)
_MIN_SHORT_EDGE_PX = 720
_MIN_RECOMMENDED_FPS = 24.0
_MAX_RECOMMENDED_FPS = 60.0
_PORTRAIT_RATIO = 9 / 16
_LANDSCAPE_RATIO = 16 / 9
_RATIO_TOLERANCE = 0.04


@dataclass(frozen=True)
class MediaFacts:
    """Technical facts read from the rendered media container."""

    width: int
    height: int
    duration: float
    fps: float
    video_codec: str
    pixel_format: str
    has_audio: bool
    audio_codec: Optional[str]
    size_bytes: int

    @property
    def aspect_ratio(self) -> float:
        """Return width divided by height."""
        return self.width / self.height if self.height else 0.0


@dataclass(frozen=True)
class PrepublishIssue:
    """One actionable technical finding."""

    severity: str
    code: str
    message: str


@dataclass(frozen=True)
class PlatformCompatibility:
    """Whether the current frame shape suits a publishing surface."""

    platform: str
    format_name: str
    status: str
    message: str


@dataclass(frozen=True)
class PrepublishReport:
    """A read-only media report; it never changes the video."""

    ready: bool
    facts: MediaFacts
    issues: Tuple[PrepublishIssue, ...]
    platforms: Tuple[PlatformCompatibility, ...]


def _parse_fps(value: str) -> float:
    if not value or value == "0/0":
        return 0.0
    try:
        numerator, denominator = value.split("/", 1)
        return float(numerator) / float(denominator)
    except (ValueError, ZeroDivisionError):
        return 0.0


def _probe_media(video_path: Path) -> Dict[str, Any]:
    command = [
        "ffprobe", "-v", "error", "-show_streams", "-show_format",
        "-of", "json", str(video_path),
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)
    if result.returncode != 0:
        raise RuntimeError("Không đọc được thông tin kỹ thuật của video bằng ffprobe")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("ffprobe trả về dữ liệu không hợp lệ") from exc


def _facts_from_probe(video_path: Path, probe: Dict[str, Any]) -> MediaFacts:
    streams = probe.get("streams") or []
    video = next((item for item in streams if item.get("codec_type") == "video"), None)
    audio = next((item for item in streams if item.get("codec_type") == "audio"), None)
    if not video:
        raise RuntimeError("File không có luồng hình ảnh")
    format_data = probe.get("format") or {}
    return MediaFacts(
        width=int(video.get("width") or 0),
        height=int(video.get("height") or 0),
        duration=float(format_data.get("duration") or video.get("duration") or 0.0),
        fps=_parse_fps(str(video.get("avg_frame_rate") or video.get("r_frame_rate") or "0/0")),
        video_codec=str(video.get("codec_name") or "unknown"),
        pixel_format=str(video.get("pix_fmt") or "unknown"),
        has_audio=audio is not None,
        audio_codec=str(audio.get("codec_name")) if audio and audio.get("codec_name") else None,
        size_bytes=video_path.stat().st_size,
    )


def _near(value: float, target: float) -> bool:
    return abs(value - target) <= _RATIO_TOLERANCE


def _platform_compatibility(facts: MediaFacts) -> Tuple[PlatformCompatibility, ...]:
    ratio = facts.aspect_ratio
    portrait = _near(ratio, _PORTRAIT_RATIO)
    landscape = _near(ratio, _LANDSCAPE_RATIO)
    vertical_message = (
        "Khung 9:16 phù hợp; vẫn nên xem lại vùng an toàn của chữ và logo."
        if portrait else
        "Video không phải 9:16; nền tảng có thể thêm viền hoặc crop khi hiển thị dọc."
    )
    return (
        PlatformCompatibility("tiktok", "Video dọc", "ready" if portrait else "needs_adjustment", vertical_message),
        PlatformCompatibility("facebook", "Reels", "ready" if portrait else "needs_adjustment", vertical_message),
        PlatformCompatibility("youtube", "Shorts", "ready" if portrait else "needs_adjustment", vertical_message),
        PlatformCompatibility(
            "youtube", "Video ngang", "ready" if landscape else "needs_adjustment",
            "Khung 16:9 phù hợp video ngang." if landscape else "Không phải khung 16:9 tiêu chuẩn cho video ngang.",
        ),
    )


def inspect_for_publish(video_path: Path) -> PrepublishReport:
    """Inspect a finished video without modifying it or its job."""
    path = Path(video_path)
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(f"Video not found: {path}")
    facts = _facts_from_probe(path, _probe_media(path))
    issues = []
    if facts.width <= 0 or facts.height <= 0:
        issues.append(PrepublishIssue("error", "invalid_dimensions", "Video có kích thước khung hình không hợp lệ."))
    elif min(facts.width, facts.height) < _MIN_SHORT_EDGE_PX:
        issues.append(PrepublishIssue(
            "warning", "low_resolution",
            f"Cạnh ngắn chỉ có {min(facts.width, facts.height)}px; hình có thể mờ sau khi nền tảng nén lại.",
        ))
    if not facts.has_audio:
        issues.append(PrepublishIssue("error", "missing_audio", "Video không có luồng âm thanh."))
    if facts.duration <= 0:
        issues.append(PrepublishIssue("error", "invalid_duration", "Không xác định được thời lượng video."))
    if facts.fps and not _MIN_RECOMMENDED_FPS <= facts.fps <= _MAX_RECOMMENDED_FPS:
        issues.append(PrepublishIssue(
            "warning", "unusual_fps",
            f"Tốc độ {facts.fps:.2f} FPS nằm ngoài khoảng phổ biến 24–60 FPS.",
        ))
    if facts.video_codec not in {"h264", "hevc", "av1"}:
        issues.append(PrepublishIssue(
            "warning", "uncommon_video_codec",
            f"Codec {facts.video_codec} có thể không tương thích tốt bằng H.264/HEVC.",
        ))
    if facts.pixel_format not in {"yuv420p", "yuv420p10le"}:
        issues.append(PrepublishIssue(
            "warning", "uncommon_pixel_format",
            f"Định dạng màu {facts.pixel_format} có thể bị chuyển đổi khi tải lên.",
        ))
    ready = not any(issue.severity == "error" for issue in issues)
    _logger.info("Prepublish inspection complete path=%s ready=%s issues=%d", path, ready, len(issues))
    return PrepublishReport(
        ready=ready,
        facts=facts,
        issues=tuple(issues),
        platforms=_platform_compatibility(facts),
    )


def prepublish_report_to_dict(report: PrepublishReport) -> dict:
    """Convert a report to a stable JSON-safe API response."""
    return {
        "ready": report.ready,
        "facts": {
            "width": report.facts.width,
            "height": report.facts.height,
            "duration": round(report.facts.duration, 3),
            "fps": round(report.facts.fps, 3),
            "video_codec": report.facts.video_codec,
            "pixel_format": report.facts.pixel_format,
            "has_audio": report.facts.has_audio,
            "audio_codec": report.facts.audio_codec,
            "size_bytes": report.facts.size_bytes,
            "aspect_ratio": round(report.facts.aspect_ratio, 4),
        },
        "issues": [issue.__dict__ for issue in report.issues],
        "platforms": [platform.__dict__ for platform in report.platforms],
    }
