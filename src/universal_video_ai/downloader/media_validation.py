"""Small, dependency-free validation for downloaded video containers."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Tuple


MIN_VIDEO_BYTES = 16 * 1024


def validate_video_file(path: Path) -> Tuple[bool, str]:
    """Return whether ``path`` is a non-trivial, probeable video container."""
    path = Path(path)
    if not path.is_file():
        return False, "file is missing"
    size = path.stat().st_size
    if size < MIN_VIDEO_BYTES:
        return False, f"file is too small ({size} bytes)"

    if shutil.which("ffprobe") is None:
        # Size validation still prevents HTML/placeholder responses from
        # becoming cache entries on minimal installations.
        return True, "ffprobe unavailable; size check passed"

    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "stream=codec_type:format=duration",
                "-of", "json", str(path),
            ],
            capture_output=True,
            text=False,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"ffprobe failed: {exc}"

    stderr = (result.stderr or b"")
    stderr_text = stderr.decode("utf-8", errors="replace") if isinstance(stderr, bytes) else str(stderr)
    if result.returncode != 0:
        return False, stderr_text.strip() or f"ffprobe exited {result.returncode}"
    stdout = (result.stdout or b"")
    stdout_text = stdout.decode("utf-8", errors="replace") if isinstance(stdout, bytes) else str(stdout)
    try:
        payload = json.loads(stdout_text or "{}")
    except json.JSONDecodeError as exc:
        return False, f"invalid ffprobe JSON: {exc}"
    if not any(stream.get("codec_type") == "video" for stream in payload.get("streams", [])):
        return False, "container has no video stream"
    return True, "ok"
