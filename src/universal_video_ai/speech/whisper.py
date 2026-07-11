# src/universal_video_ai/speech/whisper.py
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import logging
from typing import List, Optional

from universal_video_ai.segment import TranscriptSegment, UNKNOWN_TIMING

__all__ = ["WhisperTranscriber", "WhisperConfig"]

_logger = logging.getLogger(__name__)


@dataclass
class WhisperConfig:
    """
    Configuration for Whisper transcription.

    Attributes:
        model: model name (e.g. "tiny", "base", "small", "medium", "large").
        device: device string passed to whisper (e.g. "cpu", "cuda:0") or None to let library decide.
        task: "transcribe" or "translate" (if supported).
    """
    model: str = "small"
    device: Optional[str] = None
    task: str = "transcribe"


class WhisperTranscriber:
    """
    Wrapper around OpenAI Whisper transcription.

    Behavior:
    - Attempts to use the `whisper` Python package if available.
    - If `whisper` is not installed, raises a clear RuntimeError instructing how to install it.
    - Exposes a simple `transcribe` method that returns the recognized text.
    - Exposes `transcribe_segments` that returns per-sentence TranscriptSegment
      objects with real start/end timestamps, which is what the rest of the
      pipeline (translation, TTS, subtitles, on-screen text cover) needs in
      order to stay aligned with the original video's timing.

    Notes:
    - This wrapper keeps the heavy dependency optional. Tests mock internal behavior so
      the test suite does not need the real Whisper model.
    """

    def __init__(self, config: Optional[WhisperConfig] = None, logger: Optional[logging.Logger] = None) -> None:
        """
        Initialize the transcriber.

        :param config: optional WhisperConfig object
        :param logger: optional logger; if None, module logger is used
        """
        self.config = config or WhisperConfig()
        self.logger = logger or _logger
        self.logger.debug("WhisperTranscriber initialized with model=%s, device=%s", self.config.model, self.config.device)

    def transcribe(self, audio_path: Path, language: Optional[str] = None) -> str:
        """
        Transcribe the given audio file to text.

        :param audio_path: path to audio file (wav/mp3/etc.)
        :param language: optional language code (e.g., "en") to hint the model
        :return: transcript text
        :raises FileNotFoundError: if audio file doesn't exist
        :raises RuntimeError: if Whisper package is not installed or transcription fails
        """
        audio_path = Path(audio_path).resolve()

        if not audio_path.exists():
            raise FileNotFoundError(f"Audio file not found: {audio_path}")
        if not audio_path.is_file():
            raise FileNotFoundError(f"Path is not a file: {audio_path}")

        # Delegate to backend implementation; this method is small and easy to mock in tests.
        return self._transcribe_with_python_whisper(audio_path, language=language)

    def transcribe_segments(self, audio_path: Path, language: Optional[str] = None) -> List[TranscriptSegment]:
        """
        Transcribe the given audio file and return per-sentence segments with
        real start/end timestamps (seconds), as reported by Whisper.

        This is the timestamp-accurate counterpart of `transcribe()`. Whisper
        already computes sentence-level timing internally (`result["segments"]`);
        previously this wrapper discarded that information and only returned
        the flat `result["text"]`, which made it impossible for translation/TTS/
        subtitles to stay aligned with the source video. This method fixes that.

        :param audio_path: path to audio file (wav/mp3/etc.)
        :param language: optional language code (e.g., "en") to hint the model
        :return: list of TranscriptSegment ordered by start time
        :raises FileNotFoundError: if audio file doesn't exist
        :raises RuntimeError: if Whisper package is not installed or transcription fails
        """
        audio_path = Path(audio_path).resolve()

        if not audio_path.exists():
            raise FileNotFoundError(f"Audio file not found: {audio_path}")
        if not audio_path.is_file():
            raise FileNotFoundError(f"Path is not a file: {audio_path}")

        return self._transcribe_segments_with_python_whisper(audio_path, language=language)

    def _run_whisper(self, audio_path: Path, language: Optional[str] = None) -> dict:
        """
        Shared low-level call into the `whisper` python package.

        Returns the raw dict result (with "text" and "segments" keys).
        Both `_transcribe_with_python_whisper` and
        `_transcribe_segments_with_python_whisper` delegate here so the model
        is loaded/invoked in exactly one place.

        :raises RuntimeError: if whisper is not installed or transcription fails
        """
        try:
            import whisper  # type: ignore
        except Exception as exc:  # ImportError or other import-time errors
            self.logger.error("Whisper package is not available: %s", exc)
            raise RuntimeError(
                "The 'whisper' package is not installed. Install it with: "
                "pip install -U openai-whisper ; note this may also require torch. "
                "Alternatively, use a different transcription backend."
            ) from exc

        try:
            # Load model (this can be heavy; caller is responsible for environment)
            self.logger.debug("Loading whisper model %s (device=%s)", self.config.model, self.config.device)
            model = whisper.load_model(self.config.model, device=self.config.device)  # type: ignore[attr-defined]
            self.logger.debug("Model loaded, starting transcription for %s", audio_path)

            # whisper.transcribe accepts language and task
            whisper_kwargs = {}
            if language:
                whisper_kwargs["language"] = language
            if self.config.task:
                whisper_kwargs["task"] = self.config.task

            result = model.transcribe(str(audio_path), **whisper_kwargs)  # type: ignore[attr-defined]
            if not isinstance(result, dict):
                # Defensive: some mocks/backends might return a plain string.
                result = {"text": str(result), "segments": []}
            return result
        except Exception as exc:
            self.logger.exception("Whisper transcription failed: %s", exc)
            raise RuntimeError(f"Whisper transcription failed: {exc}") from exc

    def _transcribe_with_python_whisper(self, audio_path: Path, language: Optional[str] = None) -> str:
        """
        Attempt to transcribe using the `whisper` python package.

        This method tries to import `whisper` lazily so the package remains optional.
        It raises a RuntimeError with an actionable message if the package is not installed.

        :param audio_path: Path to audio file
        :param language: optional language code
        :return: recognized text
        :raises RuntimeError: if whisper is not installed or transcription fails
        """
        result = self._run_whisper(audio_path, language=language)
        text = result.get("text", "")
        self.logger.info("Transcription complete for %s (length=%d chars)", audio_path, len(text))
        return text

    def _transcribe_segments_with_python_whisper(
        self, audio_path: Path, language: Optional[str] = None
    ) -> List[TranscriptSegment]:
        """
        Attempt to transcribe using the `whisper` python package and preserve
        Whisper's own per-segment start/end timestamps.

        :param audio_path: Path to audio file
        :param language: optional language code
        :return: list of TranscriptSegment ordered by start time
        :raises RuntimeError: if whisper is not installed or transcription fails
        """
        result = self._run_whisper(audio_path, language=language)
        raw_segments = result.get("segments") or []

        segments: List[TranscriptSegment] = []
        for raw in raw_segments:
            text = (raw.get("text") or "").strip()
            if not text:
                continue
            start = float(raw.get("start", 0.0))
            end = float(raw.get("end", start))
            segments.append(TranscriptSegment(start=start, end=end, text=text))

        if not segments:
            # Whisper produced no segment-level data (e.g. very short/empty audio,
            # or a mocked backend). Fall back to the flat text as a single
            # segment with unknown timing rather than losing the transcript.
            text = (result.get("text") or "").strip()
            if text:
                segments = [TranscriptSegment(start=0.0, end=UNKNOWN_TIMING, text=text)]

        self.logger.info(
            "Segment transcription complete for %s (%d segments)", audio_path, len(segments)
        )
        return segments
