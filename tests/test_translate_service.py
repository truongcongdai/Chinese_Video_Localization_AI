# tests/test_translate_service.py
import asyncio
from dataclasses import dataclass
from typing import Optional

import pytest
from universal_video_ai.cache import SQLiteCache

from universal_video_ai.translate.service import TranslateService
from universal_video_ai.translate.exceptions import TranslationBackendUnavailable, TranslationFailed
from universal_video_ai.translate.speech_fit import (
    SpeechFitConfig,
    fit_translated_segments,
    speech_fit_report,
)
from universal_video_ai.segment import TranscriptSegment


@dataclass
class DummyTranslateBackend:
    async def translate(self, text: str, source_lang: str, target_lang: str) -> str:
        return f"translated:{text}:{source_lang}:{target_lang}"


def test_translate_service_success():
    backend = DummyTranslateBackend()
    svc = TranslateService(backend=backend)
    result = asyncio.run(svc.translate("hello", "en", "vi"))
    assert "translated" in result
    assert "hello" in result


def test_translate_service_no_backend_raises():
    svc = TranslateService(backend=None)
    with pytest.raises(TranslationBackendUnavailable):
        asyncio.run(svc.translate("hello", "en", "vi"))


def test_batch_translation_resumes_from_persistent_checkpoints(tmp_path):
    class CheckpointBackend:
        provider = "google"

        def __init__(self, fail_on_call=None):
            self.fail_on_call = fail_on_call
            self.calls = []

        async def translate_batch(self, texts, source_lang, target_lang):
            self.calls.append(list(texts))
            if self.fail_on_call == len(self.calls):
                raise RuntimeError("provider interrupted")
            return [f"vi:{text}" for text in texts]

    segments = [
        TranscriptSegment(start=float(index), end=float(index + 1), text=f"source-{index}")
        for index in range(5)
    ]
    cache_path = tmp_path / "translations.sqlite3"
    first_backend = CheckpointBackend(fail_on_call=2)
    first_service = TranslateService(
        backend=first_backend,
        cache=SQLiteCache(cache_path),
        batch_checkpoint_size=2,
    )

    with pytest.raises(RuntimeError, match="provider interrupted"):
        asyncio.run(first_service.translate_segments(segments, "zh", "vi"))

    assert first_backend.calls == [["source-0", "source-1"], ["source-2", "source-3"]]

    resumed_backend = CheckpointBackend()
    resumed_service = TranslateService(
        backend=resumed_backend,
        cache=SQLiteCache(cache_path),
        batch_checkpoint_size=2,
    )
    result = asyncio.run(resumed_service.translate_segments(segments, "zh", "vi"))

    assert resumed_backend.calls == [["source-2", "source-3"], ["source-4"]]
    assert [segment.text for segment in result] == [f"vi:source-{index}" for index in range(5)]


def test_speech_fit_reports_overlong_segment_without_truncating_by_default():
    segment = TranscriptSegment(
        start=10.0,
        end=12.0,
        text="Đây thực sự là một cách giải thích rất là dài về cơ bản ở đây cho phụ đề mới",
    )

    fitted = fit_translated_segments([segment], SpeechFitConfig(max_cps=12.0))[0]

    assert fitted.start == segment.start
    assert fitted.end == segment.end
    assert fitted.text == segment.text
    assert not speech_fit_report(fitted, SpeechFitConfig(max_cps=12.0)).fits


def test_speech_fit_can_locally_rewrite_when_explicitly_enabled():
    segment = TranscriptSegment(
        start=10.0,
        end=12.0,
        text="Đây thực sự là một cách giải thích rất là dài về cơ bản ở đây cho phụ đề mới",
    )

    fitted = fit_translated_segments([
        segment
    ], SpeechFitConfig(max_cps=12.0, allow_local_rewrite=True, allow_truncation=True))[0]

    assert len(fitted.text) < len(segment.text)
    assert "thực sự" not in fitted.text
