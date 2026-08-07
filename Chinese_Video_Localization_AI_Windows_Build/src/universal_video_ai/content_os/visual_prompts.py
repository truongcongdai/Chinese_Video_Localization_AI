"""Utilities for Content OS script and visual prompt generation."""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List


_GENERIC_PREFIXES = (
    "show ",
    "display ",
    "visual ",
    "image of ",
    "scene ",
)


def repair_mojibake(text: str) -> str:
    """Repair common UTF-8-as-Latin-1 mojibake without changing valid text."""
    if not isinstance(text, str) or not text:
        return text or ""
    markers = ("Ã", "Â", "Ä", "áº", "á»", "Æ", "�")
    if not any(marker in text for marker in markers):
        return text
    candidates = [text]
    for source_encoding in ("latin1", "cp1252"):
        try:
            candidates.append(text.encode(source_encoding, errors="ignore").decode("utf-8", errors="ignore"))
        except Exception:
            pass
    return min(candidates, key=lambda value: sum(value.count(marker) for marker in markers))


def clean_text(text: str) -> str:
    """Normalize whitespace and repair common encoding damage."""
    return re.sub(r"\s+", " ", repair_mojibake(text or "")).strip()


def is_generic_visual_instruction(text: str) -> bool:
    """Return True when a visual instruction is too generic for asset generation."""
    value = clean_text(text).lower()
    if not value:
        return True
    if len(value.split()) < 5:
        return True
    if value.startswith(_GENERIC_PREFIXES):
        return True
    generic_terms = ("intro", "highlights", "details", "summary", "what to show")
    return any(term in value for term in generic_terms) and len(value.split()) < 12


def infer_topic(context: Dict[str, Any] | None = None, *texts: str) -> str:
    """Infer the best available topic string from context and text fields."""
    context = context or {}
    candidates: List[str] = []
    for key in ("topic", "objective", "user_instructions"):
        value = context.get(key)
        if isinstance(value, str):
            candidates.append(value)
    content_plan = context.get("content_plan")
    if isinstance(content_plan, dict):
        for key in ("content_angle", "core_message", "hook"):
            value = content_plan.get(key)
            if isinstance(value, str):
                candidates.append(value)
    candidates.extend(text for text in texts if isinstance(text, str))
    for candidate in candidates:
        candidate = clean_text(candidate)
        if candidate:
            return candidate
    return "nội dung chính"


def scene_visual_prompt(topic: str, narration: str = "", order: int = 1) -> str:
    """Create a concrete image-generation prompt for a vertical short scene."""
    topic = clean_text(topic) or "nội dung chính"
    narration = clean_text(narration)
    combined = f"{topic} {narration}".lower()

    if any(word in combined for word in ("tiếng anh", "english", "học ngoại ngữ", "language")):
        scene_focus = [
            "a learner practicing English speaking with an AI conversation app on a smartphone",
            "a smartphone camera scanning English text in a book with translation and pronunciation hints",
            "a vocabulary flashcard app using spaced repetition, headphones, notebook, and study desk",
            "a clean summary layout with three AI learning features around one modern phone",
        ][(order - 1) % 4]
    elif any(word in combined for word in ("ai", "artificial intelligence", "trí tuệ")):
        scene_focus = [
            "a modern smartphone showing AI assistant bubbles and productivity widgets",
            "a creator desk with phone, waveform, captions, and AI automation elements",
            "a futuristic but realistic mobile AI workflow with icons and soft light",
            "a concise comparison dashboard of AI features on a phone screen",
        ][(order - 1) % 4]
    else:
        scene_focus = [
            f"a concrete visual example of {topic}",
            f"a close-up lifestyle scene representing {topic}",
            f"a practical before-and-after demonstration related to {topic}",
            f"a clean visual summary of {topic}",
        ][(order - 1) % 4]

    return (
        f"Vertical 9:16 realistic short-video scene, {scene_focus}. "
        "Modern Vietnamese mobile content style, clear foreground subject, rich background details, "
        "cinematic lighting, no brand logos, no readable text, leave lower 28 percent clean for subtitles."
    )


def detailed_visual_prompt(
    topic: str,
    narration: str = "",
    visual_instruction: str = "",
    order: int = 1,
    total: int = 1,
) -> str:
    """Build a detailed image prompt that is anchored to the exact script segment."""
    topic = clean_text(topic)
    narration = clean_text(narration)
    visual_instruction = clean_text(visual_instruction)
    base = visual_instruction if visual_instruction else scene_visual_prompt(topic, narration, order)
    if not base.lower().startswith("vertical"):
        base = f"Vertical 9:16 realistic short-video scene, {base}"

    segment_role = {
        1: "hook shot that immediately communicates the problem and grabs attention",
        2: "first feature demonstration with clear phone interaction",
        3: "second feature demonstration with a distinct setting and action",
        4: "third feature demonstration with a different close-up composition",
    }.get(order, "final takeaway shot that summarizes the value")

    return (
        f"{base}. "
        f"Scene {order} of {total}: {segment_role}. "
        f"Script context: {narration[:420]}. "
        "Generate a finished vertical YouTube Shorts frame, not a poster template. "
        "Show concrete real-world objects and actions from the script: smartphone, learner, UI-like shapes, "
        "voice waveform, camera scan, grammar suggestion, study desk, or confident learner when relevant. "
        "Use varied composition from other scenes, high detail, cinematic lighting, realistic depth. "
        "No brand logos, no readable UI text, no captions inside the image, leave the bottom 28 percent clean for subtitles."
    )


