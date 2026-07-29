# tests/test_translate_service.py
import asyncio
from dataclasses import dataclass
from typing import Optional

import pytest

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
