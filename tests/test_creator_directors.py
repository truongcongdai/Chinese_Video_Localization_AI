from __future__ import annotations

from universal_video_ai.render.visual_director import direct_visual_scene
from universal_video_ai.render.voice_director import direct_voice_cue


def test_voice_director_emphasizes_first_hook() -> None:
    cue = direct_voice_cue("You are losing time on the wrong step", 0, 4, "en")

    assert cue.text.endswith(".")
    assert cue.rate == "+2%"
    assert cue.pitch == "+4Hz"


def test_voice_director_uses_question_lift() -> None:
    cue = direct_voice_cue("Do you know why this workflow fails?", 2, 5, "en")

    assert cue.rate == "+7%"
    assert cue.pitch == "+3Hz"


def test_voice_director_restores_common_vietnamese_diacritics() -> None:
    cue = direct_voice_cue(
        "Neu ban dang muon lam python automation, dung bat dau truoc khi biet diem nay",
        0,
        3,
        "vi",
    )

    assert cue.rate == "+2%"
    assert "Nếu bạn đang muốn làm" in cue.text
    assert "đừng bắt đầu trước khi biết điểm này" in cue.text


def test_visual_director_prefers_screen_workflow_for_software_scene() -> None:
    match = direct_visual_scene(
        "AI automation",
        "A creator reviews an automation dashboard on a laptop",
        "Compare setup time and reliability before choosing a tool",
    )

    assert match.shot_type == "Over-the-shoulder screen/workflow shot"
    assert any("laptop" in query for query in match.stock_queries)
    assert "Match the narration meaning" in match.ai_prompt
