"""
Voice Cloning Module

Allows users to upload voice samples and generate TTS with cloned voices.
Basic implementation using voice profile matching.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


@dataclass
class VoiceProfile:
    """Voice profile created from sample audio."""
    id: str
    user_id: int
    name: str
    sample_path: str
    language: str
    gender: Optional[str] = None
    created_at: float = 0
    is_active: bool = True


class VoiceCloner:
    """Basic voice cloning using voice profile matching."""

    def __init__(self, storage_dir: Optional[Path] = None):
        self.storage_dir = storage_dir or Path("./local_data/voice_samples")
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.profiles: Dict[str, VoiceProfile] = {}

    def create_profile(
        self,
        user_id: int,
        name: str,
        sample_audio_path: Path,
        language: str = "vi",
        gender: Optional[str] = None,
    ) -> VoiceProfile:
        """
        Create a voice profile from a sample audio file.

        Args:
            user_id: User ID
            name: Profile name
            sample_audio_path: Path to sample audio file
            language: Language code
            gender: Optional gender (male/female)

        Returns:
            Created VoiceProfile
        """
        import time
        import shutil

        profile_id = uuid.uuid4().hex[:12]
        sample_filename = f"{user_id}_{profile_id}{sample_audio_path.suffix}"
        sample_dest = self.storage_dir / sample_filename

        # Copy sample to storage
        shutil.copy(sample_audio_path, sample_dest)

        # Analyze sample (basic implementation)
        gender = gender or self._detect_gender(sample_dest)

        profile = VoiceProfile(
            id=profile_id,
            user_id=user_id,
            name=name,
            sample_path=str(sample_dest),
            language=language,
            gender=gender,
            created_at=time.time(),
            is_active=True,
        )

        self.profiles[profile_id] = profile
        logger.info(f"Created voice profile: {name} ({profile_id})")
        return profile

    def _detect_gender(self, audio_path: Path) -> str:
        """
        Detect gender from audio sample (basic implementation).

        Returns "male" or "female" based on pitch analysis.
        """
        try:
            import librosa
            import numpy as np

            y, sr = librosa.load(audio_path, sr=None)

            # Extract pitch using librosa
            pitches, magnitudes = librosa.piptrack(y=y, sr=sr)
            pitch_values = []
            for i in range(pitches.shape[1]):
                index = magnitudes[:, i].argmax()
                pitch = pitches[index, i]
                if pitch > 0:
                    pitch_values.append(pitch)

            if pitch_values:
                avg_pitch = np.mean(pitch_values)
                # Male: ~85-180 Hz, Female: ~165-255 Hz
                return "female" if avg_pitch > 165 else "male"
        except ImportError:
            pass
        except Exception as e:
            logger.warning(f"Gender detection failed: {e}")

        return "unknown"

    def get_profile(self, profile_id: str) -> Optional[VoiceProfile]:
        """Get voice profile by ID."""
        return self.profiles.get(profile_id)

    def get_user_profiles(self, user_id: int) -> list[VoiceProfile]:
        """Get all voice profiles for a user."""
        return [p for p in self.profiles.values() if p.user_id == user_id and p.is_active]

    def delete_profile(self, profile_id: str, user_id: int) -> bool:
        """Delete a voice profile."""
        profile = self.profiles.get(profile_id)
        if profile and profile.user_id == user_id:
            profile.is_active = False
            # Optionally delete the file
            try:
                Path(profile.sample_path).unlink(missing_ok=True)
            except:
                pass
            return True
        return False

    def match_tts_voice(self, profile_id: str, target_language: str) -> Optional[str]:
        """
        Find the best matching TTS voice for a voice profile.

        Args:
            profile_id: Voice profile ID
            target_language: Target language code

        Returns:
            TTS voice ID or None
        """
        profile = self.get_profile(profile_id)
        if not profile:
            return None

        # Simple matching based on language and gender
        # This is a basic implementation - can be enhanced with ML
        from universal_video_ai.tts.tts import DEFAULT_VOICES_BY_LANGUAGE

        voices = DEFAULT_VOICES_BY_LANGUAGE.get(target_language, [])

        # Try to match gender
        if profile.gender and profile.gender != "unknown":
            gender_voices = [v for v in voices if profile.gender in v.lower()]
            if gender_voices:
                return gender_voices[0]

        # Fallback to first available voice
        return voices[0] if voices else None

    def get_profile_features(self, profile_id: str) -> Optional[Dict[str, Any]]:
        """
        Extract voice features from a profile.

        Returns basic features for ML-based matching.
        """
        profile = self.get_profile(profile_id)
        if not profile:
            return None

        try:
            import librosa
            import numpy as np

            y, sr = librosa.load(profile.sample_path, sr=None)

            # Extract features
            pitches, magnitudes = librosa.piptrack(y=y, sr=sr)
            pitch_values = []
            for i in range(pitches.shape[1]):
                index = magnitudes[:, i].argmax()
                pitch = pitches[index, i]
                if pitch > 0:
                    pitch_values.append(pitch)

            features = {
                "avg_pitch": float(np.mean(pitch_values)) if pitch_values else 0,
                "std_pitch": float(np.std(pitch_values)) if pitch_values else 0,
                "duration": float(librosa.get_duration(y=y, sr=sr)),
                "gender": profile.gender,
                "language": profile.language,
            }

            return features
        except Exception as e:
            logger.warning(f"Failed to extract features: {e}")
            return None
