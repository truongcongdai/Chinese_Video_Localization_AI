# src/universal_video_ai/mixer/service.py
from __future__ import annotations

from dataclasses import dataclass
import json
import logging
from pathlib import Path
from typing import Optional, List
import subprocess
import shutil

__all__ = [
    "MixerService", "MixerConfig", "AudioMix", "TimedAudioClip",
    "DubbedBackgroundMix", "DubbedSourceBackgroundMix",
]

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AudioMix:
    """Specification for mixing audio streams.

    `mix_level` is the volume weight given to `primary_audio` (the ORIGINAL,
    untranslated audio); `secondary_audio` (the dubbed/TTS track) gets
    `1 - mix_level`.

    For a localized/dubbed video, the translated voice is the thing viewers
    actually need to understand, so it should be the DOMINANT track — the
    original audio should be ducked low (kept only for ambient sound/music
    under the dub), not the other way around. The previous default of 0.7
    gave the ORIGINAL language 70% and the dub only 30%, which is why the
    dubbed voice was nearly inaudible under the original dialogue. 0.18
    keeps a bit of the original's ambience/music audible without competing
    with the dub for intelligibility.
    """

    primary_audio: Path  # original audio
    secondary_audio: Optional[Path] = None  # TTS/translation
    mix_level: float = 0.18  # volume of primary/original (0-1); secondary/dub gets 1-mix_level


@dataclass(frozen=True)
class TimedAudioClip:
    """A single dubbed-sentence audio clip anchored to the original video's timeline.

    Attributes:
        start: when this clip should start playing, in seconds, relative to
               the original video (i.e. the same timestamp the source
               sentence started at).
        end: when this clip's slot ends, in seconds. Used only to know how
             much room is available before the next sentence; the clip will
             be time-stretched/compressed to fit `end - start` if its actual
             rendered duration differs.
        audio_path: path to the synthesized (TTS) audio file for this clip.
    """

    start: float
    end: float
    audio_path: Path


@dataclass(frozen=True)
class DubbedBackgroundMix:
    """A clean dub mixed with a separately licensed background track."""

    voice_audio: Path
    background_audio: Path
    total_duration: float
    background_volume: float = 0.16
    fade_seconds: float = 0.75


@dataclass(frozen=True)
class DubbedSourceBackgroundMix:
    """Dub mixed with ducked original ambience/SFX and optional new music."""

    voice_audio: Path
    source_audio: Path
    total_duration: float
    source_volume: float = 1.0
    background_audio: Optional[Path] = None
    background_volume: float = 0.16
    fade_seconds: float = 0.75


@dataclass
class MixerConfig:
    """Configuration for mixer service."""

    output_format: str = "wav"
    sample_rate: int = 44100
    # Keep dubbed speech cadence natural. The old behavior stretched every
    # clip exactly into its source timestamp slot, which made short translated
    # lines drag slowly and long lines rush. By default we never slow TTS down
    # and only allow mild speed-up for overcrowded slots.
    min_tts_tempo: float = 1.0
    max_tts_tempo: float = 1.30


