from universal_video_ai.web.app import (
    AffiliateReviewBody,
    CreatorSuggestionBody,
    _affiliate_product_ad_creative,
    _creator_ai_prompt,
    _harden_creator_image_prompt,
    _creator_keywords_from_topic,
    _creator_narration_from_topic,
    _creator_narration_text_from_topic,
    _creator_seo_keywords_from_topic,
    _creator_scene_brief_from_topic,
    _creator_script_text_from_topic,
    _creator_stock_queries,
    _enforce_creator_entity_consistency,
    _postprocess_creator_suggestion_quality,
    _product_media_animation_for_scene,
    _scene_requires_model_context,
    _scene_prefers_product_media,
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


def test_seo_keywords_add_search_intent_without_losing_subject():
    keywords = _creator_seo_keywords_from_topic("python automation for creators", "vi", 30)

    joined = " | ".join(keywords).lower()
    assert keywords[0] == "python automation for creators"
    assert "cho nguoi moi" in joined
    assert "sai lam" in joined
    assert "review" in joined


def test_creator_quality_postprocess_replaces_weak_hook_and_visuals():
    result = {
        "keywords": ["python automation"],
        "visual_brief": "intro\nb-roll\nscene",
        "script": "intro\nb-roll\nscene",
        "narration_script": "Trong video nay, chung ta se tim hieu python automation.\nNo giup creator tiet kiem thoi gian.",
    }

    upgraded = _postprocess_creator_suggestion_quality(
        result, "python automation for creators", "vi", 30,
    )

    assert upgraded["narration_script"].splitlines()[0].startswith("Nếu bạn")
    assert len(upgraded["narration_script"].split()) >= round(30 * 2.35 * 0.92)
    assert len(upgraded["visual_brief"].splitlines()) == 6
    assert "python automation for creators" in upgraded["visual_brief"].lower()
    assert upgraded["quality_notes"]


def test_creator_ai_prompt_requires_retention_hook_and_seo_intent():
    body = CreatorSuggestionBody(
        topic="python automation for creators",
        target_language="vi",
        aspect_ratio="9:16",
        duration_seconds=30,
        transition="fade",
    )

    prompt, _ = _creator_ai_prompt(body)

    assert "RETENTION + SEO QUALITY BAR" in prompt
    assert "first narration line must be a sharp viewer-facing hook" in prompt
    assert "full Vietnamese diacritics" in prompt
    assert "mistakes/pain points" in prompt


def test_affiliate_review_has_purchase_hook_and_proof_broll():
    body = AffiliateReviewBody(
        product_name="may hut bui mini cam tay",
        real_experience="toi da dung tren ban lam viec va thay hut bui nho kha nhanh",
        audience="nguoi lam viec trong phong nho",
        model_prompt="nu creator trong phong lam viec nho dang don ban",
        duration_seconds=30,
        platform="tiktok_shop",
    )

    result = _affiliate_product_ad_creative(body)

    assert result["generator"] == "product_ad_template"
    assert result["narration_script"].splitlines()[0].startswith("Nếu bạn đang định mua")
    assert "có đáng mua không" in result["title"].lower()
    assert "HERO PRODUCT MEDIA" in result["broll_plan"]
    assert "MODEL USE SCENE" in result["broll_plan"]
    assert "nu creator trong phong lam viec nho" in result["broll_plan"]
    assert "hoa hồng" not in result["narration_script"].lower()
    assert "codangmuakhong" in result["hashtags"]


def test_affiliate_product_ad_supports_demo_proof_format():
    body = AffiliateReviewBody(
        product_name="den livestream mini",
        real_experience="toi da test khi quay ban dem va anh sang mat sang hon nhung khong bi choi",
        audience="creator quay video tai nha",
        duration_seconds=60,
        platform="shorts",
        creative_format="demo_proof",
    )

    result = _affiliate_product_ad_creative(body)

    assert result["creative_format"] == "demo_proof"
    assert "demo thật" in result["title"].lower()
    assert len(result["broll_plan"].splitlines()) == 10
    assert "Product media should be uploaded" in " ".join(result["quality_notes"])


def test_product_media_is_used_only_for_product_ad_beats():
    assert _scene_prefers_product_media(
        "9-13s | PRODUCT REVEAL: cận cảnh sản phẩm thật, bao bì, kích thước",
    )
    assert not _scene_prefers_product_media(
        "13-17s | MODEL USE SCENE + DEMO PROOF: người mẫu dùng sản phẩm",
    )
    assert _scene_requires_model_context(
        "13-17s | MODEL USE SCENE + DEMO PROOF: người mẫu dùng sản phẩm",
    )
    assert not _scene_prefers_product_media(
        "4-9s | PROBLEM SHOT: người thuộc nhóm creator gặp đúng vấn đề trước khi dùng sản phẩm",
    )
    assert _product_media_animation_for_scene("PRODUCT REVEAL", 0) == "zoomin"


def test_product_ad_stock_queries_are_action_specific():
    queries = _creator_stock_queries(
        "Product ad: handheld vacuum",
        "13-17s | MODEL USE SCENE + DEMO PROOF: female creator uses handheld vacuum on a desk",
        "The strongest reason to consider it is cleaning crumbs from a desk",
    )

    joined = " | ".join(queries)
    assert "handheld vacuum" in joined or "cleaning desk crumbs keyboard" in joined
    assert "creator product review" not in queries[0]


def test_image_prompt_hardening_preserves_scene_and_blocks_anatomy_errors():
    prompt = _harden_creator_image_prompt(
        "A creator opens a Python automation dashboard and compares the output with their content calendar",
    )

    assert "Python automation dashboard" in prompt
    assert "anatomically correct body proportions" in prompt
    assert "avoid close-up fingers" in prompt


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
