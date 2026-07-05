# tests/test_translate_backends.py
import pytest
from pathlib import Path

from universal_video_ai.translate.translator import (
    Translator,
    TranslatorConfig,
    NoOpTranslator,
    TranslatorFactory,
    TranslationError,
)


def test_noop_translator():
    """Test NoOpTranslator returns input unchanged."""
    config = TranslatorConfig(provider="noop")
    translator = NoOpTranslator(config=config)
    
    result = translator.translate("Hello world", src_lang="en", dest_lang="vi")
    assert result == "Hello world"


def test_noop_translator_invalid_input():
    """Test NoOpTranslator raises error for non-string input."""
    config = TranslatorConfig(provider="noop")
    translator = NoOpTranslator(config=config)
    
    with pytest.raises(TranslationError):
        translator.translate(123)  # type: ignore


def test_translator_factory_noop():
    """Test factory creates NoOpTranslator for noop provider."""
    config = TranslatorConfig(provider="noop")
    translator = TranslatorFactory.create(config=config)
    
    assert isinstance(translator, NoOpTranslator)
    assert translator.translate("test") == "test"


def test_translator_factory_google_unavailable():
    """Test factory raises error when google provider unavailable."""
    config = TranslatorConfig(provider="google")
    
    # If googletrans is not installed, should raise ValueError
    try:
        from googletrans import Translator  # type: ignore
        # If available, skip this test
        pytest.skip("googletrans is available")
    except ImportError:
        with pytest.raises(ValueError, match="googletrans is not available"):
            TranslatorFactory.create(config=config)


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
