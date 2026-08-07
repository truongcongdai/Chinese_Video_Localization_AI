from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

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
    gemini_retry_count: int = 2
    gemini_batch_size: int = 24
    gemini_debug_dir: Optional[str] = None


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
        parsed = _parse_json_object(content)
        return _extract_segment_texts(parsed, len(translated_segments))

    def _adapt_with_gemini(
        self,
        source_segments: list[TranscriptSegment],
        translated_segments: list[TranscriptSegment],
        source_lang: str,
        target_lang: str,
    ) -> list[str]:
        """Adapt with Gemini, retrying malformed JSON and degrading to batches.

        A single large Gemini response can be truncated or contain one missing
        comma even when JSON MIME mode is requested. We first preserve full
        context by trying the whole script. Only after those attempts fail do
        we split into smaller batches. Failed batches fall back to their base
        translations when fallback_on_error is enabled, so an optional quality
        pass cannot destroy an otherwise usable localization job.
        """
        try:
            return self._request_gemini_with_retries(
                source_segments,
                translated_segments,
                source_lang,
                target_lang,
                label="full",
            )
        except Exception as full_error:
            if len(translated_segments) <= 1:
                raise
            self.logger.warning(
                "Gemini full-script adaptation failed; retrying in smaller batches: %s",
                full_error,
            )

        configured_size = max(2, int(self.config.gemini_batch_size or 24))
        # When the full request itself was already smaller than the configured
        # batch size, halve it so the fallback actually reduces response size.
        batch_size = min(configured_size, max(1, len(translated_segments) // 2))
        results: list[str] = []
        failed_batches = 0

        for start_index in range(0, len(translated_segments), batch_size):
            source_batch = source_segments[start_index:start_index + batch_size]
            translated_batch = translated_segments[start_index:start_index + batch_size]
            label = f"batch-{start_index}-{start_index + len(translated_batch) - 1}"
            try:
                batch_texts = self._request_gemini_with_retries(
                    source_batch,
                    translated_batch,
                    source_lang,
                    target_lang,
                    label=label,
                )
            except Exception as batch_error:
                failed_batches += 1
                if not self.config.fallback_on_error:
                    raise RuntimeError(
                        f"Gemini adaptation failed for {label}: {batch_error}"
                    ) from batch_error
                self.logger.warning(
                    "Gemini %s failed; keeping base translation for this batch: %s",
                    label,
                    batch_error,
                )
                batch_texts = [segment.text for segment in translated_batch]
            results.extend(batch_texts)

        if failed_batches:
            self.logger.warning(
                "Gemini adaptation used base translation for %d failed batch(es)",
                failed_batches,
            )
        return results

    def _request_gemini_with_retries(
        self,
        source_segments: list[TranscriptSegment],
        translated_segments: list[TranscriptSegment],
        source_lang: str,
        target_lang: str,
        *,
        label: str,
    ) -> list[str]:
        attempts = max(1, int(self.config.gemini_retry_count or 0) + 1)
        last_error: Optional[Exception] = None
        for attempt in range(1, attempts + 1):
            try:
                return self._request_gemini_once(
                    source_segments,
                    translated_segments,
                    source_lang,
                    target_lang,
                    label=label,
                    repair_attempt=attempt > 1,
                )
            except Exception as exc:
                last_error = exc
                if attempt >= attempts:
                    break
                self.logger.warning(
                    "Gemini adaptation %s attempt %d/%d failed: %s",
                    label,
                    attempt,
                    attempts,
                    exc,
                )
                time.sleep(min(1.5, 0.35 * attempt))
        assert last_error is not None
        raise last_error

    def _request_gemini_once(
        self,
        source_segments: list[TranscriptSegment],
        translated_segments: list[TranscriptSegment],
        source_lang: str,
        target_lang: str,
        *,
        label: str,
        repair_attempt: bool,
    ) -> list[str]:
        prompt = self._build_prompt(source_segments, translated_segments, source_lang, target_lang)
        if repair_attempt:
            prompt["retry_instruction"] = (
                "The previous response could not be parsed. Return one compact JSON object only. "
                "Do not use markdown fences, comments, prose, trailing commas, or omit commas."
            )
        model = self.config.model or "gemini-3.1-flash-lite"
        timeout_seconds = max(10, int(self.config.request_timeout_seconds or 90))
        generation_config: dict[str, Any] = {
            "temperature": 0.0 if repair_attempt else 0.1,
            "maxOutputTokens": max(1024, min(8192, len(translated_segments) * 112)),
            "responseMimeType": "application/json",
            "responseSchema": {
                "type": "OBJECT",
                "properties": {
                    "segments": {
                        "type": "ARRAY",
                        "items": {
                            "type": "OBJECT",
                            "properties": {
                                "i": {"type": "INTEGER"},
                                "text": {"type": "STRING"},
                            },
                            "required": ["i", "text"],
                        },
                    }
                },
                "required": ["segments"],
            },
        }
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
                                "Chỉ trả về một JSON object hợp lệ theo output_schema, không markdown.\n\n"
                                + json.dumps(prompt, ensure_ascii=False)
                            ),
                        }
                    ],
                }
            ],
            "generationConfig": generation_config,
        }
        response = self._post_gemini_request(model, request_payload, timeout_seconds)
        if response.status_code >= 400:
            raise RuntimeError(f"Gemini adaptation failed: {response.status_code} {response.text[:500]}")

        data = response.json()
        candidates = data.get("candidates") or []
        candidate = candidates[0] if candidates else {}
        parts = ((candidate.get("content") or {}).get("parts") or [])
        content = "".join(str(part.get("text") or "") for part in parts).strip()
        finish_reason = str(candidate.get("finishReason") or "").strip()
        if not content:
            self._write_gemini_debug(label, content, data, "empty response")
            raise RuntimeError(f"Gemini returned no text for {label}; finishReason={finish_reason or 'unknown'}")
        try:
            parsed = _parse_json_object(content)
            return _extract_segment_texts(parsed, len(translated_segments))
        except Exception as exc:
            self._write_gemini_debug(label, content, data, str(exc))
            truncation = finish_reason.upper() in {"MAX_TOKENS", "LENGTH"}
            reason = "truncated output" if truncation else "invalid JSON/output schema"
            raise RuntimeError(
                f"Gemini {reason} for {label} (finishReason={finish_reason or 'unknown'}): {exc}"
            ) from exc

    def _post_gemini_request(self, model: str, payload: dict, timeout_seconds: int):
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        headers = {"x-goog-api-key": self.config.api_key, "Content-Type": "application/json"}
        current_payload = json.loads(json.dumps(payload))
        response = None
        for _ in range(3):
            response = requests.post(
                url,
                headers=headers,
                json=current_payload,
                timeout=timeout_seconds,
            )
            if response.status_code != 400:
                return response
            error_text = response.text or ""
            normalized_error = re.sub(r"[^a-z]", "", error_text.lower())
            generation_config = current_payload.get("generationConfig") or {}
            changed = False
            if "responseschema" in normalized_error and "responseSchema" in generation_config:
                generation_config.pop("responseSchema", None)
                changed = True
            if "responsemimetype" in normalized_error and "responseMimeType" in generation_config:
                generation_config.pop("responseMimeType", None)
                changed = True
            if not changed:
                return response
        assert response is not None
        return response

    def _write_gemini_debug(self, label: str, content: str, response_data: dict, error: str) -> None:
        configured = str(self.config.gemini_debug_dir or "").strip()
        if not configured:
            return
        try:
            debug_dir = Path(configured).expanduser()
            debug_dir.mkdir(parents=True, exist_ok=True)
            safe_label = re.sub(r"[^A-Za-z0-9_.-]+", "_", label)[:80] or "response"
            stamp = time.strftime("%Y%m%d-%H%M%S")
            path = debug_dir / f"gemini-adaptation-{stamp}-{safe_label}.json"
            path.write_text(
                json.dumps(
                    {
                        "label": label,
                        "error": error,
                        "raw_content": content,
                        "response": response_data,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            self.logger.warning("Saved malformed Gemini adaptation response to %s", path)
        except Exception as debug_error:
            self.logger.debug("Could not save Gemini adaptation debug response: %s", debug_error)

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
        return _extract_segment_texts(parsed, len(translated_segments))


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
    """Parse model JSON with conservative repairs for common LLM damage.

    Repairs are deliberately narrow: markdown fences/think blocks, trailing
    commas, missing commas between adjacent JSON records/fields, and raw control
    characters inside quoted strings. The result still has to pass json.loads.
    """
    errors: list[str] = []
    for candidate in _json_object_candidates(content):
        for variant in (candidate, _repair_json_candidate(candidate)):
            try:
                parsed = json.loads(variant)
            except json.JSONDecodeError as exc:
                errors.append(f"line {exc.lineno} column {exc.colno}: {exc.msg}")
                continue
            if not isinstance(parsed, dict):
                errors.append("top-level JSON value is not an object")
                continue
            return parsed
    detail = errors[-1] if errors else "no balanced JSON object found"
    raise RuntimeError(f"Model response did not include valid JSON: {detail}")


def _json_object_candidates(content: str) -> list[str]:
    text = str(content or "").lstrip("\ufeff").strip()
    if not text:
        return []
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.IGNORECASE | re.DOTALL).strip()
    candidates: list[str] = [text]
    for match in re.finditer(r"```(?:json)?\s*(.*?)```", text, flags=re.IGNORECASE | re.DOTALL):
        fenced = match.group(1).strip()
        if fenced:
            candidates.append(fenced)
    candidates.extend(_balanced_json_objects(text))
    unique: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        candidate = candidate.strip()
        if candidate and candidate not in seen:
            seen.add(candidate)
            unique.append(candidate)
    return unique


def _balanced_json_objects(text: str) -> list[str]:
    objects: list[str] = []
    start: Optional[int] = None
    depth = 0
    in_string = False
    escaped = False
    for index, char in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}" and depth:
            depth -= 1
            if depth == 0 and start is not None:
                objects.append(text[start:index + 1])
                start = None
    # A truncated response may never close its last object. Keep it as a
    # repair candidate only when adding the missing braces is unambiguous.
    if start is not None and depth > 0 and not in_string:
        objects.append(text[start:] + ("}" * depth))
    return objects


def _repair_json_candidate(candidate: str) -> str:
    repaired = _escape_control_characters_in_json_strings(candidate)
    repaired = re.sub(r",\s*([}\]])", r"\1", repaired)
    repaired = re.sub(r"}\s*{", "},{", repaired)
    # A frequent Gemini formatting defect is a missing comma at a newline
    # between a completed value and the next object key.
    repaired = re.sub(
        r"([}\]\"0-9])\s*\n\s*(\"(?:[^\"\\]|\\.)+\"\s*:)",
        r"\1,\n\2",
        repaired,
    )
    return repaired


def _escape_control_characters_in_json_strings(candidate: str) -> str:
    output: list[str] = []
    in_string = False
    escaped = False
    for char in candidate:
        if in_string:
            if escaped:
                output.append(char)
                escaped = False
                continue
            if char == "\\":
                output.append(char)
                escaped = True
                continue
            if char == '"':
                output.append(char)
                in_string = False
                continue
            if char == "\n":
                output.append("\\n")
                continue
            if char == "\r":
                output.append("\\r")
                continue
            if char == "\t":
                output.append("\\t")
                continue
            if ord(char) < 0x20:
                output.append(f"\\u{ord(char):04x}")
                continue
            output.append(char)
            continue
        output.append(char)
        if char == '"':
            in_string = True
    return "".join(output)


def _extract_segment_texts(payload: dict, expected_count: int) -> list[str]:
    items = payload.get("segments")
    if not isinstance(items, list):
        raise RuntimeError("JSON output does not contain a segments array")
    by_index: dict[int, str] = {}
    for item in items:
        if not isinstance(item, dict):
            raise RuntimeError("segments contains a non-object item")
        try:
            index = int(item.get("i"))
        except (TypeError, ValueError) as exc:
            raise RuntimeError("segment item has an invalid i value") from exc
        if index in by_index:
            raise RuntimeError(f"duplicate segment index {index}")
        text = str(item.get("text") or "").strip()
        if not text:
            raise RuntimeError(f"segment {index} has empty text")
        by_index[index] = text
    expected = set(range(expected_count))
    actual = set(by_index)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise RuntimeError(f"segment indices mismatch; missing={missing[:10]} extra={extra[:10]}")
    return [by_index[index] for index in range(expected_count)]


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
