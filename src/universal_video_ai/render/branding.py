"""Global, optional branding overlays shared by every video pipeline.

The visible layers are intentionally generated with FFmpeg expressions so they
work for arbitrary resolutions and durations without hard-coded coordinates.
A best-effort forensic fingerprint is also written into container metadata and
a sidecar manifest.  This raises the cost of casual re-uploading, but it is not
claimed to be impossible to remove.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Dict, List, Optional
import hashlib
import json
import logging
import os
import shutil
import subprocess

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BrandingConfig:
    enabled: bool = False
    text: str = ""
    preset: str = "balanced"  # subtle | balanced | strong
    edge_runner_enabled: bool = True
    diagonal_enabled: bool = True
    pattern_enabled: bool = True
    fingerprint_enabled: bool = True
    avoid_subtitles: bool = True
    avoid_center: bool = True
    font_path: Optional[str] = None
    opacity: Optional[float] = None
    pattern_opacity: Optional[float] = None
    speed_px_per_second: Optional[float] = None
    font_size_ratio: Optional[float] = None
    fingerprint_id: Optional[str] = None

    @classmethod
    def from_dict(cls, raw: Optional[Dict[str, Any]]) -> "BrandingConfig":
        if not raw:
            return cls()
        allowed = {field for field in cls.__dataclass_fields__}
        values = {key: value for key, value in raw.items() if key in allowed}
        config = cls(**values)
        return config.normalized()

    def normalized(self) -> "BrandingConfig":
        preset = str(self.preset or "balanced").strip().lower()
        if preset not in {"subtle", "balanced", "strong"}:
            preset = "balanced"
        text = " ".join(str(self.text or "").split())[:80]
        opacity = None if self.opacity is None else max(0.03, min(0.80, float(self.opacity)))
        pattern_opacity = (
            None if self.pattern_opacity is None
            else max(0.01, min(0.30, float(self.pattern_opacity)))
        )
        speed = (
            None if self.speed_px_per_second is None
            else max(20.0, min(400.0, float(self.speed_px_per_second)))
        )
        ratio = (
            None if self.font_size_ratio is None
            else max(0.012, min(0.065, float(self.font_size_ratio)))
        )
        return replace(
            self,
            enabled=bool(self.enabled and text),
            text=text,
            preset=preset,
            opacity=opacity,
            pattern_opacity=pattern_opacity,
            speed_px_per_second=speed,
            font_size_ratio=ratio,
        )

    def with_fingerprint_context(self, *parts: object) -> "BrandingConfig":
        config = self.normalized()
        if not config.enabled or not config.fingerprint_enabled or config.fingerprint_id:
            return config
        return replace(config, fingerprint_id=make_fingerprint_id(config.text, *parts))

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self.normalized())


_PRESETS = {
    "subtle": {
        "opacity": 0.22,
        "pattern_opacity": 0.040,
        "speed": 85.0,
        "font_ratio": 0.023,
        "diagonal_opacity": 0.12,
        "diagonal_period": 7.2,
        "diagonal_duration": 1.55,
    },
    "balanced": {
        "opacity": 0.32,
        "pattern_opacity": 0.070,
        "speed": 110.0,
        "font_ratio": 0.028,
        "diagonal_opacity": 0.16,
        "diagonal_period": 6.0,
        "diagonal_duration": 1.85,
    },
    "strong": {
        "opacity": 0.46,
        "pattern_opacity": 0.115,
        "speed": 145.0,
        "font_ratio": 0.034,
        "diagonal_opacity": 0.22,
        "diagonal_period": 4.9,
        "diagonal_duration": 2.10,
    },
}


def make_fingerprint_id(*parts: object) -> str:
    payload = "\x1f".join(str(part or "") for part in parts)
    return hashlib.sha256(payload.encode("utf-8", errors="replace")).hexdigest()[:20]


def _escape_drawtext_text(text: str) -> str:
    text = text.replace("\\", "\\\\")
    for char in (":", "%", ",", ";", "[", "]", "="):
        text = text.replace(char, f"\\{char}")
    return text.replace("'", "’").replace("\n", " ")


def _escape_filter_path(path: str) -> str:
    # Forward slashes are accepted by FFmpeg on Windows and avoid the second
    # layer of backslash escaping required by filtergraphs. Drive-letter
    # colons still need escaping (C\:/...).
    normalized = path.replace("\\", "/")
    return normalized.replace(":", "\\:").replace("'", "’")


def resolve_font_path(configured: Optional[str] = None) -> Optional[str]:
    candidates = [
        configured,
        os.getenv("BRANDING_FONT_PATH"),
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
        "/usr/share/fonts/opentype/noto/NotoSans-Bold.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
        "C:/Windows/Fonts/arial.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).expanduser().is_file():
            return str(Path(candidate).expanduser().resolve())
    return None


def _drawtext_base(
    text: str,
    font_size: int,
    opacity: float,
    font_path: Optional[str],
    *,
    border_width: int,
) -> str:
    escaped = _escape_drawtext_text(text)
    font = (
        f"fontfile='{_escape_filter_path(font_path)}':"
        if font_path else "font='Sans':"
    )
    return (
        f"drawtext={font}text='{escaped}':fontsize={font_size}:"
        f"fontcolor=white@{opacity:.3f}:borderw={border_width}:"
        f"bordercolor=black@{min(0.75, opacity + 0.25):.3f}:"
        "shadowx=1:shadowy=1:shadowcolor=black@0.35"
    )


def build_branding_filters(
    raw_config: Optional[BrandingConfig],
    frame_width: int,
    frame_height: int,
) -> List[str]:
    """Return linear FFmpeg filters for the visible branding layers."""
    config = (raw_config or BrandingConfig()).normalized()
    if not config.enabled or frame_width <= 0 or frame_height <= 0:
        return []

    preset = _PRESETS[config.preset]
    opacity = config.opacity if config.opacity is not None else preset["opacity"]
    pattern_opacity = (
        config.pattern_opacity
        if config.pattern_opacity is not None else preset["pattern_opacity"]
    )
    speed = (
        config.speed_px_per_second
        if config.speed_px_per_second is not None else preset["speed"]
    )
    ratio = config.font_size_ratio if config.font_size_ratio is not None else preset["font_ratio"]
    base_dimension = min(frame_width, frame_height)
    font_size = max(16, min(72, round(base_dimension * ratio)))
    margin = max(8, round(base_dimension * 0.014))
    # Keep even unusually long channel names inside the frame. The estimate
    # is deliberately conservative for bold Unicode fonts; normal short
    # handles retain the preset size.
    estimated_char_width = 0.66
    max_runner_size = int(
        max(12, (frame_width - 2 * margin) / max(1.0, len(config.text) * estimated_char_width))
    )
    font_size = min(font_size, max_runner_size)
    # On vertical short-form videos, subtitles commonly occupy the lower
    # quarter. Route the runner above that area; subtitle filters are added
    # after branding as a second safety layer.
    subtitle_guard = round(frame_height * (0.22 if config.avoid_subtitles else 0.02))
    font_path = resolve_font_path(config.font_path)
    filters: List[str] = []

    if config.edge_runner_enabled:
        # A single text instance traverses a rectangle: top -> right -> bottom
        # -> left.  All geometry is derived from the actual frame and text
        # dimensions, so this works on 9:16, 16:9 and square outputs.
        a = f"(w-text_w-{2 * margin})"
        b = f"(h-text_h-{margin + subtitle_guard})"
        p = f"mod(t*{speed:.3f},2*{a}+2*{b})"
        x = (
            f"if(lt({p},{a}),{margin}+{p},"
            f"if(lt({p},{a}+{b}),w-text_w-{margin},"
            f"if(lt({p},2*{a}+{b}),w-text_w-{margin}-({p}-{a}-{b}),{margin})))"
        )
        y = (
            f"if(lt({p},{a}),{margin},"
            f"if(lt({p},{a}+{b}),{margin}+({p}-{a}),"
            f"if(lt({p},2*{a}+{b}),h-text_h-{subtitle_guard},"
            f"h-text_h-{subtitle_guard}-({p}-2*{a}-{b}))))"
        )
        filters.append(
            _drawtext_base(
                config.text, font_size, opacity, font_path,
                border_width=max(1, round(font_size / 22)),
            ) + f":x='{x}':y='{y}'"
        )

    if config.diagonal_enabled:
        # Intermittent diagonal sweeps make single-crop or static inpaint
        # removal harder without permanently blocking the centre of the frame.
        diagonal_opacity = min(0.55, preset.get("diagonal_opacity", opacity * 0.5))
        diagonal_period = max(3.6, float(preset.get("diagonal_period", 6.0)))
        diagonal_duration = min(diagonal_period * 0.60, float(preset.get("diagonal_duration", 1.8)))
        diagonal_size = max(14, round(font_size * 0.90))
        top_start = margin + round(frame_height * 0.05)
        top_end = margin + round(frame_height * (0.27 if config.avoid_center else 0.38))
        bottom_start = margin + round(frame_height * (0.12 if config.avoid_center else 0.05))
        bottom_end = max(
            top_end + 8,
            frame_height - subtitle_guard - margin - round(frame_height * (0.20 if config.avoid_center else 0.10)),
        )
        diagonal_specs = [
            (0.0, "ltr", top_start, bottom_end),
            (diagonal_period / 2.0, "rtl", bottom_start, top_end),
        ]
        for phase, direction, y_start, y_end in diagonal_specs:
            progress = f"(mod(t+{phase:.3f},{diagonal_period:.3f})/{diagonal_duration:.3f})"
            x_expr = (
                f"-text_w+(w+text_w+{2 * margin})*{progress}"
                if direction == "ltr" else
                f"w+{margin}-(w+text_w+{2 * margin})*{progress}"
            )
            y_expr = f"{y_start}+({y_end}-{y_start})*{progress}"
            enable_expr = f"lt(mod(t+{phase:.3f},{diagonal_period:.3f}),{diagonal_duration:.3f})"
            filters.append(
                _drawtext_base(
                    config.text, diagonal_size, diagonal_opacity, font_path,
                    border_width=max(1, round(diagonal_size / 28)),
                ) + f":x='{x_expr}':y='{y_expr}':enable='{enable_expr}'"
            )

    if config.pattern_enabled:
        pattern_size = max(12, round(font_size * 0.66))
        # Keep the repeating pattern out of the bottom subtitle area and away
        # from the exact visual centre when requested. Slight phase-shifted
        # motion prevents one static inpaint mask from covering the full clip.
        positions = [
            (0.05, 0.12, 0.0),
            (0.58, 0.10, 1.4),
            (0.08, 0.34, 2.2),
            (0.66, 0.37, 3.1),
            (0.04, 0.58, 4.0),
            (0.60, 0.60, 5.0),
        ]
        if not config.avoid_center:
            positions.append((0.34, 0.48, 2.7))
        for x_ratio, y_ratio, phase in positions:
            x = f"min(max(0,w*{x_ratio:.3f}+7*sin(t*0.31+{phase:.2f})),w-text_w)"
            y = f"min(max(0,h*{y_ratio:.3f}+5*cos(t*0.27+{phase:.2f})),h-text_h-{subtitle_guard})"
            filters.append(
                _drawtext_base(
                    config.text, pattern_size, pattern_opacity, font_path,
                    border_width=0,
                ) + f":x='{x}':y='{y}'"
            )

    if config.fingerprint_enabled and config.fingerprint_id:
        # This visual mark is intentionally near-invisible and complements,
        # rather than replaces, metadata/manifest fingerprinting.
        fp_size = max(8, round(font_size * 0.30))
        fp_text = f"wm:{config.fingerprint_id[:12]}"
        filters.append(
            _drawtext_base(fp_text, fp_size, 0.018, font_path, border_width=0)
            + ":x='mod(t*19,w-text_w)':y='h*0.46+9*sin(t*0.19)'"
        )

    return filters


def fingerprint_comment(config: Optional[BrandingConfig]) -> Optional[str]:
    config = (config or BrandingConfig()).normalized()
    if not config.enabled or not config.fingerprint_enabled or not config.fingerprint_id:
        return None
    return f"UVAI-BRANDING:{config.fingerprint_id}"


def stamp_fingerprint_metadata(video_path: Path, config: Optional[BrandingConfig], logger=None) -> None:
    """Best-effort lossless MP4 metadata remux after all transforms finish."""
    logger = logger or _logger
    comment = fingerprint_comment(config)
    video_path = Path(video_path)
    if not comment or not video_path.is_file() or shutil.which("ffmpeg") is None:
        return
    temp_path = video_path.with_name(f".{video_path.stem}.branding-meta{video_path.suffix}")
    cmd = [
        "ffmpeg", "-y", "-i", str(video_path), "-map", "0", "-c", "copy",
        "-metadata", f"comment={comment}",
        "-metadata", f"description={comment}",
        "-movflags", "+faststart", str(temp_path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        if result.returncode != 0 or not temp_path.is_file():
            logger.warning("Could not stamp branding metadata: %s", result.stderr[-1200:])
            temp_path.unlink(missing_ok=True)
            return
        os.replace(temp_path, video_path)
    except Exception as exc:
        logger.warning("Could not stamp branding metadata: %s", exc)
        temp_path.unlink(missing_ok=True)


def write_branding_manifest(
    output_path: Path,
    config: Optional[BrandingConfig],
    *,
    source_path: Optional[Path] = None,
    logger=None,
) -> Optional[Path]:
    logger = logger or _logger
    config = (config or BrandingConfig()).normalized()
    if not config.enabled:
        return None
    output_path = Path(output_path)
    manifest_path = output_path.with_suffix(output_path.suffix + ".branding.json")
    payload = {
        "schema_version": 1,
        "branding_applied": True,
        "config": config.to_dict(),
        "output_file": output_path.name,
        "source_file": Path(source_path).name if source_path else None,
        "output_sha256": None,
        "notice": (
            "Visible and forensic branding are deterrence/tracing aids; "
            "they cannot guarantee that determined editors cannot remove them."
        ),
    }
    try:
        digest = hashlib.sha256()
        with output_path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        payload["output_sha256"] = digest.hexdigest()
        manifest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return manifest_path
    except Exception as exc:
        logger.warning("Could not write branding manifest for %s: %s", output_path, exc)
        return None
