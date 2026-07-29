from __future__ import annotations

from dataclasses import dataclass
import hashlib
import logging
from pathlib import Path
from typing import Optional, Tuple

from universal_video_ai.audio.analyzer import AudioAnalyzer

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
        self._analyzer: Optional[AudioAnalyzer] = None

    def select(self, selection_key: str) -> Optional[Path]:
        """Return a stable track for ``selection_key``, or None if unavailable."""
        tracks = self._tracks()
        if not tracks:
            return None

        digest = hashlib.sha256(selection_key.encode("utf-8")).digest()
        selected = tracks[int.from_bytes(digest[:8], "big") % len(tracks)]
        self.logger.info("Selected licensed background track: %s", selected)
        return selected

    def select_like(self, reference_audio: Path, selection_key: str = "") -> Optional[Path]:
        """Return the licensed track closest to the reference audio's music feel.

        This never reuses the source video's audio. It only uses coarse audio
        features from the source as a matching signal for tracks already placed
        in the licensed local library.
        """
        tracks = self._tracks()
        if not tracks:
            return None

        try:
            analyzer = self._get_analyzer()
            reference_features = analyzer.analyze(Path(reference_audio))
            if reference_features is None:
                return self.select(selection_key or str(reference_audio))

            scored = []
            for track in tracks:
                features = analyzer.analyze(track)
                if features is None:
                    continue
                score = analyzer.calculate_similarity(reference_features, features)
                tie_break = hashlib.sha256(f"{selection_key}:{track.name}".encode("utf-8")).hexdigest()
                scored.append((score, tie_break, track))
            if not scored:
                return self.select(selection_key or str(reference_audio))
            selected = max(scored, key=lambda item: (item[0], item[1]))[2]
            self.logger.info("Selected licensed background track by audio match: %s", selected)
            return selected
        except Exception as exc:
            self.logger.warning("Background music matching failed; using stable fallback: %s", exc)
            return self.select(selection_key or str(reference_audio))

    def _get_analyzer(self) -> AudioAnalyzer:
        if self._analyzer is None:
            self._analyzer = AudioAnalyzer()
        return self._analyzer

    def _tracks(self) -> list[Path]:
        library_dir = self.config.library_dir
        if library_dir is None:
            self.logger.info("No licensed background-music directory configured")
            return []

        library_dir = Path(library_dir).resolve()
        if not library_dir.is_dir():
            self.logger.warning("Licensed background-music directory not found: %s", library_dir)
            return []

        allowed = {extension.lower() for extension in self.config.extensions}
        tracks = sorted(
            path for path in library_dir.iterdir()
            if path.is_file() and path.suffix.lower() in allowed
        )
        if not tracks:
            self.logger.warning("No supported licensed music found in %s", library_dir)
        return tracks
