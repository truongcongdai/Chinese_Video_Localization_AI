# tests/test_translate.py
import asyncio

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
