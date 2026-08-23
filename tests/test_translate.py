# tests/test_translate.py
import asyncio

import httpx

from universal_video_ai.translate import (
    TranslatorConfig,
    TranslatorFactory,
    NoOpTranslator,
    TranslationError,
)
from typing import Any


def test_factory_default_noop():
    cfg = TranslatorConfig()  # default provider = noop
    translator = TranslatorFactory.create(cfg)
    assert isinstance(translator, NoOpTranslator)


def test_noop_translator_returns_same_text():
    translator = NoOpTranslator()
    text = "Hello world!"
    out = asyncio.run(translator.translate(text, src_lang="en", dest_lang="vi"))
    assert out == text


def test_noop_invalid_input():
    translator = NoOpTranslator()
    try:
        asyncio.run(translator.translate(123))  # type: ignore[arg-type]
        assert False, "Expected TranslationError for non-string input"
    except TranslationError:
        pass


def test_factory_unknown_provider_raises():
    cfg = TranslatorConfig(provider="nonexistent")
    try:
        TranslatorFactory.create(cfg)
        assert False, "Expected ValueError for unknown provider"
    except ValueError:
        pass


def test_google_retry_delay_honors_retry_after():
    translator = TranslatorFactory.create(TranslatorConfig(provider="google"))
    request = httpx.Request("POST", "https://translate.googleapis.com")
    response = httpx.Response(429, headers={"Retry-After": "7"}, request=request)
    error = httpx.HTTPStatusError("rate limited", request=request, response=response)

    assert translator._retry_delay(error, 0) == 7.0


def test_google_translation_uses_post_and_small_batches():
    translator = TranslatorFactory.create(TranslatorConfig(provider="google"))

    class FakeClient:
        def __init__(self):
            self.calls = []

        async def post(self, url, data):
            self.calls.append((url, data))
            separator = "\n[[[UVAI_SEG_BREAK]]]\n"
            translated = separator.join(f"vi:{part}" for part in data["q"].split(separator))
            return httpx.Response(
                200,
                json=[[[translated, None, None, None]]],
                request=httpx.Request("POST", url),
            )

    client = FakeClient()
    translator._direct_client = client
    result = asyncio.run(translator.translate_batch([f"line {i}" for i in range(21)], "zh", "vi"))

    assert result == [f"vi:line {i}" for i in range(21)]
    sizes = [len(call[1]["q"].split("[[[UVAI_SEG_BREAK]]]")) for call in client.calls]
    assert sizes == [10, 10, 1]
    assert all(len(call[1]["q"]) <= 1000 for call in client.calls)
