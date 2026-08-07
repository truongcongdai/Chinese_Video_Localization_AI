from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Optional

import requests

from universal_video_ai.segment import TranscriptSegment
from universal_video_ai.translate.speech_fit import SpeechFitConfig, speech_fit_report

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AdaptationConfig:
    enabled: bool = False
    provider: str = "openai"
    api_key: Optional[str] = None
    model: str = "gpt-5.6-luna"
    base_url: Optional[str] = None
    mode: str = "faithful"
    tone: str = "natural"
    audience: Optional[str] = None
    glossary: Optional[str] = None
    speech_fit: SpeechFitConfig = SpeechFitConfig()
    fallback_on_error: bool = True
    request_timeout_seconds: int = 90
    ollama_num_ctx: int = 8192
    ollama_num_predict: int = 0


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
        if not source_segments or not translated_segments:
            return translated_segments
        try:
            provider = (self.config.provider or "").strip().lower()
            if provider == "openai" and self.config.api_key:
                adapted_texts = self._adapt_with_openai(source_segments, translated_segments, source_lang, target_lang)
            elif provider == "gemini" and self.config.api_key:
                adapted_texts = self._adapt_with_gemini(source_segments, translated_segments, source_lang, target_lang)
            elif provider == "ollama":
                adapted_texts = self._adapt_with_ollama(source_segments, translated_segments, source_lang, target_lang)
            else:
                return translated_segments
        except Exception as exc:
            if not self.config.fallback_on_error:
                raise RuntimeError(f"Contextual translation adaptation failed: {exc}") from exc
            self.logger.warning("Contextual translation adaptation failed; keeping base translation: %s", exc)
            return translated_segments
        if len(adapted_texts) != len(translated_segments):
            if not self.config.fallback_on_error:
                raise RuntimeError(
                    f"Contextual adapter returned {len(adapted_texts)} segments for {len(translated_segments)} inputs"
                )
            self.logger.warning(
                "Contextual adapter returned %d segments for %d inputs; keeping base translation",
                len(adapted_texts), len(translated_segments),
            )
            return translated_segments
        quality_issue = _adaptation_quality_issue(adapted_texts, source_segments)
        if quality_issue:
            if not self.config.fallback_on_error:
                raise RuntimeError(f"Contextual adapter output failed quality gate: {quality_issue}")
            self.logger.warning("Contextual adapter output failed quality gate; keeping base translation: %s", quality_issue)
            return translated_segments
        unchanged_issue = _unchanged_output_issue(adapted_texts, translated_segments)
        if unchanged_issue:
            if not self.config.fallback_on_error:
                raise RuntimeError(f"Contextual adapter output failed quality gate: {unchanged_issue}")
            self.logger.warning("Contextual adapter output barely changed draft; keeping base translation: %s", unchanged_issue)
            return translated_segments
        return [
            TranscriptSegment(start=segment.start, end=segment.end, text=text.strip() or segment.text)
            for segment, text in zip(translated_segments, adapted_texts)
        ]

    def _build_prompt(
        self,
        source_segments: list[TranscriptSegment],
        translated_segments: list[TranscriptSegment],
        source_lang: str,
        target_lang: str,
    ) -> dict:
        rows = []
        for index, (src, dst) in enumerate(zip(source_segments, translated_segments)):
            report = speech_fit_report(
                TranscriptSegment(start=src.start, end=src.end, text=dst.text),
                self.config.speech_fit,
            )
            rows.append(
                {
                    "i": index,
                    "start": round(src.start, 3),
                    "end": round(src.end, 3),
                    "duration_seconds": round(src.duration, 3),
                    "target_speech_seconds": round(report.target_speech_seconds, 3),
                    "max_chars": report.max_chars,
                    "current_cps": round(report.cps, 2),
                    "source": src.text,
                    "draft_translation": dst.text,
                }
            )
        return {
            "task": (
                "Translate the full source-language subtitle script into natural target-language subtitles "
                "with global context, then return one target-language subtitle for each original segment."
            ),
            "critical_instruction": (
                "The draft_translation field is an unreliable machine-translation hint. Translate from source first. "
                "If draft_translation is awkward, literal, contradictory, hallucinated, or nonsense, ignore it."
            ),
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
                "Translate from the source text, not from pinyin and not from the draft when the draft sounds wrong.",
                "Use the entire source script context to resolve pronouns, subjects, speaker intent, terminology, and tone.",
                "Preserve subject/object relationships and Vietnamese pronoun/register choices consistently across segments.",
                "For Vietnamese short-drama localization, choose natural spoken pronouns from context: con/mẹ/bố, anh/em, anh cả, chú/cô, ông/bà, tôi/anh/cô only when context fits.",
                "Translate Chinese kinship, rank, family nicknames, and address terms naturally; do not translate them word-for-word when Vietnamese would sound unnatural.",
                "Never output broken machine Vietnamese, random Sino-Vietnamese names, sexual meanings, or literal fragments unless the source explicitly says that.",
                "Translate the meaning faithfully. Do not summarize, omit facts, soften claims, or add new information.",
                "Respect glossary terms and names.",
                "Make the target-language wording natural for voiceover and subtitles.",
                "Each output should fit its own duration_seconds and target_speech_seconds when possible.",
                "Aim for max_chars or fewer only by using concise equivalent wording; never drop meaning just to become shorter.",
                "Do not pad short segments unless the draft is incomplete.",
                "Prefer natural spoken language over literal word-for-word translation.",
            ],
            "segments": rows,
            "output_schema": {"segments": [{"i": 0, "text": "adapted target-language text"}]},
        }

    def _adapt_with_openai(
        self,
        source_segments: list[TranscriptSegment],
        translated_segments: list[TranscriptSegment],
        source_lang: str,
        target_lang: str,
    ) -> list[str]:
        prompt = self._build_prompt(source_segments, translated_segments, source_lang, target_lang)
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

    def _adapt_with_gemini(
        self,
        source_segments: list[TranscriptSegment],
        translated_segments: list[TranscriptSegment],
        source_lang: str,
        target_lang: str,
    ) -> list[str]:
        prompt = self._build_prompt(source_segments, translated_segments, source_lang, target_lang)
        model = self.config.model or "gemini-3.1-flash-lite"
        timeout_seconds = max(10, int(self.config.request_timeout_seconds or 90))
        request_payload = {
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {
                            "text": (
                                "Bạn là biên tập viên localization phim ngắn Trung Quốc sang tiếng Việt. "
                                "Dịch lại từ thoại gốc, sửa xưng hô và ngữ cảnh. "
                                "Không được chép lại draft_translation nếu draft còn máy móc. "
                                "Chỉ trả về JSON hợp lệ theo output_schema.\n\n"
                                + json.dumps(prompt, ensure_ascii=False)
                            ),
                        }
                    ],
                }
            ],
            "generationConfig": {
                "temperature": 0.1,
                "maxOutputTokens": max(1024, min(8192, len(translated_segments) * 96)),
                "responseMimeType": "application/json",
            },
        }
        response = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
            headers={"x-goog-api-key": self.config.api_key, "Content-Type": "application/json"},
            json=request_payload,
            timeout=timeout_seconds,
        )
        if response.status_code == 400 and "responseMimeType" in response.text:
            request_payload["generationConfig"].pop("responseMimeType", None)
            response = requests.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                headers={"x-goog-api-key": self.config.api_key, "Content-Type": "application/json"},
                json=request_payload,
                timeout=timeout_seconds,
            )
        if response.status_code >= 400:
            raise RuntimeError(f"Gemini adaptation failed: {response.status_code} {response.text[:300]}")
        data = response.json()
        parts = ((data.get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
        content = "".join(str(part.get("text") or "") for part in parts).strip()
        parsed = _parse_json_object(content)
        items = parsed.get("segments") or []
        by_index = {int(item.get("i")): str(item.get("text") or "") for item in items}
        return [by_index.get(index, translated_segments[index].text) for index in range(len(translated_segments))]

    def _adapt_with_ollama(
        self,
        source_segments: list[TranscriptSegment],
        translated_segments: list[TranscriptSegment],
        source_lang: str,
        target_lang: str,
    ) -> list[str]:
        prompt = self._build_prompt(source_segments, translated_segments, source_lang, target_lang)
        base_url = (self.config.base_url or "http://127.0.0.1:11434").rstrip("/")
        model = self.config.model or "qwen3:8b"
        timeout_seconds = max(10, int(self.config.request_timeout_seconds or 90))
        num_ctx = max(2048, int(self.config.ollama_num_ctx or 8192))
        num_predict = int(self.config.ollama_num_predict or 0)
        if num_predict <= 0:
            num_predict = max(1024, min(8192, len(translated_segments) * 96))
        try:
            response = requests.post(
                f"{base_url}/api/chat",
                json={
                    "model": model,
                    "stream": False,
                    "format": "json",
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "Bạn là biên tập viên localization phim ngắn Trung Quốc sang tiếng Việt. "
                                "Dịch trực tiếp từ thoại gốc, chọn xưng hô tự nhiên theo ngữ cảnh, "
                                "không bám máy móc vào bản dịch nháp. Chỉ trả JSON hợp lệ."
                            ),
                        },
                        {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
                    ],
                    "options": {
                        "temperature": 0.1,
                        "repeat_penalty": 1.12,
                        "num_ctx": num_ctx,
                        "num_predict": num_predict,
                    },
                },
                timeout=timeout_seconds,
            )
        except requests.exceptions.ConnectionError as exc:
            raise RuntimeError(
                f"Ollama local chưa chạy tại {base_url}. Mở terminal chạy: ollama serve"
            ) from exc
        except requests.exceptions.Timeout as exc:
            raise RuntimeError(
                f"Ollama local quá chậm hoặc không phản hồi tại {base_url} sau {timeout_seconds}s"
            ) from exc
        if response.status_code >= 400:
            if response.status_code == 404:
                raise RuntimeError(f"Ollama model chưa có: {model}. Chạy: ollama pull {model}")
            raise RuntimeError(f"Ollama adaptation failed: {response.status_code} {response.text[:300]}")
        data = response.json()
        content = ((data.get("message") or {}).get("content") or data.get("response") or "").strip()
        parsed = _parse_json_object(content)
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


