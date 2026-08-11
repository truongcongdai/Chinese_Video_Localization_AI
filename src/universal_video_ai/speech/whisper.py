# src/universal_video_ai/speech/whisper.py
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import logging
import re
import threading
import time
from typing import List, Optional

from universal_video_ai.segment import TranscriptSegment, UNKNOWN_TIMING

__all__ = ["WhisperTranscriber", "WhisperConfig"]

_logger = logging.getLogger(__name__)
_MODEL_CACHE: dict[tuple[str, Optional[str], bool], object] = {}
_MODEL_CACHE_LOCK = threading.Lock()
_MODEL_INFERENCE_LOCKS: dict[tuple[str, Optional[str], bool], threading.Lock] = {}


def _resolve_whisper_device(torch_module, requested_device: Optional[str]) -> str:
    """Resolve ``auto`` to a concrete device for predictable deployments.

    Passing ``None`` through to openai-whisper technically lets that library
    choose a device, but it leaves our precision selection and diagnostics
    guessing. Resolve it once here so GPU machines use CUDA and machines with
    a CPU-only PyTorch build fall back cleanly to CPU.
    """
    requested = (requested_device or "auto").strip().lower()
    if requested == "auto":
        return "cuda" if torch_module.cuda.is_available() else "cpu"
    if requested == "cpu":
        return "cpu"
    if requested == "cuda" or requested.startswith("cuda:"):
        if not torch_module.cuda.is_available():
            cuda_build = getattr(getattr(torch_module, "version", None), "cuda", None)
            detail = (
                "the installed PyTorch build has no CUDA support"
                if not cuda_build
                else f"PyTorch CUDA {cuda_build} cannot access an NVIDIA GPU"
            )
            raise RuntimeError(
                f"Whisper device {requested!r} was requested, but {detail}. "
                "Use WHISPER_DEVICE=auto (recommended) or WHISPER_DEVICE=cpu."
            )
        return requested
    raise RuntimeError(
        f"Unsupported Whisper device {requested_device!r}; expected auto, cpu, cuda, or cuda:<index>"
    )


def _cached_whisper_model(whisper_module, model_name: str, device: Optional[str], fp16: bool = False):
    """Load a cached Whisper model on the requested device.

    Do not call ``model.half()`` here. openai-whisper deliberately keeps
    normalization weights in fp32 and applies fp16 to inference inputs through
    its ``fp16`` transcribe option. Converting every module to half breaks
    LayerNorm on CUDA with ``expected scalar type Float but found Half``.
    """
    key = (model_name, device, fp16)
    model = _MODEL_CACHE.get(key)
    if model is not None:
        return model
    with _MODEL_CACHE_LOCK:
        model = _MODEL_CACHE.get(key)
        if model is None:
            model = whisper_module.load_model(model_name, device=device)
            _MODEL_CACHE[key] = model
    return model


def _whisper_inference_lock(
    model_name: str, device: Optional[str], fp16: bool
) -> threading.Lock:
    """Return the lock guarding one cached model's mutable decode state.

    ``openai-whisper`` installs and removes KV-cache hooks on the model while
    decoding. A cached model therefore cannot safely serve two Python threads
    at once: concurrent calls corrupt that cache and fail with ``KeyError:
    Linear(...)`` or zero-length tensor errors. Loading/extraction can remain
    concurrent; only the actual model inference is serialized.
    """
    key = (model_name, device, fp16)
    with _MODEL_CACHE_LOCK:
        return _MODEL_INFERENCE_LOCKS.setdefault(key, threading.Lock())


