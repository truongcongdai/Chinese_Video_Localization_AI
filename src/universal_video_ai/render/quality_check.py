# src/universal_video_ai/render/quality_check.py
"""
Best-effort automated sanity checks run on a finished render, so obviously
broken output (near-silent audio, wildly wrong duration) gets flagged to
the person instead of them only finding out after publishing it somewhere.

Deliberately narrow in scope — this is NOT lip-sync detection or a claim
of general "quality" scoring, just two concrete, cheaply-measurable things
that are common real failure modes in this pipeline (TTS/mix step produced
near-silent audio; render step somehow dropped/duplicated a big chunk of
the timeline). Returns human-readable Vietnamese warning strings; an empty
list means nothing suspicious was detected — not a guarantee the video is
good, just that these specific checks didn't fire.
"""
from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

# Below this mean volume (dBFS), a video is flagged as "quite quiet" — a
# properly mixed dub/voiceover track is normally well above this.
_QUIET_MEAN_DB_THRESHOLD = -35.0
# A video with essentially no measurable audio at all (near-total silence).
_SILENT_MEAN_DB_THRESHOLD = -50.0


def _get_duration(video_path: Path) -> Optional[float]:
    cmd = [
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(video_path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if result.returncode != 0:
            return None
        return float(result.stdout.strip())
    except Exception:
        return None


def _get_mean_volume_db(video_path: Path) -> Optional[float]:
    cmd = [
        "ffmpeg", "-i", str(video_path), "-af", "volumedetect",
        "-f", "null", "-",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except Exception:
        return None
    match = re.search(r"mean_volume:\s*(-?\d+\.?\d*)\s*dB", result.stderr)
    return float(match.group(1)) if match else None


def analyze_output_quality(
    video_path: Path,
    source_duration: Optional[float] = None,
) -> List[str]:
    """
    Run cheap post-render sanity checks on `video_path`.

    :param source_duration: the ORIGINAL source video's duration in
        seconds, if known — used to flag a suspiciously large mismatch
        (e.g. render silently truncated the video). None skips this check.
    :return: list of warning strings (Vietnamese, ready to show a user).
        Empty list = no warnings triggered.
    """
    video_path = Path(video_path)
    warnings: List[str] = []

    if not video_path.exists():
        return ["Không tìm thấy file video để kiểm tra chất lượng."]

    mean_db = _get_mean_volume_db(video_path)
    if mean_db is not None:
        if mean_db <= _SILENT_MEAN_DB_THRESHOLD:
            warnings.append(
                f"Âm thanh gần như im lặng (trung bình {mean_db:.1f} dB) — "
                "rất có thể bước lồng tiếng hoặc trộn âm đã bị lỗi."
            )
        elif mean_db <= _QUIET_MEAN_DB_THRESHOLD:
            warnings.append(
                f"Âm lượng khá nhỏ (trung bình {mean_db:.1f} dB) — nên nghe thử "
                "trước khi đăng, có thể cần tăng âm lượng."
            )

    if source_duration and source_duration > 0:
        final_duration = _get_duration(video_path)
        if final_duration is not None:
            diff = abs(final_duration - source_duration)
            tolerance = max(2.0, source_duration * 0.08)
            if diff > tolerance:
                warnings.append(
                    f"Thời lượng video sau khi xử lý ({final_duration:.1f}s) lệch khá "
                    f"nhiều so với video gốc ({source_duration:.1f}s) — nên xem trước để "
                    "chắc không bị mất đoạn hoặc lệch tiếng."
                )

    return warnings