def enrich_segments_with_visuals(segments: Iterable[Dict[str, Any]], topic: str) -> List[Dict[str, Any]]:
    """Ensure every segment has a usable visual instruction."""
    enriched: List[Dict[str, Any]] = []
    for index, segment in enumerate(segments, start=1):
        item = dict(segment)
        narration = item.get("narration") or item.get("subtitle_text") or ""
        visual = item.get("visual_instruction", "")
        if is_generic_visual_instruction(visual):
            item["visual_instruction"] = scene_visual_prompt(topic, narration, index)
        else:
            item["visual_instruction"] = clean_text(visual)
        for key in ("narration", "subtitle_text"):
            if isinstance(item.get(key), str):
                item[key] = clean_text(item[key])
        enriched.append(item)
    return enriched


def normalize_script_segments(segments: Iterable[Dict[str, Any]], topic: str = "") -> List[Dict[str, Any]]:
    """Normalize common LLM segment aliases into the Content OS schema."""
    normalized: List[Dict[str, Any]] = []
    raw_segments = list(segments or [])
    total = max(1, len(raw_segments))
    for index, segment in enumerate(raw_segments, start=1):
        if not isinstance(segment, dict):
            continue
        item = dict(segment)
        item["segment_id"] = clean_text(
            str(item.get("segment_id") or item.get("id") or item.get("scene_id") or f"seg{index}")
        )
        if "start_second" not in item:
            item["start_second"] = item.get("time_start", item.get("start", item.get("start_time", 0.0)))
        if "end_second" not in item:
            item["end_second"] = item.get("time_end", item.get("end", item.get("end_time", item.get("duration", 0.0))))
        try:
            item["start_second"] = float(item.get("start_second") or 0.0)
        except (TypeError, ValueError):
            item["start_second"] = 0.0
        try:
            item["end_second"] = float(item.get("end_second") or 0.0)
        except (TypeError, ValueError):
            item["end_second"] = item["start_second"] + 5.0
        if item["end_second"] <= item["start_second"]:
            item["end_second"] = item["start_second"] + 5.0
        item["narration"] = clean_text(
            item.get("narration") or item.get("voiceover") or item.get("text") or item.get("script") or ""
        )
        item["subtitle_text"] = clean_text(
            item.get("subtitle_text") or item.get("subtitle") or item.get("caption") or item["narration"]
        )
        if item["narration"] and len(item["subtitle_text"]) < min(42, len(item["narration"]) * 0.45):
            item["subtitle_text"] = _caption_from_narration(item["narration"])
        item["visual_instruction"] = clean_text(
            item.get("visual_instruction") or item.get("visual") or item.get("visual_prompt") or item.get("scene") or ""
        )
        refs = item.get("source_refs") or item.get("sources") or []
        item["source_refs"] = refs if isinstance(refs, list) else [str(refs)]
        item["visual_instruction"] = detailed_visual_prompt(
            topic or "short-form video",
            item["narration"] or item["subtitle_text"],
            item["visual_instruction"],
            index,
            total,
        )
        normalized.append(item)
    return enrich_segments_with_visuals(normalized, topic or "short-form video")


def _caption_from_narration(narration: str, max_words: int = 18) -> str:
    words = clean_text(narration).split()
    if len(words) <= max_words:
        return " ".join(words)
    return " ".join(words[:max_words]).rstrip(",.;:") + "…"


def normalize_generated_script(data: Dict[str, Any], topic: str = "") -> Dict[str, Any]:
    """Normalize common LLM script aliases into GeneratedScript-compatible data."""
    script = dict(data or {})
    script["title_options"] = script.get("title_options") or script.get("titles") or script.get("title") or []
    if isinstance(script["title_options"], str):
        script["title_options"] = [script["title_options"]]
    script["title_options"] = [clean_text(title) for title in script.get("title_options", [])]
    script["hook"] = clean_text(script.get("hook") or script.get("opening") or "")
    script["narration_text"] = clean_text(
        script.get("narration_text") or script.get("narration") or script.get("voiceover") or script.get("script") or ""
    )
    inferred_topic = infer_topic({"topic": topic}, script.get("hook", ""), script.get("narration_text", ""))
    script["segments"] = normalize_script_segments(script.get("segments") or script.get("scenes") or [], inferred_topic)
    if not script["narration_text"] and script["segments"]:
        script["narration_text"] = " ".join(segment.get("narration", "") for segment in script["segments"])
    script["description"] = clean_text(script.get("description") or "")
    hashtags = script.get("hashtags") or script.get("tags") or []
    if isinstance(hashtags, str):
        hashtags = [tag.strip() for tag in hashtags.replace("#", " #").split() if tag.strip()]
    script["hashtags"] = hashtags
    script["estimated_duration_seconds"] = float(script.get("estimated_duration_seconds") or script.get("duration_seconds") or 45.0)
    script["source_attributions"] = script.get("source_attributions") or script.get("sources") or []
    return script
