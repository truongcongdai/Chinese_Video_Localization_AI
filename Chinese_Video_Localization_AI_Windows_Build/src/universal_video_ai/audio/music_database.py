"""
Music Database for Smart Background Music Matching

Manages music library with metadata for intelligent selection.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import List, Optional, Dict, Any

from .analyzer import AudioAnalyzer, AudioFeatures

logger = logging.getLogger(__name__)


@dataclass
class MusicTrack:
    """Music track with metadata."""
    id: str
    name: str
    file_path: str
    duration: float
    features: Optional[AudioFeatures] = None
    tags: List[str] = None
    category: str = "general"  # cinematic, upbeat, calm, etc.

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        d = asdict(self)
        if self.features:
            d["features"] = asdict(self.features)
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MusicTrack":
        """Create from dictionary."""
        features_data = data.pop("features", None)
        features = AudioFeatures(**features_data) if features_data else None
        return cls(features=features, **data)


class MusicDatabase:
    """Manages music library with metadata."""

    def __init__(self, metadata_path: Optional[Path] = None):
        self.metadata_path = metadata_path or Path("./local_data/music_metadata.json")
        self.tracks: Dict[str, MusicTrack] = {}
        self.analyzer = AudioAnalyzer()
        self._load_metadata()

    def _load_metadata(self) -> None:
        """Load music metadata from JSON file."""
        if self.metadata_path.exists():
            try:
                with open(self.metadata_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for track_data in data:
                    track = MusicTrack.from_dict(track_data)
                    self.tracks[track.id] = track
                logger.info(f"Loaded {len(self.tracks)} tracks from metadata")
            except Exception as e:
                logger.error(f"Failed to load music metadata: {e}")

    def _save_metadata(self) -> None:
        """Save music metadata to JSON file."""
        try:
            self.metadata_path.parent.mkdir(parents=True, exist_ok=True)
            data = [track.to_dict() for track in self.tracks.values()]
            with open(self.metadata_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            logger.info(f"Saved {len(self.tracks)} tracks to metadata")
        except Exception as e:
            logger.error(f"Failed to save music metadata: {e}")

    def add_track(
        self,
        file_path: Path,
        name: str,
        category: str = "general",
        tags: Optional[List[str]] = None,
        analyze: bool = True,
    ) -> MusicTrack:
        """
        Add a music track to the database.

        Args:
            file_path: Path to music file
            name: Track name
            category: Category (cinematic, upbeat, calm, etc.)
            tags: Optional tags
            analyze: Whether to analyze audio features

        Returns:
            Created MusicTrack
        """
        import uuid

        track_id = uuid.uuid4().hex[:12]

        # Analyze audio if requested
        features = None
        if analyze:
            features = self.analyzer.analyze(file_path)

        # Get duration from file if features not available
        duration = features.duration if features else 0.0
        if duration == 0.0:
            try:
                from mutagen import File
                audio_file = File(file_path)
                if audio_file:
                    duration = audio_file.info.length
            except:
                pass

        track = MusicTrack(
            id=track_id,
            name=name,
            file_path=str(file_path),
            duration=duration,
            features=features,
            tags=tags or [],
            category=category,
        )

        self.tracks[track_id] = track
        self._save_metadata()
        logger.info(f"Added track: {name} ({track_id})")
        return track

    def get_track(self, track_id: str) -> Optional[MusicTrack]:
        """Get track by ID."""
        return self.tracks.get(track_id)

    def get_tracks_by_category(self, category: str) -> List[MusicTrack]:
        """Get all tracks in a category."""
        return [t for t in self.tracks.values() if t.category == category]

    def find_best_match(
        self,
        target_features: AudioFeatures,
        category: Optional[str] = None,
        exclude_ids: Optional[List[str]] = None,
        min_similarity: float = 0.5,
    ) -> Optional[MusicTrack]:
        """
        Find the best matching track based on audio features.

        Args:
            target_features: Target audio features to match
            category: Optional category filter
            exclude_ids: Track IDs to exclude
            min_similarity: Minimum similarity score threshold

        Returns:
            Best matching MusicTrack or None
        """
        candidates = self.tracks.values()

        # Filter by category
        if category:
            candidates = [t for t in candidates if t.category == category]

        # Filter by exclude list
        if exclude_ids:
            candidates = [t for t in candidates if t.id not in exclude_ids]

        # Filter by features availability
        candidates = [t for t in candidates if t.features is not None]

        if not candidates:
            logger.warning("No candidates with features available for matching")
            return None

        # Calculate similarities
        best_track = None
        best_score = 0.0

        for track in candidates:
            similarity = self.analyzer.calculate_similarity(target_features, track.features)
            if similarity > best_score:
                best_score = similarity
                best_track = track

        if best_score >= min_similarity:
            logger.info(f"Best match: {best_track.name} (score: {best_score:.2f})")
            return best_track
        else:
            logger.warning(f"No track meets similarity threshold (best: {best_score:.2f})")
            return None

    def scan_directory(
        self,
        music_dir: Path,
        analyze: bool = True,
        category: str = "general",
    ) -> int:
        """
        Scan a directory and add all audio files to the database.

        Args:
            music_dir: Directory to scan
            analyze: Whether to analyze audio features
            category: Default category for scanned tracks

        Returns:
            Number of tracks added
        """
        audio_extensions = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"}
        added_count = 0

        for file_path in music_dir.rglob("*"):
            if file_path.suffix.lower() in audio_extensions:
                # Check if already in database
                existing = [t for t in self.tracks.values() if t.file_path == str(file_path)]
                if existing:
                    continue

                # Add track
                name = file_path.stem
                try:
                    self.add_track(file_path, name, category=category, analyze=analyze)
                    added_count += 1
                except Exception as e:
                    logger.error(f"Failed to add {file_path}: {e}")

        logger.info(f"Scanned {music_dir}: added {added_count} tracks")
        return added_count

    def list_all(self) -> List[MusicTrack]:
        """List all tracks."""
        return list(self.tracks.values())
