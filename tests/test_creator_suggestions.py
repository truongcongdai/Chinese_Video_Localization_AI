from universal_video_ai.web.app import (
    CreatorSuggestionBody,
    _creator_ai_prompt,
    _creator_keywords_from_topic,
    _creator_narration_from_topic,
    _creator_narration_text_from_topic,
    _creator_scene_brief_from_topic,
    _creator_script_text_from_topic,
    _enforce_creator_entity_consistency,
    _split_creator_script,
    _validate_creator_suggestion_timing,
)


TOPIC = "5 đặc điểm ít người biết về con lửng mật"


def test_animal_topic_keeps_compound_subject_in_all_local_suggestions():
    keywords = _creator_keywords_from_topic(TOPIC, "vi")
    visuals = _creator_scene_brief_from_topic(TOPIC, "vi")
    narration = _creator_narration_from_topic(TOPIC, "vi")

    assert keywords[0] == "động vật lửng mật"
    assert all("động vật lửng mật" in keyword for keyword in keywords)
    assert all("cây lửng mật" not in item.lower() for item in keywords + visuals + narration)
    assert not any("một người" in item.lower() for item in visuals)


def test_ai_entity_guard_corrects_tree_animal_contradiction():
    result = {
        "keywords": ["cây lửng mật", "đặc điểm cây lửng mật"],
        "visual_brief": "Cận cảnh cây lửng mật trong rừng",
        "narration_script": "Đây là cây lửng mật.",
    }

    fixed = _enforce_creator_entity_consistency(result, TOPIC, "vi")

    assert fixed["keywords"] == ["động vật lửng mật", "đặc điểm động vật lửng mật"]
    assert "cây lửng mật" not in fixed["visual_brief"].lower()
    assert "cây lửng mật" not in fixed["narration_script"].lower()


def test_long_form_prompt_uses_full_duration_word_budget_and_scene_count():
    body = CreatorSuggestionBody(
        topic=TOPIC, target_language="vi", aspect_ratio="16:9",
        duration_seconds=1200, transition="fade",
    )
    prompt, scene_count = _creator_ai_prompt(body)

    assert scene_count == 150
    assert "2820 từ" in prompt
    assert "2679-2961 từ" in prompt
    assert "1200 giây" in prompt


def test_creator_script_is_not_truncated_to_twelve_scenes():
    lines = [f"Cảnh {index}" for index in range(1, 31)]
    parsed = _split_creator_script(TOPIC, "\n".join(lines), "vi")

    assert parsed == lines


def test_local_suggestions_change_with_selected_duration():
    scripts = [_creator_script_text_from_topic(TOPIC, "vi", seconds) for seconds in (30, 45, 60)]
    narrations = [_creator_narration_text_from_topic(TOPIC, "vi", seconds) for seconds in (30, 45, 60)]

    assert [len(value.splitlines()) for value in scripts] == [6, 9, 12]
    word_counts = [len(value.split()) for value in narrations]
    assert word_counts[0] < word_counts[1] < word_counts[2]
    assert word_counts[0] >= 30 * 2.35 * 0.90
    assert word_counts[2] >= 60 * 2.35 * 0.90


def test_keywords_do_not_change_with_duration():
    assert _creator_keywords_from_topic(TOPIC, "vi", 30) == _creator_keywords_from_topic(
        TOPIC, "vi", 60,
    )


def test_ai_suggestion_must_match_selected_duration():
    result = {
        "visual_brief": "\n".join(f"Cảnh {index}" for index in range(6)),
        "narration_script": " ".join(["từ"] * 71),
        "language": "vi",
    }
    assert _validate_creator_suggestion_timing(result, 30) is result

    try:
        _validate_creator_suggestion_timing(result, 60)
    except RuntimeError as exc:
        assert "expected 12 scenes" in str(exc)
    else:
        raise AssertionError("A 30-second response must not be accepted for 60 seconds")
