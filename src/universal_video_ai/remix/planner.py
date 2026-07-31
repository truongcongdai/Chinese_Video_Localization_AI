from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Dict, Iterable, List


PLATFORM_PROFILES: Dict[str, Dict[str, Any]] = {
    "tiktok": {
        "label": "TikTok",
        "format": "short",
        "aspect_ratio": "9:16",
        "target_seconds": (20, 60),
        "safe_zone": "center_bottom_caption",
    },
    "reels": {
        "label": "Instagram Reels",
        "format": "short",
        "aspect_ratio": "9:16",
        "target_seconds": (20, 90),
        "safe_zone": "center_bottom_caption",
    },
    "youtube_shorts": {
        "label": "YouTube Shorts",
        "format": "short",
        "aspect_ratio": "9:16",
        "target_seconds": (20, 60),
        "safe_zone": "center_bottom_caption",
    },
    "facebook_reels": {
        "label": "Facebook Reels",
        "format": "short",
        "aspect_ratio": "9:16",
        "target_seconds": (20, 90),
        "safe_zone": "center_bottom_caption",
    },
    "youtube_long": {
        "label": "YouTube video dài",
        "format": "long",
        "aspect_ratio": "16:9",
        "target_seconds": (360, 1800),
        "safe_zone": "standard_16_9",
    },
    "facebook_long": {
        "label": "Facebook video dài",
        "format": "long",
        "aspect_ratio": "16:9",
        "target_seconds": (180, 1200),
        "safe_zone": "standard_16_9",
    },
}

DEFAULT_PLATFORMS = ("tiktok", "reels", "youtube_shorts")
VALID_STRENGTHS = {"light", "balanced", "strong"}
VALID_GOALS = {"viral", "education", "sales", "review", "story", "news"}


@dataclass(frozen=True)
class RemixPlan:
    enabled: bool
    platforms: List[str]
    goal: str
    strength: str
    primary_format: str
    processing_mode: str
    translation_mode: str
    review_before_render: bool
    animated_subtitles: bool
    template: str
    aspect_ratios: List[str]
    pipeline_steps: List[str]
    qa_checks: List[str]
    free_mode: Dict[str, Any]
    publish_assets: List[str]
    warnings: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _clean_platforms(platforms: Iterable[str] | None) -> List[str]:
    cleaned: List[str] = []
    for platform in platforms or DEFAULT_PLATFORMS:
        key = str(platform or "").strip().lower()
        if key in PLATFORM_PROFILES and key not in cleaned:
            cleaned.append(key)
    return cleaned or list(DEFAULT_PLATFORMS)


def build_remix_plan(
    *,
    enabled: bool = True,
    platforms: Iterable[str] | None = None,
    goal: str = "viral",
    strength: str = "balanced",
    free_mode: bool = True,
) -> RemixPlan:
    selected = _clean_platforms(platforms)
    normalized_goal = goal if goal in VALID_GOALS else "viral"
    normalized_strength = strength if strength in VALID_STRENGTHS else "balanced"
    formats = {PLATFORM_PROFILES[p]["format"] for p in selected}
    primary_format = "mixed" if len(formats) > 1 else next(iter(formats))

    long_form = "long" in formats
    strong = normalized_strength == "strong"
    translation_mode = "contextual" if enabled and normalized_strength != "light" else "faithful"
    processing_mode = "pro" if long_form or strong else "quality"
    template = "professional" if long_form else "social"

    pipeline_steps = [
        "download_clean_source",
        "transcribe_with_timestamps",
        "scene_detect_and_segment",
        "rewrite_script_for_goal",
        "storyboard_new_timeline",
        "generate_or_select_broll",
        "synthesize_voice",
        "mix_music_with_ducking",
        "render_subtitles_effects_cta",
        "quality_review_before_publish",
    ]
    if long_form:
        pipeline_steps.extend(["generate_chapters", "generate_title_description_thumbnail"])
    if len(selected) > 1:
        pipeline_steps.append("export_platform_variants")

    qa_checks = [
        "scene_voice_match",
        "subtitle_timing",
        "voice_naturalness",
        "music_ducking",
        "source_similarity_guard",
        "safe_zone_text_check",
        "black_frame_or_silence_check",
    ]
    if long_form:
        qa_checks.extend(["retention_gap_check", "chapter_consistency"])

    publish_assets = ["caption", "hashtags", "cta"]
    if long_form:
        publish_assets.extend(["title", "description", "chapters", "thumbnail"])

    warnings: List[str] = []
    if free_mode and translation_mode == "contextual":
        warnings.append(
            "Free remix can use local Ollama/contextual rewrite when available; otherwise it falls back to basic translation."
        )
    if long_form and free_mode:
        warnings.append("Long-form render is allowed in free mode but should be limited by credits/duration to control compute cost.")

    return RemixPlan(
        enabled=enabled,
        platforms=selected,
        goal=normalized_goal,
        strength=normalized_strength,
        primary_format=primary_format,
        processing_mode=processing_mode,
        translation_mode=translation_mode,
        review_before_render=enabled,
        animated_subtitles=not long_form or primary_format == "mixed",
        template=template,
        aspect_ratios=sorted({PLATFORM_PROFILES[p]["aspect_ratio"] for p in selected}),
        pipeline_steps=pipeline_steps,
        qa_checks=qa_checks,
        free_mode={
            "no_system_watermark": True,
            "user_brand_watermark_optional": True,
            "default_tts_provider": "edge",
            "preview_allowed": True,
        },
        publish_assets=publish_assets,
        warnings=warnings,
    )
