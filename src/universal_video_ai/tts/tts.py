# src/universal_video_ai/tts/tts.py
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import logging
import os
import random
import shutil
import subprocess
import threading
import time
from typing import Optional, Protocol

__all__ = ["TTS", "TTSConfig", "TTSFactory", "NoOpTTS", "EdgeTTS", "voice_for_language"]

_logger = logging.getLogger(__name__)

# Serialize Edge TTS calls inside this process. Multiple concurrent websocket
# requests are a common cause of transient NoAudioReceived failures.
_EDGE_TTS_LOCK = threading.Lock()

# Maps an ISO-639-1 (or ISO-639-1 + region) language code to a sensible
# default Edge neural voice for that language. This matters because Edge's
# TTS service will reliably fail (commonly surfacing as
# `edge_tts.exceptions.NoAudioReceived`) when asked to speak text in a
# language that does not match the selected voice's locale — e.g. sending
# Vietnamese text to "en-US-JennyNeural". Keys are lowercased language
# codes as used throughout this project (e.g. "vi", "en", "zh").
DEFAULT_VOICES_BY_LANGUAGE = {
    "vi": "vi-VN-HoaiMyNeural|rate=-3%|pitch=+2Hz",
    "en": "en-US-JennyNeural",
    "zh": "zh-CN-XiaoxiaoNeural",
    "zh-cn": "zh-CN-XiaoxiaoNeural",
    "zh-tw": "zh-TW-HsiaoChenNeural",
    "ja": "ja-JP-NanamiNeural",
    "ko": "ko-KR-SunHiNeural",
    "fr": "fr-FR-DeniseNeural",
    "de": "de-DE-KatjaNeural",
    "es": "es-ES-ElviraNeural",
    "pt": "pt-BR-FranciscaNeural",
    "ru": "ru-RU-SvetlanaNeural",
    "th": "th-TH-PremwadeeNeural",
    "id": "id-ID-GadisNeural",
    "ar": "ar-SA-ZariyahNeural",
    "hi": "hi-IN-SwaraNeural",
}


def voice_for_language(language: Optional[str], fallback: Optional[str] = None) -> str:
    """
    Resolve a sensible default Edge TTS voice for a given language code.

    :param language: language code (e.g. "vi", "en", "zh-CN"); case-insensitive.
    :param fallback: voice to use if the language is unknown (defaults to the
        English voice so callers always get *some* valid voice back).
    :return: an Edge TTS voice name.
    """
    if language:
        key = language.strip().lower()
        if key in DEFAULT_VOICES_BY_LANGUAGE:
            return DEFAULT_VOICES_BY_LANGUAGE[key]
        # Try just the primary subtag (e.g. "vi-VN" -> "vi")
        primary = key.split("-")[0]
        if primary in DEFAULT_VOICES_BY_LANGUAGE:
            return DEFAULT_VOICES_BY_LANGUAGE[primary]
    return fallback or DEFAULT_VOICES_BY_LANGUAGE["en"]


class TTS(Protocol):
    """
    Text-to-speech engine interface.

    Implementations must provide `synthesize`.
    """

    def synthesize(self, text: str, output_path: Path) -> Path:
        """
        Synthesize `text` to a media file at `output_path`.

        :param text: input text to synthesize (non-empty)
        :param output_path: target output file path (directory is created if needed)
        :return: Path to created output file
        :raises ValueError: for invalid input
        :raises RuntimeError: on synthesis failure
        """
        ...


@dataclass(frozen=True)
class TTSConfig:
    """
    Configuration for TTS engine.

    Attributes:
        provider: 'noop', 'edge', 'azure', or 'google' (default 'noop')
        voice: voice identifier for provider (Edge TTS example: "en-US-JennyNeural")
        output_format: output file extension/format (e.g., 'mp3', 'wav')
        api_key: API key for cloud providers (Azure, Google)
        region: Region for cloud providers (Azure only, default 'eastus')
    """
    provider: str = "noop"
    voice: str = "en-US-JennyNeural"
    output_format: str = "mp3"
    api_key: Optional[str] = None
    region: Optional[str] = None


