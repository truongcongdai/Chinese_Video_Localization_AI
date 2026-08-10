# src/universal_video_ai/audio/demucs.py
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import logging
import shutil
import subprocess
import tempfile
from typing import Optional

__all__ = ["DemucsProcessor", "DemucsOutput", "DemucsConfig", "DEMUCS_AVAILABLE"]

_logger = logging.getLogger(__name__)


def _check_demucs_available() -> bool:
    """
    Check if demucs is available in PATH or as Python module.

    :return: True if demucs found, False otherwise
    """
    return shutil.which("demucs") is not None


@dataclass
class DemucsConfig:
    """
    Configuration for Demucs audio separation.

    Attributes:
        model: Demucs model name (e.g., "htdemucs", "htdemucs_6stems", "mdx_q")
        output_format: output audio format ("wav", "mp3", etc.)
        device: device to use ("cpu", "cuda")
        segment_length: optional segment length in seconds for processing long files
    """

    model: str = "htdemucs"
    output_format: str = "wav"
    device: str = "cpu"
    segment_length: Optional[int] = None
    # Demucs loads an input file before its internal ``--segment`` inference,
    # so multi-hour WAV files still exhaust RAM. Inputs above this size are
    # split externally, processed sequentially by one Demucs process, then
    # concatenated back into the normal four-stem layout.
    chunk_threshold_bytes: int = 512 * 1024 * 1024
    chunk_length_seconds: int = 15 * 60
    long_audio_timeout_seconds: int = 24 * 60 * 60


@dataclass(frozen=True)
class DemucsOutput:
    """
    Output of Demucs audio separation.

    Contains paths to separated audio stems:
    - vocals: lead vocal track
    - drums: drum track
    - bass: bass track
    - other: other instruments/background
    """

    vocals: Path
    drums: Path
    bass: Path
    other: Path


