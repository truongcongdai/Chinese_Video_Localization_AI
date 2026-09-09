"""Regression coverage for moving, unreadable and coloured source subtitles."""
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image, ImageDraw, ImageFont

from universal_video_ai.orchestrator.factory import create_localization_service
from universal_video_ai.orchestrator.service import LocalizationConfig
from universal_video_ai.render.text_detector import OnScreenTextDetector


def detector_with_boxes(monkeypatch, per_frame):
    detector = OnScreenTextDetector()
    monkeypatch.setattr(detector, "_get_video_dimensions", lambda _: (1080, 1920))
    monkeypatch.setattr(detector, "_extract_frame", lambda *args: True)
    monkeypatch.setattr(detector, "_subtitle_presence_score_for_region", lambda *args: 0.02)
    monkeypatch.setattr(detector, "_detect_boxes_in_frame",
                        lambda path: per_frame[int(path.stem.split("_")[1])])
    return detector


def test_cover_detection_does_not_require_recognized_text(monkeypatch):
    detector = OnScreenTextDetector()
    def no_recognition(*args):
        raise AssertionError("Cover geometry must not invoke text recognition")
    detector._reader = SimpleNamespace(
        readtext=no_recognition,
        detect=lambda _: (
            [[[100, 260, 600, 640], [5, 5, 10, 15]]],
            [[[[300, 610], [440, 600], [450, 635], [310, 645]]]],
        ),
    )
    assert detector._detect_boxes_in_frame(Path("frame.png")) == [
        (100, 600, 260, 640), (300, 600, 450, 645),
    ]


@pytest.mark.parametrize("moving_box", [
    (240, 1120, 840, 1180), (260, 620, 820, 690), (500, 620, 545, 665),
])
def test_local_geometry_follows_moving_and_short_captions(monkeypatch, moving_box):
    detector = detector_with_boxes(monkeypatch, [
        [(240, 1400, 840, 1460)], [(240, 1400, 840, 1460)], [moving_box],
    ])
    regions = detector.detect_regions_for_windows(
        Path("source.mp4"), [(0, 1), (1, 2), (2, 3)], samples_per_window=1,
        fill_undetected_windows=False,
    )
    assert len(regions) == 3
    moved = regions[-1]
    assert moved.x <= moving_box[0] and moved.y <= moving_box[1]
    assert moved.x + moved.width >= moving_box[2]
    assert moved.y + moved.height >= moving_box[3]


def test_missing_caption_uses_nearest_layout_instead_of_global_bottom(monkeypatch):
    detector = detector_with_boxes(monkeypatch, [
        [(240, 1400, 840, 1460)], [(240, 1400, 840, 1460)],
        [(260, 620, 820, 690)], [],
    ])
    regions = detector.detect_regions_for_windows(
        Path("source.mp4"), [(0, 1), (1, 2), (10, 11), (11, 12)],
        samples_per_window=1,
    )
    assert len(regions) == 4
    assert regions[-1].y == regions[-2].y == 610


def test_explicit_candidate_area_and_exclusions_still_apply(monkeypatch):
    detector = detector_with_boxes(monkeypatch, [[(260, 620, 820, 690)]])
    assert detector.detect_regions_for_windows(
        Path("source.mp4"), [(0, 1)], samples_per_window=1,
        subtitle_candidate_region_fractional=(0.06, 0.55, 0.94, 0.96),
    ) == []
    assert detector.detect_regions_for_windows(
        Path("source.mp4"), [(0, 1)], samples_per_window=1,
        exclude_regions_fractional=[(0, 0.2, 1, 0.5)],
    ) == []


@pytest.mark.parametrize("colour", [(255, 255, 0), (255, 80, 80), (0, 255, 255)])
def test_coloured_subtitle_presence_keeps_object_rejection(tmp_path, colour):
    detector = OnScreenTextDetector()
    try:
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", 56)
    except OSError:
        font = ImageFont.load_default(size=56)
    frame = tmp_path / "caption.png"
    image = Image.new("RGB", (1280, 720), (25, 25, 30))
    draw = ImageDraw.Draw(image)
    draw.text((300, 600), "Subtitle text here now", fill=colour, font=font,
              stroke_width=4, stroke_fill=(0, 0, 0))
    image.save(frame)
    assert detector._subtitle_presence_score(frame) > 0.012
    image = Image.new("RGB", (1280, 720), (25, 25, 30))
    ImageDraw.Draw(image).rectangle((360, 610, 920, 660), fill=colour)
    image.save(frame)
    assert detector._subtitle_presence_score(frame) < 0.012


def test_factory_uses_config_sample_default_and_preserves_explicit_override():
    assert create_localization_service().config.text_cover_samples_per_segment == (
        LocalizationConfig().text_cover_samples_per_segment
    )
    assert create_localization_service(text_cover_samples_per_segment=1).config.text_cover_samples_per_segment == 1


def test_moving_title_is_not_merged_into_dialogue_cover(monkeypatch):
    caption = (480, 650, 800, 688)
    detector = detector_with_boxes(monkeypatch, [
        [caption], [caption],
        [caption, (1030, 580, 1250, 680), (40, 530, 430, 575)],
    ])
    monkeypatch.setattr(detector, "_get_video_dimensions", lambda _: (1280, 720))
    regions = detector.detect_regions_for_windows(
        Path("source.mp4"), [(0, 1), (1, 2), (2, 3)], samples_per_window=1,
        fill_undetected_windows=False,
    )
    assert len(regions) == 3
    assert all(r.y == 640 and r.height == 58 and r.x == 470 for r in regions)


def test_default_translated_captions_do_not_follow_ocr():
    assert LocalizationConfig().place_subtitles_in_text_cover_boxes is False
