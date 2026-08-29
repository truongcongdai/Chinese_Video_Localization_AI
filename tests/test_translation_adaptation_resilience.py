from __future__ import annotations

import asyncio

import pytest

from universal_video_ai.segment import TranscriptSegment
from universal_video_ai.translate.adapt import (
    AdaptationConfig,
    SegmentAdapter,
    _extract_segment_texts,
    _parse_json_object,
)


def _segments(prefix: str, count: int) -> list[TranscriptSegment]:
    return [
        TranscriptSegment(start=float(i), end=float(i + 1), text=f"{prefix} {i}")
        for i in range(count)
    ]


def test_parse_json_repairs_fence_missing_commas_and_trailing_comma():
    content = '''```json
    {
      "segments": [
        {"i": 0, "text": "Xin chào"}
        {"i": 1, "text": "Tạm biệt"},
      ]
    }
    ```'''

    parsed = _parse_json_object(content)

    assert _extract_segment_texts(parsed, 2) == ["Xin chào", "Tạm biệt"]


def test_parse_json_repairs_raw_newline_inside_text():
    content = '{"segments":[{"i":0,"text":"Dòng một\nDòng hai"}]}'

    parsed = _parse_json_object(content)

    assert _extract_segment_texts(parsed, 1) == ["Dòng một\nDòng hai"]


def test_segment_schema_rejects_missing_or_duplicate_indices():
    with pytest.raises(RuntimeError, match="indices mismatch"):
        _extract_segment_texts({"segments": [{"i": 1, "text": "Sai index"}]}, 1)

    with pytest.raises(RuntimeError, match="duplicate"):
        _extract_segment_texts(
            {"segments": [{"i": 0, "text": "A"}, {"i": 0, "text": "B"}]},
            2,
        )


def test_gemini_full_failure_degrades_to_smaller_batches(monkeypatch):
    source = _segments("nguồn", 5)
    draft = _segments("nháp", 5)
    adapter = SegmentAdapter(
        AdaptationConfig(
            enabled=True,
            provider="gemini",
            api_key="test",
            fallback_on_error=True,
            gemini_retry_count=0,
            gemini_batch_size=2,
        )
    )
    labels: list[str] = []

    def fake_request(source_segments, translated_segments, source_lang, target_lang, *, label):
        labels.append(label)
        if label == "full":
            raise RuntimeError("malformed full JSON")
        return [f"đã sửa {segment.text}" for segment in translated_segments]

    monkeypatch.setattr(adapter, "_request_gemini_with_retries", fake_request)

    result = adapter._adapt_with_gemini(source, draft, "zh", "vi")

    assert labels == ["full", "batch-0-1", "batch-2-3", "batch-4-4"]
    assert result == [f"đã sửa nháp {i}" for i in range(5)]


def test_oversized_gemini_script_skips_guaranteed_to_truncate_full_request(monkeypatch):
    source = _segments("source", 60)
    draft = _segments("draft", 60)
    adapter = SegmentAdapter(
        AdaptationConfig(
            enabled=True,
            provider="gemini",
            api_key="test",
            gemini_retry_count=0,
            gemini_batch_size=10,
        )
    )
    labels: list[str] = []

    def fake_request(source_segments, translated_segments, source_lang, target_lang, *, label):
        labels.append(label)
        return [segment.text for segment in translated_segments]

    monkeypatch.setattr(adapter, "_request_gemini_with_retries", fake_request)

    result = adapter._adapt_with_gemini(source, draft, "zh", "vi")

    assert len(result) == 60
    assert "full" not in labels
    assert labels == [f"batch-{start}-{start + 9}" for start in range(0, 60, 10)]


def test_gemini_batch_circuit_breaker_stops_repeating_provider_failure(monkeypatch):
    source = _segments("source", 20)
    draft = _segments("draft", 20)
    adapter = SegmentAdapter(
        AdaptationConfig(
            enabled=True,
            provider="gemini",
            api_key="test",
            fallback_on_error=True,
            gemini_retry_count=0,
            gemini_batch_size=2,
        )
    )
    labels: list[str] = []

    def fake_request(source_segments, translated_segments, source_lang, target_lang, *, label):
        labels.append(label)
        if label == "full":
            raise RuntimeError("malformed full JSON")
        raise RuntimeError("malformed batch JSON")

    monkeypatch.setattr(adapter, "_request_gemini_with_retries", fake_request)

    result = adapter._adapt_with_gemini(source, draft, "zh", "vi")

    assert labels == ["full", "batch-0-1", "batch-2-3", "batch-4-5"]
    assert result == [segment.text for segment in draft]


def test_direct_gemini_failure_subdivides_instead_of_falling_back_to_empty_drafts(monkeypatch):
    source = _segments("source", 8)
    adapter = SegmentAdapter(
        AdaptationConfig(
            enabled=True,
            provider="gemini",
            api_key="test",
            fallback_on_error=True,
            gemini_retry_count=0,
            gemini_batch_size=4,
        )
    )
    labels: list[str] = []

    def fake_request(source_segments, translated_segments, source_lang, target_lang, *, label):
        labels.append(label)
        if label in {"full", "batch-0-3"}:
            raise RuntimeError("segment indices mismatch; missing=[3] extra=[]")
        return [f"translated {segment.text}" for segment in source_segments]

    monkeypatch.setattr(adapter, "_request_gemini_with_retries", fake_request)

    result = asyncio.run(adapter.translate_source_segments(source, "zh", "vi"))

    assert [segment.text for segment in result] == [
        f"translated source {index}" for index in range(8)
    ]
    assert labels == [
        "full",
        "batch-0-3",
        "batch-0-1",
        "batch-2-3",
        "batch-4-7",
    ]


def test_optional_adaptation_failure_keeps_base_translation(monkeypatch):
    source = _segments("nguồn", 3)
    draft = _segments("bản dịch", 3)
    adapter = SegmentAdapter(
        AdaptationConfig(
            enabled=True,
            provider="gemini",
            api_key="test",
            fallback_on_error=True,
        )
    )

    def always_fail(*args, **kwargs):
        raise RuntimeError("Gemini returned malformed JSON")

    monkeypatch.setattr(adapter, "_adapt_with_gemini", always_fail)

    result = asyncio.run(adapter.adapt_segments(source, draft, "zh", "vi"))

    assert [segment.text for segment in result] == [segment.text for segment in draft]
    assert [(segment.start, segment.end) for segment in result] == [
        (segment.start, segment.end) for segment in draft
    ]


def test_gemini_schema_controls_fall_back_for_older_models(monkeypatch):
    adapter = SegmentAdapter(
        AdaptationConfig(enabled=True, provider="gemini", api_key="test")
    )
    payloads: list[dict] = []

    class DummyResponse:
        def __init__(self, status_code: int, text: str):
            self.status_code = status_code
            self.text = text

    def fake_post(url, headers, json, timeout):
        import copy
        payloads.append(copy.deepcopy(json))
        if len(payloads) == 1:
            return DummyResponse(400, "Unknown name responseSchema")
        return DummyResponse(200, "ok")

    monkeypatch.setattr("universal_video_ai.translate.adapt.requests.post", fake_post)

    response = adapter._post_gemini_request(
        "gemini-test",
        {
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseSchema": {"type": "OBJECT"},
            }
        },
        30,
    )

    assert response.status_code == 200
    assert "responseSchema" in payloads[0]["generationConfig"]
    assert "responseSchema" not in payloads[1]["generationConfig"]
    assert "responseMimeType" in payloads[1]["generationConfig"]
