from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Optional

import requests

from universal_video_ai.segment import TranscriptSegment

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AdaptationConfig:
    enabled: bool = False
    provider: str = "openai"
    api_key: Optional[str] = None
    model: str = "gpt-5.6-luna"
    mode: str = "faithful"
    tone: str = "natural"
    audience: Optional[str] = None
    glossary: Optional[str] = None


class SegmentAdapter:
    def __init__(self, config: AdaptationConfig, logger: Optional[logging.Logger] = None) -> None:
        self.config = config
        self.logger = logger or _logger

    async def adapt_segments(
        self,
        source_segments: list[TranscriptSegment],
        translated_segments: list[TranscriptSegment],
        source_lang: str,
        target_lang: str,
    ) -> list[TranscriptSegment]:
        if not self.config.enabled:
            return translated_segments
        if self.config.provider != "openai" or not self.config.api_key:
            return translated_segments
        if not source_segments or not translated_segments:
            return translated_segments
        try:
            adapted_texts = self._adapt_with_openai(source_segments, translated_segments, source_lang, target_lang)
        except Exception as exc:
            self.logger.warning("Contextual translation adaptation failed; keeping base translation: %s", exc)
            return translated_segments
        if len(adapted_texts) != len(translated_segments):
            self.logger.warning(
                "Contextual adapter returned %d segments for %d inputs; keeping base translation",
                len(adapted_texts), len(translated_segments),
            )
            return translated_segments
        return [
            TranscriptSegment(start=segment.start, end=segment.end, text=text.strip() or segment.text)
            for segment, text in zip(translated_segments, adapted_texts)
        ]

    def _adapt_with_openai(
        self,
        source_segments: list[TranscriptSegment],
        translated_segments: list[TranscriptSegment],
        source_lang: str,
        target_lang: str,
    ) -> list[str]:
        rows = [
            {
                "i": index,
                "start": round(src.start, 3),
                "end": round(src.end, 3),
                "source": src.text,
                "draft_translation": dst.text,
            }
            for index, (src, dst) in enumerate(zip(source_segments, translated_segments))
        ]
        prompt = {
            "task": "Rewrite/adapt translated video transcript segments while preserving segment count and meaning.",
            "source_language": source_lang,
            "target_language": target_lang,
            "mode": self.config.mode,
            "tone": self.config.tone,
            "audience": self.config.audience or "",
            "glossary": self.config.glossary or "",
            "rules": [
                "Return only valid JSON.",
                "Keep exactly one output item for every input item.",
                "Do not merge, split, reorder, add, or remove segments.",
                "Respect glossary terms and names.",
                "Make the target-language wording natural for voiceover and subtitles.",
                "Keep each segment concise enough to fit its timestamp.",
            ],
            "segments": rows,
            "output_schema": {"segments": [{"i": 0, "text": "adapted target-language text"}]},
        }
        response = requests.post(
            "https://api.openai.com/v1/responses",
            headers={"Authorization": f"Bearer {self.config.api_key}", "Content-Type": "application/json"},
            json={
                "model": self.config.model or "gpt-5.6-luna",
                "input": [
                    {
                        "role": "system",
                        "content": "You are a professional video localization editor. Output strict JSON only.",
                    },
                    {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
                ],
                "text": {"format": {"type": "json_object"}},
            },
            timeout=180,
        )
        if response.status_code >= 400:
            raise RuntimeError(f"OpenAI adaptation failed: {response.status_code} {response.text[:300]}")
        data = response.json()
        content = _extract_response_text(data)
        parsed = json.loads(content)
        items = parsed.get("segments") or []
        by_index = {int(item.get("i")): str(item.get("text") or "") for item in items}
        return [by_index.get(index, translated_segments[index].text) for index in range(len(translated_segments))]


def _extract_response_text(data: dict) -> str:
    if isinstance(data.get("output_text"), str):
        return data["output_text"]
    parts: list[str] = []
    for item in data.get("output", []) or []:
        for content in item.get("content", []) or []:
            text = content.get("text")
            if isinstance(text, str):
                parts.append(text)
    if parts:
        return "\n".join(parts)
    raise RuntimeError("OpenAI response did not include text output")
