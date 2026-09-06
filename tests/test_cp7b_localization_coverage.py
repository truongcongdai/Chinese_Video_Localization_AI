from universal_video_ai.localization_coverage import (
    SourceEvidence,
    reconcile_source_evidence,
    validate_timeline_coverage,
)
from universal_video_ai.segment import TranscriptSegment
from universal_video_ai.timeline.service import TimelineSegment
from universal_video_ai.orchestrator.service import LocalizationConfig, LocalizationService


def segment(start, end, text):
    return TranscriptSegment(start=start, end=end, text=text)


def test_first_segment_at_zero_is_valid_and_covered():
    report = validate_timeline_coverage(
        [SourceEvidence(0.0, 0.8, "你好", "asr")],
        [TimelineSegment(0.0, 0.8, "Xin chào")],
        localized_start_attr="start_time",
        localized_end_attr="end_time",
    )
    assert report.complete


def test_near_zero_starts_are_preserved():
    for start in (0.1, 0.5, 1.0, 2.0):
        events = reconcile_source_evidence([segment(start, start + 0.5, "源")], [])
        assert events[0].start == start


def test_ocr_only_opening_event_fills_asr_gap():
    events = reconcile_source_evidence(
        [segment(1.5, 2.5, "第二句")],
        [segment(0.0, 0.8, "第一句")],
    )
    assert [(item.detector, item.start) for item in events] == [
        ("ocr", 0.0), ("asr", 1.5)
    ]


def test_asr_only_opening_event_is_preserved():
    events = reconcile_source_evidence([segment(0.0, 0.7, "第一句")], [])
    assert len(events) == 1
    assert events[0].detector == "asr"


def test_overlapping_ocr_asr_duplicate_is_not_double_translated():
    events = reconcile_source_evidence(
        [segment(0.0, 0.8, "你好世界")],
        [segment(0.1, 0.7, "你好 世界")],
    )
    assert len(events) == 1
    assert events[0].detector == "asr"


def test_missing_localization_is_reported_with_reason():
    report = validate_timeline_coverage(
        [SourceEvidence(0.0, 0.8, "第一句", "ocr")],
        [],
    )
    assert not report.complete
    assert report.uncovered[0].source_detector == "ocr"
    assert report.uncovered[0].reason == "no_overlapping_localized_timeline_entry"


def test_subtitle_and_tts_completeness_require_every_source_window():
    source = [segment(0.0, 0.8, "A"), segment(1.0, 1.8, "B")]
    subtitles = [TimelineSegment(0.0, 0.8, "Một")]
    report = validate_timeline_coverage(
        source,
        subtitles,
        localized_start_attr="start_time",
        localized_end_attr="end_time",
    )
    assert report.coverage_ratio == 0.5
    assert report.uncovered[0].start == 1.0


class OpeningDetector:
    def read_subtitle_text_at(self, video_path, at_seconds, **kwargs):
        del video_path, kwargs
        return "第一句" if at_seconds in {0.1, 0.5} else ""


def test_opening_ocr_scan_groups_repeated_frames_from_near_zero(tmp_path):
    service = LocalizationService(
        text_detector=OpeningDetector(),
        config=LocalizationConfig(
            opening_ocr_sample_times=(0.1, 0.5, 1.0, 2.0),
        ),
    )
    segments = service._scan_opening_ocr_segments(
        tmp_path / "source.mp4", 3.0, "zh"
    )
    assert len(segments) == 1
    assert segments[0].start == 0.0
    assert segments[0].end == 0.9
    assert segments[0].text == "第一句"
