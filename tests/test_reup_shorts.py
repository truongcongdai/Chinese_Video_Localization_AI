import pytest
import json
import shutil
import subprocess

from universal_video_ai.segment import TranscriptSegment
from universal_video_ai.remix.shorts import ShortCut, ShortPlan, build_short_plan, remap_segments
from universal_video_ai.remix.shorts import render_short


@pytest.mark.parametrize("cuts", [
    (ShortCut(-1, 2),), (ShortCut(5, 4),), (ShortCut(0, 101),),
    (ShortCut(0, 8), ShortCut(6, 9)), (ShortCut(float("nan"), 8),),
    (ShortCut(0, 20), ShortCut(30, 80)),
])
def test_invalid_edl_cannot_reach_renderer(cuts):
    with pytest.raises(ValueError):
        ShortPlan(cuts, 60, 100)


def test_remap_keeps_source_dialogue_and_speaker_on_new_clock():
    plan = ShortPlan((ShortCut(10, 12), ShortCut(50, 54)), 15, 100)
    result = remap_segments([TranscriptSegment(10, 12, "setup", "speaker_0"),
                             TranscriptSegment(25, 30, "excluded"), TranscriptSegment(50, 54, "outcome", "speaker_1")], plan)
    assert [(s.start, s.end, s.text, s.speaker_id) for s in result] == [(0, 2, "setup", "speaker_0"), (2, 6, "outcome", "speaker_1")]
    assert plan.to_dict()["cuts"][1]["destination_start"] == 2


def test_summary_uses_selected_source_ids_without_inventing_timestamps():
    class Selector:
        def generate_json(self, prompt):
            return {"ids": [0, 2]}
    segments = [TranscriptSegment(0, 5, "setup"), TranscriptSegment(20, 30, "filler"), TranscriptSegment(90, 98, "outcome")]
    plan = build_short_plan(segments, 100, target_seconds=15, client=Selector())
    assert [(c.source_start, c.source_end) for c in plan.cuts] == [(0, 5), (90, 98)]
    assert plan.method == "ai_extractive_summary"


def test_failed_ai_does_not_silently_claim_semantic_summary():
    class Selector:
        def generate_json(self, prompt):
            return {"ids": [999]}
    with pytest.raises(ValueError, match="valid source segment IDs"):
        build_short_plan([TranscriptSegment(0, 5, "source")], 10, target_seconds=15, client=Selector())
    assert build_short_plan([TranscriptSegment(0, 5, "source")], 10, target_seconds=15).method == "sampled_highlight_review_required"


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="FFmpeg is not installed")
def test_real_ffmpeg_short_keeps_audio_and_outputs_one_portrait_video(tmp_path):
    source = tmp_path / "source.mp4"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "color=c=blue:s=160x90:r=30:d=6", "-f", "lavfi", "-i", "sine=frequency=440:duration=6",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(source)],
                   check=True, capture_output=True, timeout=60)
    plan = ShortPlan((ShortCut(0, 1), ShortCut(4, 5)), 10, 6)
    output = render_short(source, plan, tmp_path / "output", timeout=120)
    result = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(output)],
                            check=True, capture_output=True, text=True, timeout=30)
    data = json.loads(result.stdout)
    video = next(s for s in data["streams"] if s["codec_type"] == "video")
    assert (video["width"], video["height"]) == (720, 1280)
    assert any(s["codec_type"] == "audio" for s in data["streams"])
    assert abs(float(data["format"]["duration"]) - 2) < 0.2