class MixerService:
    """Service for mixing audio streams.

    Responsibilities:
    - Combine original audio with translated/TTS audio.
    - Adjust volume levels.
    - Output mixed audio.
    """

    def __init__(self, config: Optional[MixerConfig] = None, logger: Optional[logging.Logger] = None) -> None:
        self.config = config or MixerConfig()
        self.logger = logger or _logger
        self._ffmpeg_available = shutil.which("ffmpeg") is not None
        self.logger.debug("MixerService initialized output_format=%s sample_rate=%s ffmpeg=%s",
                          self.config.output_format, self.config.sample_rate, self._ffmpeg_available)

    def mix(self, mix_spec: AudioMix, output_path: Path) -> Path:
        """
        Mix audio streams and write output.

        If secondary_audio is None, simply returns the primary audio path.
        Otherwise, uses FFmpeg to mix the two with specified levels.

        :param mix_spec: AudioMix specification
        :param output_path: where to save mixed audio
        :return: output_path
        """
        output_path = Path(output_path).resolve()

        # If no secondary audio, just copy primary
        if mix_spec.secondary_audio is None:
            self.logger.info("MixerService.mix: no secondary audio, returning primary only")
            return mix_spec.primary_audio

        if not self._ffmpeg_available:
            self.logger.error("FFmpeg not available; cannot mix audio")
            raise RuntimeError("FFmpeg not available in PATH")

        self.logger.info("MixerService.mix: mixing %s + %s -> %s (mix_level=%.2f)",
                         mix_spec.primary_audio, mix_spec.secondary_audio, output_path, mix_spec.mix_level)

        # Build FFmpeg command to mix two audio streams
        # Filter: amix=inputs=2:duration=first (mix two inputs, use duration of first)
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "error",
            "-i", str(mix_spec.primary_audio),
            "-i", str(mix_spec.secondary_audio),
            "-filter_complex",
            # `volume=` on each input already sets the exact primary/secondary
            # balance we want (they sum to 1.0), so we disable amix's default
            # `normalize` behavior (which would otherwise divide both inputs
            # by the input count again and quietly halve overall loudness,
            # on top of the balance we already applied).
            f"[0:a]volume={mix_spec.mix_level}[a0];[1:a]volume={1 - mix_spec.mix_level}[a1];"
            f"[a0][a1]amix=inputs=2:duration=first:normalize=0[out]",
            "-map", "[out]",
            "-ar", str(self.config.sample_rate),
            "-y",
            str(output_path),
        ]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=600)
            if result.returncode != 0:
                stderr = result.stderr or result.stdout or "unknown error"
                self.logger.error("FFmpeg mix failed: %s", stderr)
                raise RuntimeError(f"FFmpeg mix failed: {stderr}")
            self.logger.info("MixerService.mix: success")
            return output_path
        except subprocess.TimeoutExpired:
            self.logger.error("FFmpeg mix timed out")
            raise RuntimeError("FFmpeg mix timed out")
        except Exception as exc:
            self.logger.exception("Unexpected error during mix: %s", exc)
            raise RuntimeError(f"Audio mix failed: {exc}") from exc

    def mix_dub_with_background(self, spec: DubbedBackgroundMix, output_path: Path) -> Path:
        """Loop licensed music under a dub and duck it whenever speech is active."""
        if not self._ffmpeg_available:
            raise RuntimeError("FFmpeg not available in PATH")
        if not 0.0 <= spec.background_volume <= 1.0:
            raise ValueError("background_volume must be between 0 and 1")

        output_path = Path(output_path).resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        duration = max(0.01, spec.total_duration)
        fade = max(0.0, min(spec.fade_seconds, duration / 2.0))
        fade_out_start = max(0.0, duration - fade)
        music_filters = [
            f"atrim=duration={duration:.3f}",
            "asetpts=PTS-STARTPTS",
            f"volume={spec.background_volume:.4f}",
        ]
        if fade > 0:
            music_filters.extend([
                f"afade=t=in:st=0:d={fade:.3f}",
                f"afade=t=out:st={fade_out_start:.3f}:d={fade:.3f}",
            ])

        filter_complex = (
            f"[0:a]atrim=duration={duration:.3f},asetpts=PTS-STARTPTS,"
            "asplit=2[voice][sidechain];"
            f"[1:a]{','.join(music_filters)}[music];"
            "[music][sidechain]sidechaincompress="
            "threshold=0.025:ratio=10:attack=20:release=450[ducked];"
            "[voice][ducked]amix=inputs=2:duration=first:normalize=0,"
            "alimiter=limit=0.95:attack=5:release=50[out]"
        )
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-i", str(spec.voice_audio),
            "-stream_loop", "-1", "-i", str(spec.background_audio),
            "-filter_complex", filter_complex,
            "-map", "[out]", "-t", f"{duration:.3f}",
            "-ar", str(self.config.sample_rate), "-y", str(output_path),
        ]
        self._run_ffmpeg(cmd, "mix_dub_with_background")
        return output_path

    def mix_dub_with_source_and_background(self, spec: DubbedSourceBackgroundMix, output_path: Path) -> Path:
        """Keep source ambience/SFX under the dub and optionally add replacement music.

        This is intentionally not a promise of perfect music removal. Without
        a reliable source-separation model, source SFX and source music live in
        the same mixed audio. The safest default is to preserve that source
        bed quietly, heavily ducked under the translated voice, and add a
        licensed replacement track when available.
        """
        if not self._ffmpeg_available:
            raise RuntimeError("FFmpeg not available in PATH")
        if not 0.0 <= spec.source_volume <= 1.0:
            raise ValueError("source_volume must be between 0 and 1")
        if not 0.0 <= spec.background_volume <= 1.0:
            raise ValueError("background_volume must be between 0 and 1")

        output_path = Path(output_path).resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        duration = max(0.01, spec.total_duration)

        inputs = [
            "-i", str(spec.voice_audio),
            "-i", str(spec.source_audio),
        ]
        filter_parts = [
            f"[0:a]atrim=duration={duration:.3f},asetpts=PTS-STARTPTS,asplit=2[voice][sidechain]",
            f"[1:a]atrim=duration={duration:.3f},asetpts=PTS-STARTPTS,volume={spec.source_volume:.4f}[srcquiet]",
            "[srcquiet][sidechain]sidechaincompress=threshold=0.020:ratio=12:attack=15:release=350[srcduck]",
        ]
        mix_inputs = "[voice][srcduck]"
        input_count = 2

        if spec.background_audio is not None:
            fade = max(0.0, min(spec.fade_seconds, duration / 2.0))
            fade_out_start = max(0.0, duration - fade)
            music_filters = [
                f"atrim=duration={duration:.3f}",
                "asetpts=PTS-STARTPTS",
                f"volume={spec.background_volume:.4f}",
            ]
            if fade > 0:
                music_filters.extend([
                    f"afade=t=in:st=0:d={fade:.3f}",
                    f"afade=t=out:st={fade_out_start:.3f}:d={fade:.3f}",
                ])
            inputs.extend(["-stream_loop", "-1", "-i", str(spec.background_audio)])
            filter_parts.append(f"[2:a]{','.join(music_filters)}[music]")
            filter_parts.append(
                "[music][sidechain]sidechaincompress=threshold=0.025:ratio=10:attack=20:release=450[duckedmusic]"
            )
            mix_inputs += "[duckedmusic]"
            input_count += 1

        filter_parts.append(
            f"{mix_inputs}amix=inputs={input_count}:duration=first:normalize=0,"
            "alimiter=limit=0.95:attack=5:release=50[out]"
        )

        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            *inputs,
            "-filter_complex", ";".join(filter_parts),
            "-map", "[out]", "-t", f"{duration:.3f}",
            "-ar", str(self.config.sample_rate), "-y", str(output_path),
        ]
        self._run_ffmpeg(cmd, "mix_dub_with_source_and_background")
        return output_path

    def build_source_effects_bed(
        self,
        stems: List[Path],
        total_duration: float,
        output_path: Path,
        volume: float = 1.0,
    ) -> Path:
        """Mix non-vocal Demucs stems into one source ambience/SFX bed.

        Callers should pass only stems that do not contain lead vocals, e.g.
        drums, bass, and other. This keeps source effects/music without
        reintroducing the original spoken voice.
        """
        if not self._ffmpeg_available:
            raise RuntimeError("FFmpeg not available in PATH")
        if not stems:
            raise ValueError("stems must not be empty")
        if not 0.0 <= volume <= 1.0:
            raise ValueError("volume must be between 0 and 1")

        output_path = Path(output_path).resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        duration = max(0.01, total_duration)

        inputs: List[str] = []
        labels: List[str] = []
        filter_parts: List[str] = []
        for idx, stem in enumerate(stems):
            inputs.extend(["-i", str(stem)])
            label = f"[s{idx}]"
            filter_parts.append(
                f"[{idx}:a]atrim=duration={duration:.3f},asetpts=PTS-STARTPTS{label}"
            )
            labels.append(label)

        filter_parts.append(
            f"{''.join(labels)}amix=inputs={len(labels)}:duration=longest:normalize=0,"
            f"volume={volume:.4f},alimiter=limit=0.95:attack=5:release=50[out]"
        )

        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            *inputs,
            "-filter_complex", ";".join(filter_parts),
            "-map", "[out]", "-t", f"{duration:.3f}",
            "-ar", str(self.config.sample_rate), "-y", str(output_path),
        ]
        self._run_ffmpeg(cmd, "build_source_effects_bed")
        return output_path

    def _probe_duration(self, audio_path: Path) -> float:
        """Return duration in seconds of `audio_path` via ffprobe, or 0.0 if unknown."""
        if shutil.which("ffprobe") is None:
            self.logger.warning("ffprobe not available; cannot measure clip duration for %s", audio_path)
            return 0.0
        cmd = [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "json",
            str(audio_path),
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=30)
            if result.returncode != 0:
                self.logger.warning("ffprobe failed for %s: %s", audio_path, result.stderr)
                return 0.0
            data = json.loads(result.stdout or "{}")
            return float(data.get("format", {}).get("duration", 0.0))
        except Exception as exc:
            self.logger.warning("ffprobe duration probe failed for %s: %s", audio_path, exc)
            return 0.0

    @staticmethod
    def _atempo_chain(factor: float) -> List[str]:
        """
        Build a chain of ffmpeg `atempo` filter args so an arbitrary speed
        factor can be applied, since a single `atempo` only accepts [0.5, 2.0].

        :param factor: desired speed multiplier (>1 = faster/shorter, <1 = slower/longer)
        :return: list like ["atempo=1.5"] or ["atempo=2.0", "atempo=1.2"] for extreme factors
        """
        factor = max(0.25, min(4.0, factor))  # clamp to a sane audible range
        parts: List[str] = []
        remaining = factor
        while remaining > 2.0:
            parts.append("atempo=2.0")
            remaining /= 2.0
        while remaining < 0.5:
            parts.append("atempo=0.5")
            remaining /= 0.5
        parts.append(f"atempo={remaining:.4f}")
        return parts

    def _bounded_tts_tempo(self, factor: float) -> float:
        min_tempo = max(0.25, float(self.config.min_tts_tempo))
        max_tempo = min(4.0, float(self.config.max_tts_tempo))
        if min_tempo > max_tempo:
            min_tempo, max_tempo = max_tempo, min_tempo
        return max(min_tempo, min(max_tempo, factor))

    def build_dubbed_track(
        self,
        clips: List[TimedAudioClip],
        total_duration: float,
        output_path: Path,
    ) -> Path:
        """
        Assemble many per-sentence TTS clips into a single continuous audio
        track, placing each clip at its original sentence's start time so the
        dubbed voice stays aligned with the source video's timing.

        Each clip is time-stretched/compressed (via ffmpeg `atempo`) to fit
        the `end - start` slot of its source sentence when the synthesized
        speech doesn't naturally match that duration. Gaps between sentences
        are left silent.

        :param clips: timed clips, ideally ordered by start time (order is not required)
        :param total_duration: length in seconds of the resulting track (matches
            the original video/audio duration)
        :param output_path: where to write the assembled track
        :raises RuntimeError: if ffmpeg is unavailable or fails
        :return: output_path
        """
        output_path = Path(output_path).resolve()

        if not self._ffmpeg_available:
            self.logger.error("FFmpeg not available; cannot build dubbed track")
            raise RuntimeError("FFmpeg not available in PATH")

        if not clips:
            self.logger.warning("build_dubbed_track: no clips provided; producing silent track")
            cmd = [
                "ffmpeg", "-hide_banner", "-loglevel", "error",
                "-f", "lavfi", "-i", f"anullsrc=r={self.config.sample_rate}:cl=stereo",
                "-t", str(max(0.01, total_duration)),
                "-y", str(output_path),
            ]
            self._run_ffmpeg(cmd, "build_dubbed_track (silent)")
            return output_path

        self.logger.info(
            "MixerService.build_dubbed_track: assembling %d clips into %.2fs track -> %s",
            len(clips), total_duration, output_path,
        )

        inputs: List[str] = []
        filter_parts: List[str] = []
        mixed_labels: List[str] = []

        for idx, clip in enumerate(clips):
            slot_duration = max(0.0, clip.end - clip.start)
            actual_duration = self._probe_duration(clip.audio_path)

            inputs.extend(["-i", str(clip.audio_path)])

            stage_label = f"[{idx}:a]"
            if slot_duration > 0.05 and actual_duration > 0.05:
                factor = actual_duration / slot_duration
                bounded_factor = self._bounded_tts_tempo(factor)
                if abs(bounded_factor - factor) > 0.01:
                    self.logger.debug(
                        "Clamped TTS tempo for clip %d from %.3fx to %.3fx "
                        "(actual=%.3fs slot=%.3fs)",
                        idx,
                        factor,
                        bounded_factor,
                        actual_duration,
                        slot_duration,
                    )
                factor = bounded_factor
                # Only bother stretching if the mismatch is meaningfully audible.
                if abs(factor - 1.0) > 0.03:
                    atempo_filters = self._atempo_chain(factor)
                    chain = ",".join(atempo_filters)
                    out_label = f"[t{idx}]"
                    filter_parts.append(f"{stage_label}{chain}{out_label}")
                    stage_label = out_label

            delay_ms = max(0, int(round(clip.start * 1000)))
            delayed_label = f"[d{idx}]"
            filter_parts.append(f"{stage_label}adelay={delay_ms}|{delay_ms}{delayed_label}")
            mixed_labels.append(delayed_label)

        mix_inputs = "".join(mixed_labels)
        n = len(mixed_labels)
        # IMPORTANT: previously this used amix's default `normalize=1`
        # (divide the sum by input count) and then multiplied the result
        # back by `n` to compensate, on the assumption that only ~1 of the
        # `n` clips is ever audible at a given instant (since each clip is
        # placed at its own non-overlapping sentence slot). That assumption
        # breaks whenever two clips' audio actually overlaps in time — e.g.
        # a clip that got `atempo`-stretched and runs slightly past its
        # slot into the next clip's start. When that happens, amix's
        # normalized sum of 2+ *real* signals gets multiplied by `n`
        # (routinely 100+), producing a massive, escalating volume spike —
        # exactly the "volume suddenly jumps" symptom, worse in
        # dialogue-dense sections where overlaps are more likely.
        #
        # Fix: use `normalize=0` so amix just sums the streams as-is with
        # no automatic division. When only one clip is active this yields
        # its original (correct) volume with no multiplier needed. When
        # clips do overlap, the sum can only grow by the (small) number of
        # truly-overlapping clips, not by `n` — and `alimiter` below catches
        # any resulting peaks so overlaps can't clip/distort the output.
        filter_parts.append(
            f"{mix_inputs}amix=inputs={n}:duration=longest:dropout_transition=0:normalize=0[summed]"
        )
        filter_parts.append("[summed]alimiter=limit=0.95:attack=5:release=50[mixed]")

        filter_complex = ";".join(filter_parts)

        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            *inputs,
            "-filter_complex", filter_complex,
            "-map", "[mixed]",
            "-t", str(max(0.01, total_duration)),
            "-ar", str(self.config.sample_rate),
            "-y", str(output_path),
        ]

        self._run_ffmpeg(cmd, "build_dubbed_track")
        return output_path

    def _run_ffmpeg(self, cmd: List[str], op_name: str) -> None:
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=1800)
            if result.returncode != 0:
                stderr = result.stderr or result.stdout or "unknown error"
                self.logger.error("FFmpeg %s failed: %s", op_name, stderr)
                raise RuntimeError(f"FFmpeg {op_name} failed: {stderr}")
            self.logger.info("MixerService.%s: success", op_name)
        except subprocess.TimeoutExpired:
            self.logger.error("FFmpeg %s timed out", op_name)
            raise RuntimeError(f"FFmpeg {op_name} timed out")
