# tests/test_mixer_service.py
from pathlib import Path
import pytest

from universal_video_ai.mixer.service import (
    MixerService, MixerConfig, AudioMix, DubbedBackgroundMix, TimedAudioClip,
)


def test_mixer_no_secondary_returns_primary(tmp_path: Path):
    primary = tmp_path / "primary.wav"
    primary.write_bytes(b"\x00" * 1024)

    service = MixerService()
    spec = AudioMix(primary_audio=primary, secondary_audio=None)
    result = service.mix(spec, tmp_path / "output.wav")

    assert result == primary


def test_mixer_with_secondary_requires_ffmpeg(tmp_path: Path, monkeypatch):
    primary = tmp_path / "primary.wav"
    secondary = tmp_path / "secondary.wav"
    primary.write_bytes(b"\x00" * 1024)
    secondary.write_bytes(b"\x00" * 1024)

    # Mock ffmpeg unavailable
    import shutil
    monkeypatch.setattr(shutil, "which", lambda x: None)

    service = MixerService()
    spec = AudioMix(primary_audio=primary, secondary_audio=secondary)

    with pytest.raises(RuntimeError):
        service.mix(spec, tmp_path / "output.wav")


def test_mix_dub_with_background_loops_and_ducks_music(tmp_path: Path, monkeypatch) -> None:
    voice = tmp_path / "voice.wav"
    music = tmp_path / "music.mp3"
    output = tmp_path / "safe.wav"
    voice.write_bytes(b"voice")
    music.write_bytes(b"music")

    service = MixerService()
    monkeypatch.setattr(service, "_ffmpeg_available", True)
    captured = {}

    def fake_run(cmd, op_name):
        captured["cmd"] = cmd
        captured["op_name"] = op_name
        output.write_bytes(b"mixed")

    monkeypatch.setattr(service, "_run_ffmpeg", fake_run)
    result = service.mix_dub_with_background(
        DubbedBackgroundMix(
            voice_audio=voice,
            background_audio=music,
            total_duration=12.0,
        ),
        output,
    )

    command = captured["cmd"]
    filter_graph = command[command.index("-filter_complex") + 1]
    assert result == output.resolve()
    assert "-stream_loop" in command
    assert "sidechaincompress" in filter_graph
    assert "alimiter" in filter_graph


def test_mix_dub_with_background_rejects_invalid_volume(tmp_path: Path, monkeypatch) -> None:
    service = MixerService()
    monkeypatch.setattr(service, "_ffmpeg_available", True)

    with pytest.raises(ValueError, match="background_volume"):
        service.mix_dub_with_background(
            DubbedBackgroundMix(
                voice_audio=tmp_path / "voice.wav",
                background_audio=tmp_path / "music.mp3",
                total_duration=10.0,
                background_volume=1.5,
            ),
            tmp_path / "output.wav",
        )


def test_build_source_effects_bed_mixes_non_vocal_stems(tmp_path: Path, monkeypatch) -> None:
    stems = []
    for name in ("drums.wav", "bass.wav", "other.wav"):
        path = tmp_path / name
        path.write_bytes(b"stem")
        stems.append(path)
    output = tmp_path / "source_effects.wav"

    service = MixerService()
    monkeypatch.setattr(service, "_ffmpeg_available", True)
    captured = {}

    def fake_run(cmd, op_name):
        captured["cmd"] = cmd
        captured["op_name"] = op_name
        output.write_bytes(b"effects")

    monkeypatch.setattr(service, "_run_ffmpeg", fake_run)

    result = service.build_source_effects_bed(stems, 9.5, output, volume=0.7)

    command = captured["cmd"]
    filter_graph = command[command.index("-filter_complex") + 1]
    assert result == output.resolve()
    assert captured["op_name"] == "build_source_effects_bed"
    assert "amix=inputs=3" in filter_graph
    assert "volume=0.7000" in filter_graph


def test_build_dubbed_track_trims_clip_before_next_start(tmp_path: Path, monkeypatch) -> None:
    first = tmp_path / "first.wav"
    second = tmp_path / "second.wav"
    output = tmp_path / "dub.wav"
    first.write_bytes(b"first")
    second.write_bytes(b"second")

    service = MixerService(MixerConfig(min_tts_gap_seconds=0.03))
    monkeypatch.setattr(service, "_ffmpeg_available", True)
    monkeypatch.setattr(service, "_probe_duration", lambda path: 1.2 if path == first else 0.4)
    captured = {}

    def fake_run(cmd, op_name):
        captured["cmd"] = cmd
        output.write_bytes(b"dub")

    monkeypatch.setattr(service, "_run_ffmpeg", fake_run)

    result = service.build_dubbed_track(
        [
            TimedAudioClip(start=1.0, end=2.0, audio_path=first),
            TimedAudioClip(start=2.0, end=2.6, audio_path=second),
        ],
        total_duration=3.0,
        output_path=output,
    )

    filter_graph = captured["cmd"][captured["cmd"].index("-filter_complex") + 1]
    assert result == output.resolve()
    assert "atempo=1.2371" in filter_graph
    assert "atrim=duration=0.970" in filter_graph
    assert "adelay=1000|1000" in filter_graph
