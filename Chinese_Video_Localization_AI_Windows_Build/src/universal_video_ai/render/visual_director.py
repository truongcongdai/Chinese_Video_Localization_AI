"""Visual matching helpers for creator scenes."""

from __future__ import annotations

from dataclasses import dataclass
import re

__all__ = ["VisualMatch", "direct_visual_scene"]

_STOPWORDS = {
    "a", "an", "the", "there", "is", "are", "was", "were", "this", "that",
    "with", "and", "or", "of", "to", "from", "in", "on", "at", "for", "into",
    "scene", "shot", "footage", "video", "showing", "shows", "appears", "then",
    "next", "about", "related", "directly", "how", "why", "what", "when",
}


@dataclass(frozen=True)
class VisualMatch:
    """Directed visual instructions for one scene."""

    shot_type: str
    stock_queries: tuple[str, ...]
    ai_prompt: str


def direct_visual_scene(topic: str, visual: str, narration: str) -> VisualMatch:
    """Build scene-specific stock queries and an AI image/video prompt."""
    shot_type = _infer_shot_type(visual, narration)
    must_include = _compact_terms(f"{narration} {visual}", 9)
    visual_terms = _compact_terms(visual, 7)
    narration_terms = _compact_terms(narration, 7)
    topic_terms = _compact_terms(topic, 5)
    queries = [
        _query_for_shot(shot_type, narration_terms or visual_terms),
        _query_for_shot(shot_type, visual_terms or narration_terms),
        _compact_terms(f"{narration_terms} {visual_terms}", 8),
        topic_terms,
    ]
    queries = tuple(dict.fromkeys(query for query in queries if query))
    prompt = (
        f"{shot_type}. Show exactly: {visual}. Match the narration meaning: {narration}. "
        f"Must include: {must_include or topic_terms}. Live-action, realistic lighting, natural motion, "
        "anatomically correct people, natural hands only when necessary, face and hands aligned with the body, "
        "avoid extreme close-ups of fingers or faces unless essential, no text overlays, no logos, "
        "no UI labels unless the scene explicitly asks for a screen."
    )
    return VisualMatch(shot_type=shot_type, stock_queries=queries, ai_prompt=prompt)


def _infer_shot_type(visual: str, narration: str) -> str:
    text = f"{visual} {narration}".lower()
    if any(token in text for token in ("screen", "dashboard", "software", "app", "website", "laptop", "computer")):
        return "Over-the-shoulder screen/workflow shot"
    if any(token in text for token in ("close", "detail", "texture", "hand", "product")):
        return "Close-up detail shot"
    if any(token in text for token in ("before", "after", "result", "output", "finish", "success")):
        return "Before-and-after result shot"
    if any(token in text for token in ("problem", "mistake", "waste", "stress", "confused", "khó", "lỗi")):
        return "Problem-focused human reaction shot"
    if any(token in text for token in ("step", "process", "workflow", "prepare", "build")):
        return "Step-by-step process shot"
    return "Concrete documentary b-roll shot"


def _query_for_shot(shot_type: str, terms: str) -> str:
    if not terms:
        return ""
    prefix = {
        "Over-the-shoulder screen/workflow shot": "person using laptop",
        "Close-up detail shot": "close up",
        "Before-and-after result shot": "successful result",
        "Problem-focused human reaction shot": "frustrated person",
        "Step-by-step process shot": "hands working",
    }.get(shot_type, "")
    return _compact_terms(f"{prefix} {terms}", 8)


def _compact_terms(text: str, limit: int) -> str:
    words = re.findall(r"[a-z0-9]+", text.lower())
    result: list[str] = []
    for word in words:
        if word in _STOPWORDS or len(word) < 2 or word in result:
            continue
        result.append(word)
        if len(result) >= limit:
            break
    return " ".join(result)
