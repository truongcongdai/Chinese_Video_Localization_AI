# src/universal_video_ai/tts/tts.py
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import logging
import shutil
import subprocess
from typing import Optional, Protocol

__all__ = ["TTS", "TTSConfig", "TTSFactory", "NoOpTTS", "EdgeTTS"]

_logger = logging.getLogger(__name__)


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


class EdgeTTS:
    """
    Wrapper around the 'edge-tts' command-line tool.

    Example command (edge-tts must be installed):
      edge-tts --voice "en-US-JennyNeural" --write-media output.mp3 --text "Hello"

    This wrapper constructs the command and runs it via subprocess. Tests mock subprocess.run.
    """

    def __init__(self, config: Optional[TTSConfig] = None, logger: Optional[logging.Logger] = None) -> None:
        self.config = config or TTSConfig(provider="edge")
        self.logger = logger or _logger
        if not _check_edge_tts_available():
            self.logger.warning("edge-tts CLI not found in PATH; EdgeTTS may fail at runtime")
        self.logger.debug("EdgeTTS initialized with config=%s", self.config)

    def synthesize(self, text: str, output_path: Path) -> Path:
        if not isinstance(text, str) or not text:
            raise ValueError("text must be a non-empty string")

        output_path = output_path.resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Build command (edge-tts CLI flags)
        # Use --voice and --write-media options; pass text via --text argument.
        cmd = [
            "edge-tts",
            "--voice", self.config.voice,
            "--write-media", str(output_path),
            "--text", text,
        ]

        self.logger.info("EdgeTTS synthesizing to %s using voice=%s", output_path, self.config.voice)
        self.logger.debug("Running command: %s", " ".join(cmd))

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=120)
            if result.returncode != 0:
                error_msg = result.stderr or result.stdout or "unknown error"
                self.logger.error("edge-tts failed: %s", error_msg)
                raise RuntimeError(f"edge-tts synthesis failed: {error_msg}")

            if not output_path.exists():
                self.logger.error("edge-tts completed but output file missing: %s", output_path)
                raise RuntimeError("edge-tts did not produce output file")

            self.logger.info("EdgeTTS synthesis complete: %s", output_path)
            return output_path

        except subprocess.TimeoutExpired:
            self.logger.error("edge-tts synthesis timed out")
            raise RuntimeError("edge-tts synthesis timed out")
        except FileNotFoundError as exc:
            self.logger.error("edge-tts not found: %s", exc)
            raise RuntimeError("edge-tts CLI not installed or not in PATH") from exc
        except Exception as exc:
            self.logger.exception("Unexpected error during edge-tts synthesis: %s", exc)
            raise RuntimeError(f"TTS synthesis failed: {exc}") from exc


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