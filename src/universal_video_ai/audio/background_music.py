from __future__ import annotations

from dataclasses import dataclass
import hashlib
import logging
from pathlib import Path
from typing import Optional, Tuple

__all__ = ["BackgroundMusicConfig", "BackgroundMusicLibrary"]

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BackgroundMusicConfig:
    """Configuration for a local library of properly licensed music."""

    library_dir: Optional[Path] = None
    extensions: Tuple[str, ...] = (".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg")


class BackgroundMusicLibrary:
    """Select a deterministic replacement track from a local music library.

    Files placed in this directory are assumed to have a licence that permits
    use on the target publishing platform. The selector deliberately does not
    download or label third-party music as copyright-safe.
    """

    def __init__(
        self,
        config: Optional[BackgroundMusicConfig] = None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.config = config or BackgroundMusicConfig()
        self.logger = logger or _logger

    def select(self, selection_key: str) -> Optional[Path]:
        """Return a stable track for ``selection_key``, or None if unavailable."""
        library_dir = self.config.library_dir
        if library_dir is None:
            self.logger.info("No licensed background-music directory configured")
            return None

        library_dir = Path(library_dir).resolve()
        if not library_dir.is_dir():
            self.logger.warning("Licensed background-music directory not found: %s", library_dir)
            return None

        allowed = {extension.lower() for extension in self.config.extensions}
        tracks = sorted(
            path for path in library_dir.iterdir()
            if path.is_file() and path.suffix.lower() in allowed
        )
        if not tracks:
            self.logger.warning("No supported licensed music found in %s", library_dir)
            return None

        digest = hashlib.sha256(selection_key.encode("utf-8")).digest()
        selected = tracks[int.from_bytes(digest[:8], "big") % len(tracks)]
        self.logger.info("Selected licensed background track: %s", selected)
        return selected
