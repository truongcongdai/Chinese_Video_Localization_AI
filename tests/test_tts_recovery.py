"""Missing speech must be recovered or block rendering, never silently skipped."""
import asyncio
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from universal_video_ai.orchestrator.service import LocalizationConfig, LocalizationService
from universal_video_ai.segment import TranscriptSegment
from universal_video_ai.mixer.service import TimedAudioClip
from universal_video_ai.tts.tts import EdgeTTS


@pytest.mark.parametrize("failures", [1, 99])
def test_recovery_preserves_sentence_identity_despite_overlapping_times(tmp_path, monkeypatch, failures):
    calls = Counter()
    def synthesize(text, **kwargs):
        calls[text] += 1
        if text == "B" and calls[text] <= failures:
            raise RuntimeError("NoAudioReceived")
        actual = tmp_path / (text + ".wav")
        actual.write_bytes(b"audio")
        return actual
    service = LocalizationService(tts_service=SimpleNamespace(synthesize=synthesize),
        mixer=MagicMock(), config=LocalizationConfig(require_complete_tts_coverage=False))
    def schedule(items, total_duration):
        assert all(path.name == seg.text + ".wav" for _, seg, path in items)
        return ([TimedAudioClip(start=s.start, end=s.end, audio_path=p) for _, s, p in items],
                [s for _, s, _ in items])
    monkeypatch.setattr(service, "_schedule_tts_clips", schedule)
    segments = [TranscriptSegment(0, 3, "A"), TranscriptSegment(1, 2, "B")]
    task = service._synthesize_timed_track_async(segments, 3, tmp_path, "vi")
    if failures == 1:
        asyncio.run(task)
        assert [s.text for s in service._last_tts_playback_segments] == ["A", "B"]
        service.mixer.build_dubbed_track.assert_called_once()
    else:
        with pytest.raises(RuntimeError, match="sentence.*2"):
            asyncio.run(task)
        service.mixer.build_dubbed_track.assert_not_called()
    assert calls == {"A": 1, "B": 2}


def test_legacy_sync_never_silently_skips_missing_voice(tmp_path):
    service = LocalizationService(tts_service=MagicMock(), mixer=MagicMock())
    service.tts_service.synthesize.side_effect = RuntimeError("NoAudioReceived")
    with pytest.raises(RuntimeError, match="sentence 1"):
        service._synthesize_timed_track([TranscriptSegment(0, 1, "A")], 1, tmp_path, "vi")
    service.mixer.build_dubbed_track.assert_not_called()


def test_edge_checkpoint_checks_full_input_and_intact_audio(tmp_path, monkeypatch):
    module = "universal_video_ai.tts.tts"
    calls = []
    monkeypatch.setattr(module + "._check_edge_tts_available", lambda: True)
    monkeypatch.setattr(module + "._validate_audio_file", lambda *args: 1.0)
    def run(cmd, **kwargs):
        calls.append(cmd)
        Path(cmd[cmd.index("--write-media") + 1]).write_bytes(b"valid audio")
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    monkeypatch.setattr(module + ".subprocess.run", run)
    edge = EdgeTTS(max_retries=1)
    output = tmp_path / "sentence.wav"
    edge.synthesize("hello", output, voice="voice-a")
    edge.synthesize("hello", output, voice="voice-a")
    assert len(calls) == 1
    edge.synthesize("hello changed", output, voice="voice-a")
    edge.synthesize("hello changed", output, voice="voice-b")
    edge.synthesize("hello changed", output, voice="voice-b", rate="+5%")
    assert len(calls) == 4
    output.write_bytes(b"partial")
    edge.synthesize("hello changed", output, voice="voice-b", rate="+5%")
    assert len(calls) == 5
    output.with_suffix(".wav.tts.json").write_text("null", encoding="utf-8")
    edge.synthesize("hello changed", output, voice="voice-b", rate="+5%")
    assert len(calls) == 6

