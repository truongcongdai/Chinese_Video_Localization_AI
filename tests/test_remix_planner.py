from universal_video_ai.remix import build_remix_plan


def test_short_form_remix_plan_defaults_to_free_no_system_watermark():
    plan = build_remix_plan(platforms=["tiktok", "reels"], goal="viral", strength="balanced")

    assert plan.primary_format == "short"
    assert plan.processing_mode == "quality"
    assert plan.translation_mode == "contextual"
    assert plan.review_before_render is True
    assert plan.free_mode["no_system_watermark"] is True
    assert plan.free_mode["user_brand_watermark_optional"] is True
    assert "source_similarity_guard" in plan.qa_checks


def test_mixed_long_and_short_remix_plan_adds_long_form_assets():
    plan = build_remix_plan(platforms=["youtube_long", "facebook_long", "youtube_shorts"])

    assert plan.primary_format == "mixed"
    assert plan.processing_mode == "pro"
    assert "16:9" in plan.aspect_ratios
    assert "9:16" in plan.aspect_ratios
    assert "generate_chapters" in plan.pipeline_steps
    assert "thumbnail" in plan.publish_assets
    assert "retention_gap_check" in plan.qa_checks
