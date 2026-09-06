from universal_video_ai.timeline.service import TimelineSegment, TimelineService


def test_ass_font_scales_for_small_landscape_video():
    content = TimelineService().generate_ass_karaoke(
        [TimelineSegment(0.0, 2.0, "Xin chào, chào mừng bạn đến đây.")],
        frame_width=640,
        frame_height=360,
    )
    assert "Style: Default,DejaVu Sans,23," in content
    assert "Dialogue: 0,0:00:00.00,0:00:02.00" in content


def test_ass_first_cue_at_zero_is_not_shifted_or_dropped():
    content = TimelineService().generate_ass_karaoke(
        [TimelineSegment(0.0, 0.5, "Bắt đầu")],
        frame_width=1080,
        frame_height=1920,
    )
    assert "Dialogue: 0,0:00:00.00,0:00:00.50" in content
