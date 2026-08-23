# tests/test_translate_backends.py
import asyncio
import pytest
from pathlib import Path

from universal_video_ai.translate.translator import (
    Translator,
    TranslatorConfig,
    NoOpTranslator,
    TranslatorFactory,
    TranslationError,
    TranslationRateLimitError,
)


def test_noop_translator():
    """Test NoOpTranslator returns input unchanged."""
    config = TranslatorConfig(provider="noop")
    translator = NoOpTranslator(config=config)
    
    result = asyncio.run(translator.translate("Hello world", src_lang="en", dest_lang="vi"))
    assert result == "Hello world"


def test_noop_translator_invalid_input():
    """Test NoOpTranslator raises error for non-string input."""
    config = TranslatorConfig(provider="noop")
    translator = NoOpTranslator(config=config)
    
    with pytest.raises(TranslationError):
        asyncio.run(translator.translate(123))  # type: ignore


def test_translator_factory_noop():
    """Test factory creates NoOpTranslator for noop provider."""
    config = TranslatorConfig(provider="noop")
    translator = TranslatorFactory.create(config=config)
    
    assert isinstance(translator, NoOpTranslator)
    assert asyncio.run(translator.translate("test")) == "test"


def test_translator_factory_google_uses_httpx_backend():
    config = TranslatorConfig(provider="google")

    translator = TranslatorFactory.create(config=config)

    assert callable(translator.translate)


def test_translator_factory_deepl_unavailable():
    """Test factory raises error when deepl provider unavailable."""
    config = TranslatorConfig(provider="deepl", api_key="test_key")
    
    # If deepl is not installed, should raise ValueError
    try:
        import deepl  # type: ignore
        # If available, skip this test
        pytest.skip("deepl is available")
    except ImportError:
        with pytest.raises(ValueError, match="deepl is not available"):
            TranslatorFactory.create(config=config)


def test_translator_factory_deepl_requires_api_key():
    """Test factory raises error when deepl provider missing api_key."""
    # Skip if deepl is not available
    try:
        import deepl  # type: ignore
    except ImportError:
        pytest.skip("deepl is not available")
    
    config = TranslatorConfig(provider="deepl")
    
    with pytest.raises(ValueError, match="requires api_key"):
        TranslatorFactory.create(config=config)


def test_translator_factory_unknown_provider():
    """Test factory raises error for unknown provider."""
    config = TranslatorConfig(provider="unknown")
    
    with pytest.raises(ValueError, match="Unknown translation provider"):
        TranslatorFactory.create(config=config)


def test_translator_config_defaults():
    """Test TranslatorConfig has correct defaults."""
    config = TranslatorConfig()
    
    assert config.provider == "noop"
    assert config.api_key is None
    assert config.src_lang is None
    assert config.dest_lang is None


def test_google_batch_recovers_when_separator_is_changed(monkeypatch):
    translator = TranslatorFactory.create(TranslatorConfig(provider="google"))
    texts = [f"segment {index}" for index in range(25)]
    original_translate = translator.translate
    calls = []

    async def fake_translate(text, src_lang=None, dest_lang=None):
        calls.append(text)
        # Reproduce the production failure: Google returns 24 markers/parts
        # for a request containing 25 source segments.
        if text.count("[[[UVAI_SEG_BREAK]]]") == 24:
            return text.replace("[[[UVAI_SEG_BREAK]]]", "UVAI SEG BREAK", 1)
        return text

    monkeypatch.setattr(translator, "translate", fake_translate)
    try:
        result = asyncio.run(translator.translate_batch(texts, "zh", "vi"))
    finally:
        monkeypatch.setattr(translator, "translate", original_translate)

    assert result == texts
    assert len(calls) > 1


def test_google_uses_post_without_putting_text_in_query():
    translator = TranslatorFactory.create(TranslatorConfig(provider="google"))
    captured = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return [[["xin chao"]]]

    class Client:
        async def post(self, url, **kwargs):
            captured["url"] = url
            captured.update(kwargs)
            return Response()

    translator._direct_client = Client()
    result = asyncio.run(translator.translate("你好", "zh", "vi"))

    assert result == "xin chao"
    assert captured["url"].endswith("/translate_a/single")
    assert captured["data"]["q"] == "你好"
    assert "params" not in captured


def test_google_429_uses_backoff_then_raises_concise_error(monkeypatch):
    import httpx

    translator = TranslatorFactory.create(TranslatorConfig(provider="google"))
    request = httpx.Request("POST", "https://translate.googleapis.com/translate_a/single")
    response = httpx.Response(429, headers={"Retry-After": "1"}, request=request)
    sleeps = []

    async def fail(*args, **kwargs):
        raise httpx.HTTPStatusError("rate limited", request=request, response=response)

    async def fake_sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr(translator, "_translate_direct", fail)
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    with pytest.raises(TranslationRateLimitError) as error:
        asyncio.run(translator.translate("你好", "zh", "vi"))

    assert sleeps == [1.0, 1.0]
    assert "HTTP 429" in str(error.value)
    assert "q=" not in str(error.value)


def test_google_antibot_redirect_is_rate_limit_without_retry(monkeypatch):
    import httpx

    translator = TranslatorFactory.create(TranslatorConfig(provider="google"))
    request = httpx.Request("POST", "https://translate.googleapis.com/translate_a/single")
    response = httpx.Response(
        302,
        headers={"Location": "https://www.google.com/sorry/index?continue=translate"},
        request=request,
    )
    sleeps = []

    async def fail(*args, **kwargs):
        raise httpx.HTTPStatusError("captcha", request=request, response=response)

    async def fake_sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr(translator, "_translate_direct", fail)
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    with pytest.raises(TranslationRateLimitError) as error:
        asyncio.run(translator.translate("hello", "zh", "vi"))

    assert error.value.status_code == 302
    assert error.value.retry_after == 3600.0
    assert sleeps == []
    assert "anti-bot" in str(error.value)


def test_google_large_segment_batch_uses_few_requests(monkeypatch):
    translator = TranslatorFactory.create(TranslatorConfig(provider="google"))
    texts = [f"segment {index:03d}" for index in range(161)]
    calls = []

    async def fake_translate(text, src_lang=None, dest_lang=None):
        calls.append(text)
        return text

    monkeypatch.delenv("GOOGLE_TRANSLATION_BATCH_SEGMENTS", raising=False)
    monkeypatch.delenv("GOOGLE_TRANSLATION_BATCH_CHARS", raising=False)
    monkeypatch.setattr(translator, "translate", fake_translate)

    result = asyncio.run(translator.translate_batch(texts, "zh", "vi"))
