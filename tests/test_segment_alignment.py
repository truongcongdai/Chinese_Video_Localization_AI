# tests/test_segment_alignment.py
"""
Tests for the new timestamp-alignment pipeline: Whisper segment extraction,
segment-level translation, timeline-from-segments, and timed-track assembly.

These cover the core fix requested: sentence N's translation/dub/subtitle
must keep sentence N's original start/end timestamps instead of being
evenly re-split or read back-to-back from t=0.
"""
from pathlib import Path
from unittest.mock import MagicMock
import asyncio

import pytest

from universal_video_ai.segment import TranscriptSegment, UNKNOWN_TIMING
from universal_video_ai.speech.whisper import WhisperTranscriber, WhisperConfig
from universal_video_ai.translate.service import TranslateService
from universal_video_ai.timeline.service import TimelineService
from universal_video_ai.mixer.service import MixerService, TimedAudioClip
from universal_video_ai.render.renderer import Renderer, TextOverlay
from universal_video_ai.orchestrator.service import LocalizationService


def test_whisper_transcribe_segments_preserves_timestamps(tmp_path: Path, monkeypatch):
    """Whisper's own segment start/end must survive, not be discarded."""
    audio_file = tmp_path / "audio.wav"
    audio_file.write_bytes(b"fake audio")

    fake_result = {
        "text": "Hello world. This is a test.",
        "segments": [
            {"start": 0.0, "end": 3.2, "text": "Hello world."},
            {"start": 3.2, "end": 7.5, "text": "This is a test."},
        ],
    }

    def fake_run_whisper(self, audio_path, language=None):
        return fake_result

    monkeypatch.setattr(WhisperTranscriber, "_run_whisper", fake_run_whisper)

    transcriber = WhisperTranscriber(config=WhisperConfig(model="tiny"))
    segments = transcriber.transcribe_segments(audio_file)

    assert len(segments) == 2
    assert segments[0].start == 0.0
    assert segments[0].end == 3.2
    assert segments[1].start == 3.2
    assert segments[1].end == 7.5
    assert all(s.has_timing for s in segments)


def test_whisper_transcribe_segments_falls_back_to_unknown_timing(monkeypatch, tmp_path: Path):
    """If a backend/mocked result has no segments, we still return the text (unknown timing)."""
    audio_file = tmp_path / "audio.wav"
    audio_file.write_bytes(b"fake audio")

    def fake_run_whisper(self, audio_path, language=None):
        return {"text": "flat text only", "segments": []}

    monkeypatch.setattr(WhisperTranscriber, "_run_whisper", fake_run_whisper)

    transcriber = WhisperTranscriber()
    segments = transcriber.transcribe_segments(audio_file)

    assert len(segments) == 1
    assert segments[0].end == UNKNOWN_TIMING
    assert not segments[0].has_timing
    assert segments[0].text == "flat text only"


def test_translate_segments_preserves_timestamps():
    """Each translated segment must keep the SOURCE segment's start/end."""

    class FakeBackend:
        async def translate(self, text, source_lang, target_lang):
            return f"[{target_lang}] {text}"

    svc = TranslateService(backend=FakeBackend())
    segments = [
        TranscriptSegment(start=0.0, end=3.0, text="Hello"),
        TranscriptSegment(start=3.0, end=6.5, text="World"),
    ]

    result = asyncio.run(svc.translate_segments(segments, source_lang="en", target_lang="vi"))

    assert [s.start for s in result] == [0.0, 3.0]
    assert [s.end for s in result] == [3.0, 6.5]
    assert result[0].text == "[vi] Hello"
    assert result[1].text == "[vi] World"


def test_translate_segments_limits_concurrency():
    class CountingBackend:
        def __init__(self):
            self.active = 0
            self.peak = 0

        async def translate(self, text, source_lang, target_lang):
            self.active += 1
            self.peak = max(self.peak, self.active)
            await asyncio.sleep(0.001)
            self.active -= 1
            return text

    backend = CountingBackend()
    svc = TranslateService(backend=backend, max_concurrency=3)
    segments = [TranscriptSegment(start=i, end=i + 1, text=str(i)) for i in range(20)]

    asyncio.run(svc.translate_segments(segments, source_lang="zh-cn", target_lang="vi"))

    assert backend.peak <= 3


def test_timeline_from_segments_uses_real_timestamps_not_even_split():
    """This is the exact bug report: subtitles must NOT be evenly re-split."""
    service = TimelineService()
    segments = [
        TranscriptSegment(start=0.0, end=2.0, text="Short first line"),
        TranscriptSegment(start=2.0, end=9.0, text="A much longer second line spoken slowly"),
    ]

    timeline_segments = service.from_segments(segments, audio_duration=9.0)

    assert len(timeline_segments) == 2
    # Unlike align_transcript's even split (which would give 4.5s/4.5s),
    # the real, unequal source timings must be preserved.
    assert timeline_segments[0].start_time == 0.0
    assert timeline_segments[0].end_time == 2.0
    assert timeline_segments[1].start_time == 2.0
    assert timeline_segments[1].end_time == 9.0


