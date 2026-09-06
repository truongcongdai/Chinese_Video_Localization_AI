"""Provider-neutral, free/local-first TTS voice discovery."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import logging
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Dict, List, Optional, Protocol, Sequence

from .backend import EdgeTTSBackend

__all__ = [
    "TTSVoice",
    "ProviderHealth",
    "TTSProvider",
    "EdgeVoiceProvider",
    "PiperVoiceProvider",
    "TTSVoiceRegistry",
    "RegistryTTSBackend",
    "get_default_voice_registry",
]

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TTSVoice:
    provider: str
    voice_id: str
    display_name: str
    language: str
    locale: str
    gender: Optional[str]
    speaker_identity: str
    is_local: bool
    requires_api_key: bool
    cost_class: str
    license_notes: str
    available: bool
    verified_synthesis: bool = False

    @property
    def catalog_id(self) -> str:
        return self.voice_id if self.provider == "edge" else f"{self.provider}:{self.voice_id}"

    def to_dict(self) -> dict:
        value = asdict(self)
        value.update({"id": self.catalog_id, "label": self.display_name})
        return value


@dataclass(frozen=True)
class ProviderHealth:
    provider: str
    available: bool
    detail: str


class TTSProvider(Protocol):
    provider_id: str

    def list_voices(self, language: str, refresh: bool = False) -> List[TTSVoice]: ...
    def supports_language(self, language: str) -> bool: ...
    def synthesize(self, text: str, output_path: Path, voice_id: str, language: str) -> Path: ...
    def health_check(self) -> ProviderHealth: ...


class EdgeVoiceProvider:
    provider_id = "edge"

    def __init__(self, logger: Optional[logging.Logger] = None) -> None:
        self.logger = logger or _logger
        self.backend = EdgeTTSBackend(logger=self.logger)

    def health_check(self) -> ProviderHealth:
        path = shutil.which("edge-tts")
        return ProviderHealth(self.provider_id, path is not None, path or "edge-tts CLI not installed")

    def list_voices(self, language: str, refresh: bool = False) -> List[TTSVoice]:
        health = self.health_check()
        primary = (language or "").lower().split("-")[0]
        # Query the installed provider instead of inventing locale voices.
        rows: List[tuple[str, str]] = []
        if health.available:
            try:
                result = subprocess.run(
                    ["edge-tts", "--list-voices"],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=30,
                )
                if result.returncode == 0:
                    for line in result.stdout.splitlines():
                        parts = line.split()
                        if len(parts) >= 2 and parts[0].lower().startswith(primary + "-"):
                            rows.append((parts[0], parts[1]))
            except Exception as exc:
                self.logger.warning("Could not refresh Edge voice catalog: %s", exc)
        if not rows and primary == "vi":
            rows = [
                ("vi-VN-HoaiMyNeural", "Female"),
                ("vi-VN-NamMinhNeural", "Male"),
            ]
        voices = []
        for voice_id, gender in rows:
            locale = "-".join(voice_id.split("-")[:2])
            name = voice_id.removeprefix(locale + "-").removesuffix("Neural")
            voices.append(TTSVoice(
                provider="edge",
                voice_id=voice_id,
                display_name=f"{name} ({locale})",
                language=primary,
                locale=locale,
                gender=gender.lower(),
                speaker_identity=f"edge:{voice_id}",
                is_local=False,
                requires_api_key=False,
                cost_class="free",
                license_notes="Microsoft Edge online TTS service terms apply",
                available=health.available,
            ))
        return voices

    def supports_language(self, language: str) -> bool:
        return bool(self.list_voices(language))

    def synthesize(self, text: str, output_path: Path, voice_id: str, language: str) -> Path:
        return self.backend.synthesize(text, output_path, language=language, voice=voice_id)


class PiperVoiceProvider:
    """Optional local Piper adapter; models are user-installed, never downloaded."""

    provider_id = "piper"

    def __init__(self, model_dirs: Optional[Sequence[Path]] = None, logger: Optional[logging.Logger] = None) -> None:
        self.logger = logger or _logger
        configured = os.getenv("PIPER_VOICE_DIR", "")
        self.model_dirs = list(model_dirs or ([Path(configured)] if configured else []))
        self.executable = shutil.which("piper")

    def health_check(self) -> ProviderHealth:
        if not self.executable:
            return ProviderHealth(self.provider_id, False, "piper CLI not installed")
        if not any(path.exists() for path in self.model_dirs):
            return ProviderHealth(self.provider_id, False, "PIPER_VOICE_DIR has no installed models")
        return ProviderHealth(self.provider_id, True, self.executable)

    def _models(self) -> List[tuple[Path, dict]]:
        found = []
        for directory in self.model_dirs:
            if not directory.exists():
                continue
            for model in directory.glob("*.onnx"):
                metadata_path = model.with_suffix(".onnx.json")
                try:
                    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                except Exception:
                    metadata = {}
                found.append((model, metadata))
        return found

    def list_voices(self, language: str, refresh: bool = False) -> List[TTSVoice]:
        if not self.health_check().available:
            return []
        primary = (language or "").lower().split("-")[0]
        voices = []
        for model, metadata in self._models():
            language_meta = metadata.get("language") or {}
            locale = str(language_meta.get("code") or "")
            if locale.lower().split("-")[0] != primary:
                continue
            dataset = str(metadata.get("dataset") or model.stem)
            voices.append(TTSVoice(
                provider="piper",
                voice_id=model.stem,
                display_name=str(metadata.get("name") or model.stem),
                language=primary,
                locale=locale,
                gender=None,
                speaker_identity=f"piper:{dataset}:{model.stem}",
                is_local=True,
                requires_api_key=False,
                cost_class="free-local",
                license_notes="User-installed Piper model; operator must verify model-card license",
                available=True,
            ))
        return voices

    def supports_language(self, language: str) -> bool:
        return bool(self.list_voices(language))

    def _model_path(self, voice_id: str) -> Path:
        matches = [model for model, _ in self._models() if model.stem == voice_id]
        if len(matches) != 1:
            raise RuntimeError(f"Piper voice model is unavailable: {voice_id}")
        return matches[0]

    def synthesize(self, text: str, output_path: Path, voice_id: str, language: str) -> Path:
        if not self.executable:
            raise RuntimeError("piper CLI not installed")
        output_path = output_path.resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            [self.executable, "--model", str(self._model_path(voice_id)), "--output_file", str(output_path)],
            input=" ".join(text.split()),
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
        if result.returncode != 0 or not output_path.exists() or output_path.stat().st_size <= 0:
            output_path.unlink(missing_ok=True)
            raise RuntimeError((result.stderr or "Piper returned no audio").strip())
        return output_path


class TTSVoiceRegistry:
    def __init__(self, providers: Optional[Sequence[TTSProvider]] = None) -> None:
        self.providers: Dict[str, TTSProvider] = {
            provider.provider_id: provider
            for provider in (providers or [EdgeVoiceProvider(), PiperVoiceProvider()])
        }
        self._verified_voice_ids: set[str] = set()

    def health(self) -> List[ProviderHealth]:
        return [provider.health_check() for provider in self.providers.values()]

    def list_voices(
        self,
        language: str,
        *,
        provider: Optional[str] = None,
        refresh: bool = False,
        verify: bool = False,
    ) -> List[TTSVoice]:
        selected = (
            [self.providers[provider]]
            if provider and provider in self.providers
            else list(self.providers.values())
        )
        voices = [
            voice
            for item in selected
            for voice in item.list_voices(language, refresh=refresh)
            if voice.available
        ]
        if not verify:
            return [
                TTSVoice(**{
                    **asdict(voice),
                    "verified_synthesis": voice.catalog_id in self._verified_voice_ids,
                })
                for voice in voices
            ]
        verified = []
        for voice in voices:
            if voice.catalog_id in self._verified_voice_ids:
                verified.append(TTSVoice(**{**asdict(voice), "verified_synthesis": True}))
                continue
            suffix = ".mp3" if voice.provider == "edge" else ".wav"
            try:
                with tempfile.TemporaryDirectory(prefix="tts_voice_check_") as tmp:
                    path = Path(tmp) / f"sample{suffix}"
                    self.synthesize(
                        "Xin chào, đây là kiểm tra giọng nói tiếng Việt.",
                        path,
                        language,
                        voice.catalog_id,
                    )
                    if path.exists() and path.stat().st_size > 0:
                        self._verified_voice_ids.add(voice.catalog_id)
                        verified.append(TTSVoice(**{**asdict(voice), "verified_synthesis": True}))
            except Exception as exc:
                _logger.warning("Voice verification failed for %s: %s", voice.catalog_id, exc)
        return verified

    def synthesize(self, text: str, output_path: Path, language: str, voice_id: str) -> Path:
        if ":" in voice_id:
            provider_id, native_id = voice_id.split(":", 1)
        else:
            provider_id, native_id = "edge", voice_id
        provider = self.providers.get(provider_id)
        if provider is None:
            raise RuntimeError(f"Unknown TTS provider: {provider_id}")
        return provider.synthesize(text, output_path, native_id, language)

    @staticmethod
    def distinct_speaker_count(voices: Sequence[TTSVoice]) -> int:
        return len({voice.speaker_identity for voice in voices})


class RegistryTTSBackend:
    """TTSBackend-compatible adapter preserving the existing TTSService."""

    def __init__(self, registry: Optional[TTSVoiceRegistry] = None) -> None:
        self.registry = registry or get_default_voice_registry()

    def synthesize(self, text: str, output_path: Path, language: str = "en", voice: Optional[str] = None) -> Path:
        if not voice:
            from .tts import voice_for_language
            voice = voice_for_language(language).split("|", 1)[0]
        return self.registry.synthesize(text, output_path, language, voice)


_DEFAULT_REGISTRY: Optional[TTSVoiceRegistry] = None


def get_default_voice_registry() -> TTSVoiceRegistry:
    global _DEFAULT_REGISTRY
    if _DEFAULT_REGISTRY is None:
        _DEFAULT_REGISTRY = TTSVoiceRegistry()
    return _DEFAULT_REGISTRY