@dataclass
class WhisperConfig:
    """
    Configuration for Whisper transcription.

    Attributes:
        model: model name (e.g. "tiny", "base", "small", "medium", "large").
               base = balanced accuracy/speed (recommended for quality), small = higher accuracy.
        device: "auto" (default), "cpu", "cuda", or a CUDA index such as
                "cuda:0". None is retained as a backwards-compatible alias
                for "auto".
        task: "transcribe" or "translate" (if supported).
        fp16: use fp16 for faster inference (default True for GPU, False for CPU).
    """
    model: str = "base"
    device: Optional[str] = "auto"
    task: str = "transcribe"
    fp16: Optional[bool] = None  # None = auto-detect (True for GPU, False for CPU)


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
        self.last_detected_language: Optional[str] = None
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
            import torch  # type: ignore
            import whisper  # type: ignore
        except Exception as exc:  # ImportError or other import-time errors
            self.logger.error("Whisper package is not available: %s", exc)
            raise RuntimeError(
                "The 'whisper' package is not installed. Install it with: "
                "pip install -U openai-whisper ; note this may also require torch. "
                "Alternatively, use a different transcription backend."
            ) from exc

        try:
            requested_device = self.config.device or "auto"
            resolved_device = _resolve_whisper_device(torch, requested_device)

            device_description = resolved_device
            gpu_name = ""
            if resolved_device.startswith("cuda"):
                try:
                    device_index = (
                        int(resolved_device.split(":", 1)[1])
                        if ":" in resolved_device
                        else torch.cuda.current_device()
                    )
                    gpu_name = torch.cuda.get_device_name(device_index)
                    device_description = f"{resolved_device} ({gpu_name})"
                except Exception:
                    pass

            # Auto-detect fp16 from the concrete device, not from the original
            # "auto" value. GTX 16xx cards are kept on CUDA/fp32 because
            # openai-whisper produces NaN logits with fp16 on these cards on
            # Windows; this was verified on the project's GTX 1660 SUPER.
            use_fp16 = self.config.fp16
            if use_fp16 is None:
                use_fp16 = resolved_device.startswith("cuda") and not re.search(
                    r"\bGTX\s*16\d{2}\b", gpu_name, re.IGNORECASE
                )
            self.logger.info(
                "Whisper selected device=%s model=%s precision=%s (requested=%s)",
                device_description,
                self.config.model,
                "fp16" if use_fp16 else "fp32",
                requested_device,
            )

            # Load model (this can be heavy; caller is responsible for environment)
            self.logger.debug("Loading whisper model %s (device=%s, fp16=%s)", self.config.model, resolved_device, use_fp16)
            model = _cached_whisper_model(whisper, self.config.model, resolved_device, use_fp16)
            self.logger.debug("Model loaded, starting transcription for %s", audio_path)

            # whisper.transcribe accepts language and task
            whisper_kwargs = {}
            if language:
                whisper_kwargs["language"] = language
            if self.config.task:
                whisper_kwargs["task"] = self.config.task
            # Pass fp16 explicitly. openai-whisper defaults to fp16 on CUDA;
            # with device=None it may auto-pick CUDA even though our local
            # config resolved use_fp16=False, which can produce NaN logits on
            # some Windows/GPU/driver combinations.
            whisper_kwargs["fp16"] = bool(use_fp16)
            # openai-whisper only displays its tqdm progress bar when verbose
            # is explicitly False. Multi-hour inputs must not look frozen.
            whisper_kwargs["verbose"] = False

            inference_lock = _whisper_inference_lock(
                self.config.model, resolved_device, bool(use_fp16)
            )
            def retry_cpu_fp32() -> dict:
                self.logger.warning(
                    "Whisper CUDA decoding produced invalid values on %s; "
                    "retrying once on CPU/fp32",
                    audio_path,
                )
                cpu_model = _cached_whisper_model(whisper, self.config.model, "cpu", False)
                cpu_lock = _whisper_inference_lock(self.config.model, "cpu", False)
                retry_kwargs = dict(whisper_kwargs)
                retry_kwargs["fp16"] = False
                with cpu_lock:
                    return cpu_model.transcribe(str(audio_path), **retry_kwargs)  # type: ignore[attr-defined]

            started_at = time.monotonic()
            with inference_lock:
                try:
                    result = model.transcribe(str(audio_path), **whisper_kwargs)  # type: ignore[attr-defined]
                except (ValueError, RuntimeError) as decode_exc:
                    # Retry with safer precision/device if CUDA decoding
                    # produces dtype/NaN failures. Keep same-model retries
                    # under the same lock because openai-whisper mutates
                    # decoder hooks during inference.
                    message = str(decode_exc)
                    lower_message = message.lower()
                    is_numeric_cuda_failure = (
                        "dtype" in lower_message
                        or "invalid values" in lower_message
                        or "nan" in lower_message
                        or "expected scalar type" in lower_message
                    )
                    if use_fp16 and is_numeric_cuda_failure:
                        self.logger.warning(
                            "Whisper fp16 decoding failed on %s with %s; "
                            "retrying once with fp16=False",
                            audio_path,
                            type(decode_exc).__name__,
                        )
                        retry_kwargs = dict(whisper_kwargs)
                        retry_kwargs["fp16"] = False
                        try:
                            result = model.transcribe(str(audio_path), **retry_kwargs)  # type: ignore[attr-defined]
                        except (ValueError, RuntimeError) as retry_exc:
                            retry_message = str(retry_exc).lower()
                            if (
                                "dtype" in retry_message
                                or "invalid values" in retry_message
                                or "nan" in retry_message
                                or "expected scalar type" in retry_message
                            ):
                                result = retry_cpu_fp32()
                            else:
                                raise
                    elif is_numeric_cuda_failure:
                        result = retry_cpu_fp32()
                    else:
                        raise
            elapsed = time.monotonic() - started_at
            self.logger.info(
                "Whisper inference finished on %s in %.1f minutes for %s",
                resolved_device,
                elapsed / 60,
                audio_path,
            )
            if not isinstance(result, dict):
                # Defensive: some mocks/backends might return a plain string.
                result = {"text": str(result), "segments": []}
            # Whisper always reports the language it transcribed in
            # (auto-detected when `language` wasn't passed as a hint, or
            # just echoed back when it was). Stashing it here lets callers
            # that only invoke transcribe()/transcribe_segments() — i.e.
            # without touching this dict — still find out what language was
            # actually detected, e.g. to pick a matching OCR language pack
            # for on-screen burned-in text instead of assuming Chinese.
            self.last_detected_language = result.get("language")
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
