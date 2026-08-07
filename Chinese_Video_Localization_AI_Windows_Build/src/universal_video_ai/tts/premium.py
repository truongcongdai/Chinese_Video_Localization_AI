from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path
from typing import Optional

import requests

from .backend import TTSBackend
from .exceptions import SynthesisError

_logger = logging.getLogger(__name__)


OPENAI_BUILTIN_VOICES = [
    "alloy", "ash", "ballad", "coral", "echo", "fable",
    "onyx", "nova", "sage", "shimmer", "verse", "marin", "cedar",
]


def _normalize_voice(voice: Optional[str]) -> Optional[str]:
    if not voice:
        return None
    if ":" in voice:
        return voice.split(":", 1)[1]
    return voice


def _convert_to_wav(source_path: Path, output_path: Path) -> Path:
    if source_path == output_path and output_path.suffix.lower() == ".wav":
        return output_path
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        if source_path != output_path:
            shutil.move(str(source_path), str(output_path))
        return output_path
    output_path.unlink(missing_ok=True)
    result = subprocess.run(
        [
            ffmpeg,
            "-y",
            "-v", "error",
            "-i", str(source_path),
            "-ar", "44100",
            "-ac", "1",
            str(output_path),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    source_path.unlink(missing_ok=True)
    if result.returncode != 0:
        raise SynthesisError((result.stderr or "ffmpeg audio conversion failed").strip())
    return output_path


class OpenAITTSBackend(TTSBackend):
    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o-mini-tts",
        default_voice: str = "alloy",
        style: str = "natural",
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.api_key = api_key
        self.model = model or "gpt-4o-mini-tts"
        self.default_voice = default_voice or "alloy"
        self.style = style or "natural"
        self.logger = logger or _logger

    def synthesize(self, text: str, output_path: Path, language: str = "en", voice: Optional[str] = None) -> Path:
        cleaned = " ".join(text.split()).strip()
        if not cleaned:
            raise ValueError("text must be a non-empty string")
        output_path = output_path.resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        effective_voice = _normalize_voice(voice) or self.default_voice
        voice_payload: str | dict = {"id": effective_voice} if effective_voice.startswith("voice_") else effective_voice
        payload = {
            "model": self.model,
            "voice": voice_payload,
            "input": cleaned[:4096],
            "response_format": "wav",
        }
        if self.model not in {"tts-1", "tts-1-hd"} and self.style:
            payload["instructions"] = _style_instructions(self.style, language)
        response = requests.post(
            "https://api.openai.com/v1/audio/speech",
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=120,
        )
        if response.status_code >= 400:
            raise SynthesisError(f"OpenAI TTS failed: {response.status_code} {response.text[:300]}")
        output_path.write_bytes(response.content)
        if not output_path.exists() or output_path.stat().st_size <= 0:
            raise SynthesisError("OpenAI TTS returned an empty audio file")
        return output_path


class ElevenLabsTTSBackend(TTSBackend):
    def __init__(
        self,
        api_key: str,
        model: str = "eleven_multilingual_v2",
        default_voice: str = "",
        style: str = "natural",
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.api_key = api_key
        self.model = model or "eleven_multilingual_v2"
        self.default_voice = default_voice
        self.style = style or "natural"
        self.logger = logger or _logger

    def synthesize(self, text: str, output_path: Path, language: str = "en", voice: Optional[str] = None) -> Path:
        cleaned = " ".join(text.split()).strip()
        if not cleaned:
            raise ValueError("text must be a non-empty string")
        voice_id = _normalize_voice(voice) or self.default_voice
        if not voice_id:
            raise SynthesisError("ElevenLabs voice id is required")
        output_path = output_path.resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        temp_mp3 = output_path.with_suffix(".elevenlabs.mp3")
        payload = {
            "text": cleaned,
            "model_id": self.model,
            "voice_settings": _elevenlabs_voice_settings(self.style),
        }
        response = requests.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
            headers={"xi-api-key": self.api_key, "Content-Type": "application/json"},
            json=payload,
            timeout=120,
        )
        if response.status_code >= 400:
            raise SynthesisError(f"ElevenLabs TTS failed: {response.status_code} {response.text[:300]}")
        temp_mp3.write_bytes(response.content)
        return _convert_to_wav(temp_mp3, output_path)


def _style_instructions(style: str, language: str) -> str:
    styles = {
        "warm": "Speak with a warm, friendly creator tone.",
        "energetic": "Speak with high energy, clear pacing, and short-video momentum.",
        "narration": "Speak like a calm documentary narrator with precise diction.",
        "female_creator": "Speak like a natural female social video creator.",
        "male_documentary": "Speak like a confident male documentary narrator.",
    }
    base = styles.get(style, "Speak naturally with clear pronunciation.")
    return f"{base} Match the target language code {language} and keep the delivery natural."


def _elevenlabs_voice_settings(style: str) -> dict:
    if style == "energetic":
        return {"stability": 0.35, "similarity_boost": 0.80, "style": 0.55, "use_speaker_boost": True}
    if style in {"narration", "male_documentary"}:
        return {"stability": 0.70, "similarity_boost": 0.75, "style": 0.20, "use_speaker_boost": True}
    if style in {"warm", "female_creator"}:
        return {"stability": 0.50, "similarity_boost": 0.80, "style": 0.35, "use_speaker_boost": True}
    return {"stability": 0.50, "similarity_boost": 0.75, "style": 0.20, "use_speaker_boost": True}