def _parse_json_object(content: str) -> dict:
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass
    # Some local models still wrap JSON in prose or <think> blocks even when
    # Ollama's JSON mode is requested. Extract the outermost object as a last
    # resort, then let json.loads validate it.
    start = content.find("{")
    end = content.rfind("}")
    if start >= 0 and end > start:
        return json.loads(content[start:end + 1])
    raise RuntimeError("Model response did not include a JSON object")


def _adaptation_quality_issue(
    texts: list[str],
    source_segments: Optional[list[TranscriptSegment]] = None,
) -> Optional[str]:
    cleaned = [str(text or "").strip() for text in texts]
    if any(not text for text in cleaned):
        return "empty adapted segment"
    source_cleaned = [
        _normalize_for_comparison(segment.text)
        for segment in (source_segments or [])
    ]

    consecutive = 1
    previous = ""
    for index, text in enumerate(cleaned):
        normalized = " ".join(text.lower().split())
        if normalized and normalized == previous:
            consecutive += 1
            if consecutive >= 4:
                start = index - consecutive + 1
                if _source_window_justifies_repeat(source_cleaned[start:index + 1]):
                    continue
                return f"same subtitle repeated {consecutive} times: {text[:60]}"
        else:
            consecutive = 1
            previous = normalized

    counts: dict[str, int] = {}
    examples: dict[str, str] = {}
    adapted_sources: dict[str, set[str]] = {}
    for index, text in enumerate(cleaned):
        normalized = " ".join(text.lower().split())
        if len(normalized) >= 12:
            counts[normalized] = counts.get(normalized, 0) + 1
            examples.setdefault(normalized, text)
            if index < len(source_cleaned):
                adapted_sources.setdefault(normalized, set()).add(source_cleaned[index])
    repeated = [
        text for text, count in counts.items()
        if count >= 5 and len(adapted_sources.get(text, set())) > 1
    ]
    if repeated:
        return f"subtitle repeated too often: {examples.get(repeated[0], repeated[0])[:60]}"
    return None


def _unchanged_output_issue(texts: list[str], draft_segments: list[TranscriptSegment]) -> Optional[str]:
    if not texts or not draft_segments:
        return None
    comparable = [
        (_normalize_for_comparison(text), _normalize_for_comparison(segment.text))
        for text, segment in zip(texts, draft_segments)
        if str(segment.text or "").strip()
    ]
    if len(comparable) < 3:
        return None
    unchanged = sum(1 for adapted, draft in comparable if adapted == draft)
    ratio = unchanged / len(comparable)
    if ratio >= 0.9:
        return f"adapter returned {unchanged}/{len(comparable)} subtitles unchanged from draft"
    return None


def _normalize_for_comparison(text: str) -> str:
    return " ".join(str(text or "").strip().lower().split())


def _source_window_justifies_repeat(source_window: list[str]) -> bool:
    meaningful = [text for text in source_window if text]
    if not meaningful:
        return False
    return len(set(meaningful)) <= max(1, len(meaningful) // 3)
