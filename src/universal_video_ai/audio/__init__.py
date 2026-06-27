"""
Public API for the audio package.

This module intentionally keeps imports minimal and import-safe:
- Exposes primary lightweight symbols eagerly.
- Attempts to import Demucs symbols and sets DEMUCS_AVAILABLE accordingly.
"""

from __future__ import annotations

from typing import Optional

import importlib
import logging

# Import lightweight, safe modules (these should not pull heavy external deps).
from .audio_result import AudioResult
from .extractor import AudioExtractor
from .ffprobe import FFprobeResult

logger = logging.getLogger(__name__)

# Demucs symbols: attempt to import but do not fail the package import if unavailable.
DEMUCS_AVAILABLE: bool = False
DemucsProcessor: Optional[type] = None
DemucsConfig: Optional[type] = None
DemucsOutput: Optional[type] = None

try:
    # local import; wrap to avoid raising ImportError on systems without demucs or heavy deps
    from .demucs import DemucsProcessor as _DemucsProcessor  # type: ignore
    from .demucs import DemucsConfig as _DemucsConfig  # type: ignore
    from .demucs import DemucsOutput as _DemucsOutput  # type: ignore

    DemucsProcessor = _DemucsProcessor  # rebind to public names
    DemucsConfig = _DemucsConfig
    DemucsOutput = _DemucsOutput
    DEMUCS_AVAILABLE = True
except Exception as exc:  # broad by intent: catch ImportError and other runtime issues
    logger.debug(
        "Demucs symbols not available from %s (%s). Set DEMUCS_AVAILABLE=False",
        __name__ + ".demucs",
        exc,
    )

__all__ = [
    "AudioExtractor",
    "AudioResult",
    "FFprobeResult",
    "DemucsProcessor",
    "DemucsConfig",
    "DemucsOutput",
    "DEMUCS_AVAILABLE",
]