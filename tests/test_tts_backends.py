# tests/test_tts_backends.py
import pytest
from pathlib import Path

from universal_video_ai.tts.tts import (
    TTS,
    TTSConfig,
    NoOpTTS,
    EdgeTTS,
    TTSFactory,
)


def test_noop_tts_synthesize(tmp_path: Path):
    """Test NoOpTTS creates placeholder file."""
    config = TTSConfig(provider="noop")
    tts = NoOpTTS(config=config)
    
    output = tmp_path / "output.mp3"
    result = tts.synthesize("Hello world", output)
    
    assert result == output
    assert output.exists()
    content = output.read_text()
    assert "TTS_PLACEHOLDER" in content
    assert "Hello world" in content


def test_noop_tts_invalid_input(tmp_path: Path):
    """Test NoOpTTS raises error for invalid input."""
    config = TTSConfig(provider="noop")
    tts = NoOpTTS(config=config)
    
    output = tmp_path / "output.mp3"
    
    with pytest.raises(ValueError):
        tts.synthesize("", output)
    
    with pytest.raises(ValueError):
        tts.synthesize(123, output)  # type: ignore


def test_tts_factory_noop():
    """Test factory creates NoOpTTS for noop provider."""
    config = TTSConfig(provider="noop")
    tts = TTSFactory.create(config=config)
    
    assert isinstance(tts, NoOpTTS)


def test_tts_factory_edge():
    """Test factory creates EdgeTTS for edge provider."""
    config = TTSConfig(provider="edge")
    tts = TTSFactory.create(config=config)
    
    assert isinstance(tts, EdgeTTS)


def test_tts_factory_azure_unavailable():
    """Test factory raises error when azure provider unavailable."""
    config = TTSConfig(provider="azure", api_key="test_key")
    
    # If azure-cognitiveservices-speech is not installed, should raise ValueError
    try:
        import azure.cognitiveservices.speech  # type: ignore
        # If available, skip this test
        pytest.skip("azure-cognitiveservices-speech is available")
    except ImportError:
        with pytest.raises(ValueError, match="azure-cognitiveservices-speech is not available"):
            TTSFactory.create(config=config)


def test_tts_factory_azure_requires_api_key():
    """Test factory raises error when azure provider missing api_key."""
    # Skip if azure-cognitiveservices-speech is not available
    try:
        import azure.cognitiveservices.speech  # type: ignore
    except ImportError:
        pytest.skip("azure-cognitiveservices-speech is not available")
    
    config = TTSConfig(provider="azure")
    
    with pytest.raises(ValueError, match="requires api_key"):
        TTSFactory.create(config=config)


def test_tts_factory_google_unavailable():
    """Test factory raises error when google provider unavailable."""
    config = TTSConfig(provider="google")
    
    # If gTTS is not installed, should raise ValueError
    try:
        from gtts import gTTS  # type: ignore
        # If available, skip this test
        pytest.skip("gTTS is available")
    except ImportError:
        with pytest.raises(ValueError, match="gTTS is not available"):
            TTSFactory.create(config=config)


def test_tts_factory_unknown_provider():
    """Test factory raises error for unknown provider."""
    config = TTSConfig(provider="unknown")
    
    with pytest.raises(ValueError, match="Unknown TTS provider"):
        TTSFactory.create(config=config)


def test_tts_config_defaults():
    """Test TTSConfig has correct defaults."""
    config = TTSConfig()
    
    assert config.provider == "noop"
    assert config.voice == "en-US-JennyNeural"
    assert config.output_format == "mp3"
    assert config.api_key is None
    assert config.region is None


def test_tts_config_with_api_key():
    """Test TTSConfig accepts api_key and region."""
    config = TTSConfig(
        provider="azure",
        api_key="test_key",
        region="westus"
    )
    
    assert config.provider == "azure"
    assert config.api_key == "test_key"
    assert config.region == "westus"