class DemucsProcessor:
    """
    Separate audio into stems (vocals, drums, bass, other) using Demucs.

    Responsibilities:
    - Validate audio file exists.
    - Run demucs command-line tool to separate tracks.
    - Return paths to separated stems.
    - Handle errors and log operations.

    Notes:
    - Requires demucs to be installed: `pip install demucs`
    - Logs operations via logging module (no prints).
    """

    def __init__(self, config: Optional[DemucsConfig] = None, logger: Optional[logging.Logger] = None) -> None:
        """
        Initialize DemucsProcessor.

        :param config: DemucsConfig for model, device, format. If None, uses defaults.
        :param logger: Optional logger instance. If None, uses module logger.
        """
        self.config = config or DemucsConfig()
        self.logger = logger or _logger

        if not _check_demucs_available():
            self.logger.warning(
                "Demucs not found in PATH; separation may fail at runtime. Install with: pip install demucs")

        self.logger.debug(
            "DemucsProcessor initialized with model=%s, device=%s, output_format=%s",
            self.config.model,
            self.config.device,
            self.config.output_format,
        )

    def get_output_dir(self, audio_path: Path, base_output_dir: Optional[Path] = None) -> Path:
        """
        Compute the base output directory for demucs separation.

        Demucs creates structure: {base_output_dir}/{model}/{audio_stem}/{stems}.wav

        :param audio_path: input audio file path
        :param base_output_dir: base output directory (default: audio_path.parent / "demucs_output")
        :return: base output directory
        """
        if base_output_dir is None:
            base_output_dir = audio_path.parent / "demucs_output"
        return base_output_dir

    def _find_stem_files(self, base_dir: Path, audio_stem: str) -> Optional[DemucsOutput]:
        """
        Find the separated stem files in demucs output directory.

        Demucs output structure:
            {base_dir}/{model}/{audio_stem}/vocals.wav
            {base_dir}/{model}/{audio_stem}/drums.wav
            {base_dir}/{model}/{audio_stem}/bass.wav
            {base_dir}/{model}/{audio_stem}/other.wav

        :param base_dir: base output directory
        :param audio_stem: audio file stem (without extension)
        :return: DemucsOutput if all stems found, None otherwise
        """
        stems_dir = base_dir / self.config.model / audio_stem

        if not stems_dir.exists():
            self.logger.debug("Stems directory not found: %s", stems_dir)
            return None

        stem_files = {
            "vocals": stems_dir / f"vocals.{self.config.output_format}",
            "drums": stems_dir / f"drums.{self.config.output_format}",
            "bass": stems_dir / f"bass.{self.config.output_format}",
            "other": stems_dir / f"other.{self.config.output_format}",
        }

        # Check all stems exist
        for stem_name, stem_path in stem_files.items():
            if not stem_path.exists():
                self.logger.debug("Stem file missing: %s (%s)", stem_name, stem_path)
                return None

        return DemucsOutput(
            vocals=stem_files["vocals"],
            drums=stem_files["drums"],
            bass=stem_files["bass"],
            other=stem_files["other"],
        )

    def _build_demucs_command(self, audio_paths: list[Path], output_dir: Path) -> list[str]:
        cmd = [
            "demucs",
            "-n", self.config.model,
            "-d", self.config.device,
            "-o", str(output_dir),
        ]
        output_format = self.config.output_format.lower()
        if output_format == "mp3":
            cmd.append("--mp3")
        elif output_format == "flac":
            cmd.append("--flac")
        elif output_format != "wav":
            raise ValueError("Demucs output_format must be one of: wav, mp3, flac")

        if self.config.segment_length is not None:
            cmd.extend(["--segment", str(self.config.segment_length)])
        cmd.extend(str(path) for path in audio_paths)
        return cmd

    def _run_checked(self, cmd: list[str], *, timeout: int) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=timeout,
        )
        if result.returncode != 0:
            error_msg = result.stderr or result.stdout or "unknown error"
            self.logger.error("Command failed: %s", error_msg)
            raise RuntimeError(f"Demucs separation failed: {error_msg}")
        return result

    @staticmethod
    def _concat_manifest_line(path: Path) -> str:
        escaped = path.resolve().as_posix().replace("'", "'\\''")
        return f"file '{escaped}'\n"

    def _separate_chunked(self, audio_path: Path, output_dir: Path) -> DemucsOutput:
        if shutil.which("ffmpeg") is None:
            raise RuntimeError("ffmpeg is required to process long audio with Demucs")

        self.logger.warning(
            "Large audio input (%.2f GB); splitting into %d-second chunks before Demucs",
            audio_path.stat().st_size / (1024 ** 3), self.config.chunk_length_seconds,
        )
        final_stems_dir = output_dir / self.config.model / audio_path.stem
        final_stems_dir.mkdir(parents=True, exist_ok=True)

        with tempfile.TemporaryDirectory(prefix=f".{audio_path.stem}_chunks_", dir=output_dir) as temp_name:
            temp_dir = Path(temp_name)
            input_dir = temp_dir / "input"
            separated_dir = temp_dir / "separated"
            input_dir.mkdir()
            separated_dir.mkdir()
            chunk_pattern = input_dir / "chunk_%05d.wav"

            split_cmd = [
                "ffmpeg", "-hide_banner", "-loglevel", "error",
                "-i", str(audio_path),
                "-f", "segment",
                "-segment_time", str(self.config.chunk_length_seconds),
                "-reset_timestamps", "1",
                "-acodec", "pcm_s16le",
                "-y", str(chunk_pattern),
            ]
            split_result = subprocess.run(
                split_cmd, capture_output=True, text=True, check=False, timeout=3600,
            )
            if split_result.returncode != 0:
                error_msg = split_result.stderr or split_result.stdout or "unknown error"
                raise RuntimeError(f"Failed to split long audio for Demucs: {error_msg}")

            chunks = sorted(input_dir.glob("chunk_*.wav"))
            if not chunks:
                raise RuntimeError("ffmpeg did not create any chunks for long Demucs input")

            # Supplying every chunk to one invocation loads the model once;
            # Demucs still opens and separates the tracks one at a time.
            demucs_cmd = self._build_demucs_command(chunks, separated_dir)
            self.logger.info("Separating %d audio chunks with one Demucs process", len(chunks))
            self._run_checked(demucs_cmd, timeout=self.config.long_audio_timeout_seconds)

            for stem_name in ("vocals", "drums", "bass", "other"):
                stem_parts: list[Path] = []
                for chunk in chunks:
                    chunk_output = self._find_stem_files(separated_dir, chunk.stem)
                    if chunk_output is None:
                        raise RuntimeError(f"Demucs output missing for audio chunk {chunk.name}")
                    stem_parts.append(getattr(chunk_output, stem_name))

                manifest = temp_dir / f"{stem_name}_concat.txt"
                manifest.write_text(
                    "".join(self._concat_manifest_line(path) for path in stem_parts),
                    encoding="utf-8",
                )
                final_path = final_stems_dir / f"{stem_name}.{self.config.output_format.lower()}"
                concat_cmd = [
                    "ffmpeg", "-hide_banner", "-loglevel", "error",
                    "-f", "concat", "-safe", "0", "-i", str(manifest),
                    "-c", "copy",
                ]
                if self.config.output_format.lower() == "wav":
                    # RF64 remains valid when a multi-hour stem exceeds WAV's
                    # traditional 4 GiB RIFF limit.
                    concat_cmd.extend(["-rf64", "auto"])
                concat_cmd.extend(["-y", str(final_path)])
                concat_result = subprocess.run(
                    concat_cmd, capture_output=True, text=True, check=False, timeout=3600,
                )
                if concat_result.returncode != 0:
                    error_msg = concat_result.stderr or concat_result.stdout or "unknown error"
                    raise RuntimeError(f"Failed to concatenate Demucs {stem_name} chunks: {error_msg}")
        result = self._find_stem_files(output_dir, audio_path.stem)
        if result is None:
            raise RuntimeError("Chunked Demucs separation did not produce expected stem files")
        self.logger.info("Chunked audio separation successful: %s", final_stems_dir)
        return result

    def separate(self, audio_path: Path, output_dir: Optional[Path] = None) -> DemucsOutput:
        """
        Separate audio into stems (vocals, drums, bass, other) using Demucs.

        :param audio_path: path to input audio file
        :param output_dir: optional base output directory (default: audio_path.parent / "demucs_output")
        :return: DemucsOutput with paths to separated stems
        :raises FileNotFoundError: if audio file does not exist
        :raises RuntimeError: if demucs is unavailable or separation fails
        """
        audio_path = audio_path.resolve()

        # Validate input file exists
        if not audio_path.exists():
            raise FileNotFoundError(f"Audio file not found: {audio_path}")

        if not audio_path.is_file():
            raise FileNotFoundError(f"Path is not a file: {audio_path}")

        output_dir = self.get_output_dir(audio_path, output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        self.logger.info("Separating audio %s using Demucs model=%s", audio_path, self.config.model)

        if audio_path.stat().st_size > self.config.chunk_threshold_bytes:
            try:
                return self._separate_chunked(audio_path, output_dir)
            except subprocess.TimeoutExpired:
                self.logger.error("Chunked Demucs separation timed out")
                raise RuntimeError("Chunked Demucs separation timed out")
            except FileNotFoundError as exc:
                self.logger.error("Required audio command not found: %s", exc)
                raise RuntimeError("Demucs or ffmpeg is not installed or not in PATH") from exc
            except Exception as exc:
                self.logger.exception("Unexpected error during chunked audio separation: %s", exc)
                raise RuntimeError(f"Audio separation failed: {exc}") from exc

        # Build demucs command.
        # Demucs writes WAV by default. Current releases do not accept
        # `--format wav`; MP3/FLAC are selected with dedicated flags.
        cmd = self._build_demucs_command([audio_path], output_dir)

        self.logger.debug("Running demucs command: %s", " ".join(cmd))

        try:
            self._run_checked(cmd, timeout=3600)

            # Find separated stem files
            audio_stem = audio_path.stem
            demucs_output = self._find_stem_files(output_dir, audio_stem)

            if demucs_output is None:
                self.logger.error("Demucs output stems not found in expected location: %s", output_dir)
                raise RuntimeError(f"Demucs separation did not produce expected stem files")

            self.logger.info("Audio separation successful. Stems: vocals=%s, drums=%s, bass=%s, other=%s",
                             demucs_output.vocals, demucs_output.drums, demucs_output.bass, demucs_output.other)
            return demucs_output

        except subprocess.TimeoutExpired:
            self.logger.error("Demucs separation timed out (1 hour)")
            raise RuntimeError("Demucs separation timed out")
        except FileNotFoundError as exc:
            self.logger.error("Demucs not found: %s", exc)
            raise RuntimeError("Demucs is not installed or not in PATH. Install with: pip install demucs") from exc
        except Exception as exc:
            self.logger.exception("Unexpected error during audio separation: %s", exc)
            raise RuntimeError(f"Audio separation failed: {exc}") from exc

DEMUCS_AVAILABLE: bool = _check_demucs_available()