def test_mixer_build_dubbed_track_places_clips_at_original_timestamps(tmp_path: Path, monkeypatch):
    """The dubbed track must place each clip at ITS sentence's start time, not back-to-back."""
    clip_a = tmp_path / "a.wav"
    clip_b = tmp_path / "b.wav"
    clip_a.write_bytes(b"\x00")
    clip_b.write_bytes(b"\x00")

    mixer = MixerService()
    monkeypatch.setattr(mixer, "_ffmpeg_available", True)
    monkeypatch.setattr(mixer, "_probe_duration", lambda path: 2.0)

    captured_cmd = {}

    def fake_run_ffmpeg(cmd, op_name):
        captured_cmd["cmd"] = cmd
        Path(cmd[-1]).write_bytes(b"mixed")

    monkeypatch.setattr(mixer, "_run_ffmpeg", fake_run_ffmpeg)

    clips = [
        TimedAudioClip(start=0.0, end=2.0, audio_path=clip_a),
        TimedAudioClip(start=10.0, end=12.0, audio_path=clip_b),
    ]
    output = tmp_path / "dubbed.wav"
    result = mixer.build_dubbed_track(clips, total_duration=15.0, output_path=output)

    assert result == output
    filter_complex = captured_cmd["cmd"][captured_cmd["cmd"].index("-filter_complex") + 1]
    # clip at start=10.0 must be delayed by 10000ms, not concatenated at 2000ms
    assert "adelay=10000|10000" in filter_complex
    assert "adelay=0|0" in filter_complex


def test_renderer_builds_per_segment_overlay_filters():
    """Each TextOverlay must only be active during its own [start, end] window."""
    renderer = Renderer()
    overlays = [
        TextOverlay(start=0.0, end=3.0, x=10, y=20, width=200, height=40, text="Xin chào"),
        TextOverlay(start=5.0, end=8.0, x=10, y=20, width=200, height=40, text="Tạm biệt"),
    ]

    filters = renderer._build_text_overlay_filters(overlays)
    combined = ";".join(filters)

    assert "between(t\\,0.000\\,3.000)" in combined
    assert "between(t\\,5.000\\,8.000)" in combined
    assert "drawbox" in combined
    assert "drawtext" in combined


def test_overlay_text_is_centered_inside_detected_cover_box():
    renderer = Renderer()
    overlay = TextOverlay(
        start=1.0, end=2.0, x=100, y=300, width=240, height=56,
        text="Mới",
    )

    filters = renderer._build_text_overlay_filters([overlay], frame_w=1080)
    drawtext = next(item for item in filters if item.startswith("drawtext="))

    assert "text='Mới'" in drawtext
    assert "x=100+(240-text_w)/2" in drawtext
    assert "y=300+(56-text_h)/2" in drawtext


def test_gap_fill_only_returns_segments_without_an_overlay():
    timeline = TimelineService()
    segments = timeline.from_segments([
        TranscriptSegment(start=0.0, end=2.0, text="Đã được OCR che"),
        TranscriptSegment(start=2.0, end=4.0, text="OCR bỏ sót"),
    ], audio_duration=4.0)
    overlays = [
        TextOverlay(start=0.0, end=2.0, x=10, y=20, width=200, height=40, text="Đã được OCR che"),
    ]

    uncovered = LocalizationService._filter_uncovered_subtitle_segments(segments, overlays)

    assert [segment.text for segment in uncovered] == ["OCR bỏ sót"]


def test_ass_karaoke_can_be_centered_in_ocr_box_with_one_font_size():
    timeline = TimelineService()
    segments = timeline.from_segments([
        TranscriptSegment(start=0.0, end=2.0, text="Xin chào bạn"),
        TranscriptSegment(start=2.0, end=4.0, text="Câu tiếp theo"),
    ], audio_duration=4.0)

    ass = timeline.generate_ass_karaoke(
        segments,
        frame_width=1080,
        frame_height=1920,
        # The first OCR window is deliberately wider than its child cue;
        # overlap matching must still position that karaoke cue correctly.
        positions={(0.0, 2.5): (540, 920), (2.5, 4.0): (540, 930)},
        font_size=34,
    )

    assert "Style: Default,DejaVu Sans,34," in ass
    assert r"\an5\pos(540,920)" in ass
    assert r"\an5\pos(540,930)" in ass
    assert r"\kf" in ass