class NoOpTTS:
    """
    No-op TTS implementation for development and tests.

    It writes a small placeholder file containing a header and the text.
    """

    def __init__(self, config: Optional[TTSConfig] = None, logger: Optional[logging.Logger] = None) -> None:
        self.config = config or TTSConfig()
        self.logger = logger or _logger
        self.logger.debug("NoOpTTS initialized with config=%s", self.config)

    def synthesize(self, text: str, output_path: Path) -> Path:
        if not isinstance(text, str) or not text:
            raise ValueError("text must be a non-empty string")

        output_path = output_path.resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)

        placeholder = f"TTS_PLACEHOLDER\nprovider=noop\nvoice={self.config.voice}\nformat={self.config.output_format}\n\n{text}"
        self.logger.info("NoOpTTS synthesizing to %s", output_path)
        output_path.write_bytes(placeholder.encode("utf-8"))
        return output_path


def _check_edge_tts_available() -> bool:
    """
    Check if 'edge-tts' CLI is available in PATH.
    """
    return shutil.which("edge-tts") is not None


def _validate_audio_file(audio_path: Path, logger: logging.Logger) -> float:
    """
    Validate a generated audio file and return its duration in seconds.

    ffprobe is preferred because valid very-short speech can be smaller than
    any arbitrary byte threshold. When ffprobe is unavailable, the function
    falls back to checking that the file exists and is non-empty.
    """
    if not audio_path.exists():
        raise RuntimeError(f"audio output file is missing: {audio_path}")

    file_size = audio_path.stat().st_size
    if file_size <= 0:
        raise RuntimeError(f"audio output file is empty: {audio_path}")

    ffprobe = shutil.which("ffprobe")
    if ffprobe is None:
        logger.warning(
            "ffprobe is not available; validating Edge TTS output by file size only: %s bytes",
            file_size,
        )
        return 0.0

    probe = subprocess.run(
        [
            ffprobe,
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(audio_path),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )

    if probe.returncode != 0:
        error = (probe.stderr or probe.stdout or "unknown ffprobe error").strip()
        raise RuntimeError(f"ffprobe rejected generated audio: {error}")

    raw_duration = probe.stdout.strip()
    try:
        duration = float(raw_duration)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(
            f"ffprobe returned an invalid audio duration: {raw_duration!r}"
        ) from exc

    if duration <= 0:
        raise RuntimeError(
            f"generated audio has invalid duration {duration}: {audio_path}"
        )

    return duration


class EdgeTTS:
    """
    Wrapper around the 'edge-tts' command-line tool.

    Example command (edge-tts must be installed):
      edge-tts --voice "en-US-JennyNeural" --write-media output.mp3 --text "Hello"

    This wrapper constructs the command and runs it via subprocess. Tests mock subprocess.run.
    """

    def __init__(
            self,
            config: Optional[TTSConfig] = None,
            logger: Optional[logging.Logger] = None,
            max_retries: int = 5,
            retry_backoff_seconds: float = 0.6,
    ) -> None:
        self.config = config or TTSConfig(provider="edge")
        self.logger = logger or _logger
        self.max_retries = max(1, max_retries)
        self.retry_backoff_seconds = retry_backoff_seconds
        self.timeout_seconds = max(8, int(os.environ.get("EDGE_TTS_TIMEOUT_SECONDS", "28")))
        if not _check_edge_tts_available():
            self.logger.warning("edge-tts CLI not found in PATH; EdgeTTS may fail at runtime")
        self.logger.debug("EdgeTTS initialized with config=%s", self.config)

    def synthesize(
            self, text: str, output_path: Path, voice: Optional[str] = None,
            rate: Optional[str] = None, pitch: Optional[str] = None,
    ) -> Path:
        """
        Synthesize ``text`` to ``output_path`` with guarded retries.

        The implementation serializes Edge TTS calls within the current
        process, validates input/output, and retries transient failures with
        exponential backoff plus jitter. Retries intentionally keep the same
        requested voice so a single dubbed video does not switch speakers
        mid-scene after a transient Edge failure.
        """
        if not isinstance(text, str):
            raise ValueError("text must be a string")

        cleaned_text = " ".join(text.split()).strip()
        if not cleaned_text:
            raise ValueError("text must be a non-empty string")
        if not any(char.isalnum() for char in cleaned_text):
            raise ValueError(f"text contains no speakable characters: {cleaned_text!r}")

        output_path = output_path.resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)

        primary_voice = voice or self.config.voice
        voices = [primary_voice]

        self.logger.info(
            "EdgeTTS request: chars=%d voice=%s output=%s text=%r",
            len(cleaned_text),
            primary_voice,
            output_path,
            cleaned_text[:150],
        )

        last_error: Optional[str] = None

        # Keep one Edge websocket request active at a time in this Python process.
        with _EDGE_TTS_LOCK:
            for voice_index, effective_voice in enumerate(voices):
                attempts_for_voice = self.max_retries if voice_index == len(voices) - 1 else min(2, self.max_retries)
                self.logger.info(
                    "EdgeTTS synthesizing to %s using voice=%s attempts=%d",
                    output_path,
                    effective_voice,
                    attempts_for_voice,
                )

                for attempt in range(1, attempts_for_voice + 1):
                    output_path.unlink(missing_ok=True)
                    temp_output = output_path.with_name(
                        f"{output_path.stem}.part{output_path.suffix}"
                    )
                    temp_output.unlink(missing_ok=True)

                    cmd = [
                        "edge-tts",
                        "--voice", effective_voice,
                        "--write-media", str(temp_output),
                        "--text", cleaned_text,
                    ]
                    if rate:
                        cmd.append(f"--rate={rate}")
                    if pitch:
                        cmd.append(f"--pitch={pitch}")

                    self.logger.debug("Running command: %s", " ".join(cmd))

                    try:
                        result = subprocess.run(
                            cmd,
                            capture_output=True,
                            text=True,
                            check=False,
                            timeout=self.timeout_seconds,
                        )

                        if result.returncode != 0:
                            last_error = (
                                (result.stderr or result.stdout or "unknown error").strip()
                            )
                            raise RuntimeError(last_error)

                        duration = _validate_audio_file(temp_output, self.logger)
                        output_size = temp_output.stat().st_size

                        temp_output.replace(output_path)
                        self.logger.info(
                            "EdgeTTS synthesis complete: %s (%d bytes, %.3fs)",
                            output_path,
                            output_size,
                            duration,
                        )
                        return output_path

                    except subprocess.TimeoutExpired:
                        last_error = f"edge-tts synthesis timed out after {self.timeout_seconds} seconds"
                    except FileNotFoundError as exc:
                        self.logger.error("edge-tts not found: %s", exc)
                        raise RuntimeError(
                            "edge-tts CLI not installed or not in PATH"
                        ) from exc
                    except RuntimeError as exc:
                        last_error = str(exc)
                    except Exception as exc:
                        last_error = f"unexpected edge-tts error: {exc}"
                        self.logger.exception("%s", last_error)

                    temp_output.unlink(missing_ok=True)
                    output_path.unlink(missing_ok=True)

                    if attempt < attempts_for_voice:
                        delay = min(
                            4.0,
                            self.retry_backoff_seconds * (1.7 ** (attempt - 1))
                            + random.uniform(0.0, 0.35),
                        )
                        self.logger.info(
                            "edge-tts transient failure (voice=%s, attempt %d/%d): %s; "
                            "retrying in %.1fs",
                            effective_voice,
                            attempt,
                            attempts_for_voice,
                            last_error,
                            delay,
                        )
                        time.sleep(delay)
                    else:
                        self.logger.warning(
                            "edge-tts exhausted attempts for voice=%s: %s",
                            effective_voice,
                            last_error,
                        )

                if len(voices) > 1 and effective_voice != voices[-1]:
                    self.logger.warning(
                        "Falling back from voice=%s to voice=%s",
                        effective_voice,
                        voices[-1],
                    )

        raise RuntimeError(
            f"edge-tts synthesis failed after trying {len(voices)} voice(s): "
            f"{last_error}"
        )


