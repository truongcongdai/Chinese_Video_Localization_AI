# tests/test_timeline_service.py
from pathlib import Path
import pytest

from universal_video_ai.timeline.service import TimelineService, TimelineConfig, TimelineSegment


def test_timeline_align_transcript():
    service = TimelineService()
    transcript = "Hello world. This is a test. Thank you."
    audio_duration = 10.0

    segments = service.align_transcript(transcript, audio_duration)

    assert len(segments) == 3  # 3 sentences
    assert segments[0].start_time == 0.0
    assert segments[0].end_time == pytest.approx(10.0 / 3)
    assert "Hello" in segments[0].text


def test_timeline_generate_srt():
    segments = [
        TimelineSegment(start_time=0.0, end_time=5.0, text="Hello world"),
        TimelineSegment(start_time=5.0, end_time=10.0, text="This is a test"),
    ]
    service = TimelineService()
    srt = service.generate_srt(segments)

    assert "1" in srt
    assert "00:00:00,000 --> 00:00:05,000" in srt
    assert "Hello world" in srt


def test_timeline_generate_vtt():
    segments = [
        TimelineSegment(start_time=0.0, end_time=5.0, text="Hello world"),
    ]
    service = TimelineService()
    vtt = service.generate_vtt(segments)

    assert "WEBVTT" in vtt
    assert "00:00:00.000 --> 00:00:05.000" in vtt
    assert "Hello world" in vtt


def test_timestamp_format_rounds_instead_of_truncating():
    service = TimelineService()
    segments = [
        TimelineSegment(start_time=1.9996, end_time=2.1006, text="Rounded"),
    ]

    srt = service.generate_srt(segments)
    vtt = service.generate_vtt(segments)

    assert "00:00:02,000 --> 00:00:02,101" in srt
    assert "00:00:02.000 --> 00:00:02.101" in vtt


def test_ass_positioned_caption_uses_middle_center_anchor():
    service = TimelineService()
    ass = service.generate_ass_karaoke(
        [TimelineSegment(start_time=0.0, end_time=1.0, text="Xin chào")],
        positions={(0.0, 1.0): (110, 60)},
    )

    assert r"\an5\pos(110,60)" in ass
