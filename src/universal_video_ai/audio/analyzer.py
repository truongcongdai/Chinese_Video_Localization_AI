"""
Audio Analyzer for Smart Background Music Matching

Extracts audio features: tempo, key, mood, energy
for intelligent music selection.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


@dataclass
class AudioFeatures:
    """Extracted audio features for music matching."""
    tempo: float  # BPM
    key: str  # Musical key (e.g., "C", "C#", "D", etc.)
    mode: int  # 0 = minor, 1 = major
    energy: float  # 0.0 to 1.0
    danceability: float  # 0.0 to 1.0
    valence: float  # 0.0 to 1.0 (mood: negative to positive)
    duration: float  # seconds


class AudioAnalyzer:
    """Analyze audio files to extract features for music matching."""

    def __init__(self):
        self._librosa_available = False
        try:
            import librosa
            import numpy as np
            self.librosa = librosa
            self.np = np
            self._librosa_available = True
            logger.info("AudioAnalyzer: librosa available, full feature extraction enabled")
        except ImportError:
            logger.warning("AudioAnalyzer: librosa not available, using fallback mode")

    def analyze(self, audio_path: Path) -> Optional[AudioFeatures]:
        """
        Analyze audio file and extract features.

        Args:
            audio_path: Path to audio file

        Returns:
            AudioFeatures or None if analysis fails
        """
        if not audio_path.exists():
            logger.error(f"Audio file not found: {audio_path}")
            return None

        if self._librosa_available:
            return self._analyze_with_librosa(audio_path)
        else:
            return self._analyze_fallback(audio_path)

    def _analyze_with_librosa(self, audio_path: Path) -> Optional[AudioFeatures]:
        """Full feature extraction using librosa."""
        try:
            # Load audio
            y, sr = self.librosa.load(audio_path, sr=None)

            # Extract tempo
            tempo, _ = self.librosa.beat.beat_track(y=y, sr=sr)

            # Extract key and mode using chroma
            chroma = self.librosa.feature.chroma_stft(y=y, sr=sr)
            key, mode = self._estimate_key(chroma)

            # Extract energy (RMS)
            rms = self.librosa.feature.rms(y=y)[0]
            energy = float(self.np.mean(rms))

            # Estimate danceability based on beat consistency
            onset_env = self.librosa.onset.onset_strength(y=y, sr=sr)
            tempogram = self.librosa.feature.tempogram(onset_envelope=onset_env, sr=sr)
            danceability = float(self.np.mean(tempogram))

            # Estimate valence (mood) from spectral features
            spectral_centroid = self.librosa.feature.spectral_centroid(y=y, sr=sr)[0]
            spectral_rolloff = self.librosa.feature.spectral_rolloff(y=y, sr=sr)[0]
            valence = float(self.np.mean(spectral_centroid) / self.np.mean(spectral_rolloff + 1e-6))
            valence = max(0.0, min(1.0, valence))  # Clamp to [0, 1]

            duration = float(self.librosa.get_duration(y=y, sr=sr))

            return AudioFeatures(
                tempo=float(tempo),
                key=key,
                mode=int(mode),
                energy=energy,
                danceability=danceability,
                valence=valence,
                duration=duration,
            )
        except Exception as e:
            logger.error(f"Librosa analysis failed for {audio_path}: {e}")
            return self._analyze_fallback(audio_path)

    def _analyze_fallback(self, audio_path: Path) -> Optional[AudioFeatures]:
        """Fallback analysis with basic metadata only."""
        try:
            import mutagen
            from mutagen import File

            audio_file = File(audio_path)
            if audio_file is None:
                return None

            # Get duration
            duration = audio_file.info.length if hasattr(audio_file, 'info') else 0.0

            # Default values for features we can't extract
            return AudioFeatures(
                tempo=120.0,  # Default BPM
                key="C",  # Default key
                mode=1,  # Major
                energy=0.5,
                danceability=0.5,
                valence=0.5,
                duration=duration,
            )
        except ImportError:
            # Even more basic fallback
            return AudioFeatures(
                tempo=120.0,
                key="C",
                mode=1,
                energy=0.5,
                danceability=0.5,
                valence=0.5,
                duration=0.0,
            )
        except Exception as e:
            logger.error(f"Fallback analysis failed for {audio_path}: {e}")
            return None

    def _estimate_key(self, chroma) -> tuple[str, int]:
        """
        Estimate musical key from chroma features.

        Returns:
            (key, mode) where key is "C", "C#", "D", etc. and mode is 0 (minor) or 1 (major)
        """
        # Average chroma across time
        chroma_mean = self.np.mean(chroma, axis=1)

        # Map chroma bins to keys (using circle of fifths)
        keys = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
        key_idx = int(self.np.argmax(chroma_mean))
        key = keys[key_idx]

        # Simple mode detection based on major/minor scale patterns
        # Major: stronger on 0, 4, 7 (I, IV, V chords)
        # Minor: stronger on 0, 3, 7 (i, III, v chords)
        major_strength = chroma_mean[0] + chroma_mean[4 % 12] + chroma_mean[7 % 12]
        minor_strength = chroma_mean[0] + chroma_mean[3 % 12] + chroma_mean[7 % 12]
        mode = 1 if major_strength > minor_strength else 0

        return key, mode

    def calculate_similarity(
        self,
        features1: AudioFeatures,
        features2: AudioFeatures,
        weights: Optional[Dict[str, float]] = None,
    ) -> float:
        """
        Calculate similarity score between two audio feature sets.

        Args:
            features1: First audio features
            features2: Second audio features
            weights: Optional weights for each feature (default: equal weights)

        Returns:
            Similarity score between 0.0 and 1.0
        """
        if weights is None:
            weights = {
                "tempo": 0.3,
                "key": 0.2,
                "energy": 0.2,
                "danceability": 0.15,
                "valence": 0.15,
            }

        # Tempo similarity (within ±10% is good match)
        tempo_diff = abs(features1.tempo - features2.tempo) / features1.tempo
        tempo_sim = max(0.0, 1.0 - tempo_diff * 10)  # Penalize differences >10%

        # Key similarity (same key = 1.0, different = 0.0)
        key_sim = 1.0 if features1.key == features2.key and features1.mode == features2.mode else 0.5

        # Energy similarity
        energy_sim = 1.0 - abs(features1.energy - features2.energy)

        # Danceability similarity
        dance_sim = 1.0 - abs(features1.danceability - features2.danceability)

        # Valence (mood) similarity
        valence_sim = 1.0 - abs(features1.valence - features2.valence)

        # Weighted average
        similarity = (
            weights["tempo"] * tempo_sim
            + weights["key"] * key_sim
            + weights["energy"] * energy_sim
            + weights["danceability"] * dance_sim
            + weights["valence"] * valence_sim
        )

        return float(similarity)
