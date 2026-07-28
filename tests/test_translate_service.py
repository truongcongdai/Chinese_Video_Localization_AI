# tests/test_translate_service.py
import asyncio
from dataclasses import dataclass
from typing import Optional

import pytest

from universal_video_ai.translate.service import TranslateService
from universal_video_ai.translate.exceptions import TranslationBackendUnavailable, TranslationFailed


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