class TTSFactory:
    """
    Factory for creating TTS engine instances based on config.
    """

    @staticmethod
    def create(config: Optional[TTSConfig] = None, logger: Optional[logging.Logger] = None) -> TTS:
        cfg = config or TTSConfig()
        provider = (cfg.provider or "noop").lower().strip()
        logger = logger or _logger
        logger.debug("TTSFactory.create provider=%s", provider)

        if provider == "noop":
            return NoOpTTS(config=cfg, logger=logger)
        if provider == "edge":
            return EdgeTTS(config=cfg, logger=logger)

        # Azure TTS
        if provider == "azure":
            try:
                import azure.cognitiveservices.speech as speechsdk  # type: ignore
            except Exception as exc:
                raise ValueError(
                    "Azure provider requested but azure-cognitiveservices-speech is not available. "
                    "Install azure-cognitiveservices-speech or choose another provider."
                ) from exc

            if not cfg.api_key:
                raise ValueError("Azure provider requires api_key in TTSConfig")

            class _AzureTTS:
                def __init__(self, cfg: TTSConfig, logger: logging.Logger) -> None:
                    self.cfg = cfg
                    self.logger = logger
                    self.speech_config = speechsdk.SpeechConfig(
                        subscription=cfg.api_key,
                        region=cfg.region or "eastus"
                    )
                    self.speech_config.speech_synthesis_voice_name = cfg.voice or "en-US-JennyNeural"
                    self.speech_config.set_speech_synthesis_output_format(
                        speechsdk.SpeechSynthesisOutputFormat.Audio16Khz128KBitRateMonoMp3
                    )

                def synthesize(self, text: str, output_path: Path) -> Path:
                    output_path = output_path.resolve()
                    output_path.parent.mkdir(parents=True, exist_ok=True)

                    synthesizer = speechsdk.SpeechSynthesizer(
                        speech_config=self.speech_config,
                        audio_config=speechsdk.audio.AudioOutputConfig(str(output_path))
                    )

                    self.logger.info("Azure TTS synthesizing to %s", output_path)
                    result = synthesizer.speak_text_async(text).get()

                    if result.reason == speechsdk.ResultReason.SynthesizingAudioCompleted:
                        self.logger.info("Azure TTS synthesis complete: %s", output_path)
                        return output_path
                    else:
                        raise RuntimeError(f"Azure TTS failed: {result.reason}")

            return _AzureTTS(cfg, logger)

        # Google TTS
        if provider == "google":
            try:
                from gtts import gTTS  # type: ignore
            except Exception as exc:
                raise ValueError(
                    "Google provider requested but gTTS is not available. "
                    "Install gTTS or choose another provider."
                ) from exc

            class _GoogleTTS:
                def __init__(self, cfg: TTSConfig, logger: logging.Logger) -> None:
                    self.cfg = cfg
                    self.logger = logger

                def synthesize(self, text: str, output_path: Path) -> Path:
                    output_path = output_path.resolve()
                    output_path.parent.mkdir(parents=True, exist_ok=True)

                    self.logger.info("Google TTS synthesizing to %s", output_path)
                    tts = gTTS(text=text, lang=self.cfg.voice[:2] if self.cfg.voice else "en")
                    tts.save(str(output_path))

                    self.logger.info("Google TTS synthesis complete: %s", output_path)
                    return output_path

            return _GoogleTTS(cfg, logger)

        raise ValueError(f"Unknown TTS provider: {cfg.provider!r}")
