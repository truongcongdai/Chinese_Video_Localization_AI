from pathlib import Path
from types import SimpleNamespace

from universal_video_ai.models import TranscriptSegment
from universal_video_ai.orchestrator.service import LocalizationService


def test_media_duration_uses_ffprobe_for_mislabeled_tts(monkeypatch, tmp_path: Path):
    clip = tmp_path / "segment.wav"
    clip.write_bytes(b"not-a-riff-provider-payload")

    monkeypatch.setattr(
        "universal_video_ai.orchestrator.service.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(stdout="2.375\n"),
    )
    assert LocalizationService._wav_duration(clip) == 2.375


def test_schedule_never_advertises_end_before_real_audio(monkeypatch, tmp_path: Path):
    service = object.__new__(LocalizationService)
    service.config = SimpleNamespace(tts_neighbor_gap_seconds=0.03, tts_max_speed_ratio=1.0)
    service.logger = SimpleNamespace(warning=lambda *args, **kwargs: None)

    paths = [tmp_path / "a.wav", tmp_path / "b.wav"]
    for path in paths:
        path.write_bytes(b"audio")
    durations = {paths[0]: 1.8, paths[1]: 1.4}
    monkeypatch.setattr(service, "_wav_duration", lambda path: durations[path])

    clips, playback = service._schedule_tts_clips(
        [
            (0, TranscriptSegment(start=0.0, end=1.0, text="Câu đầu đầy đủ"), paths[0]),
            (1, TranscriptSegment(start=1.0, end=2.0, text="Câu sau đầy đủ"), paths[1]),
        ],
        total_duration=4.0,
    )

    assert clips[0].end - clips[0].start == 1.8
    assert clips[1].start >= clips[0].end
    assert playback[0].start == clips[0].start
    assert playback[0].end == clips[0].end
    assert playback[1].start == clips[1].start
    assert playback[1].end == clips[1].end
