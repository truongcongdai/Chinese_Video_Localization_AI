"""Premium voice requests recover from transient failures without live HTTP."""
from unittest.mock import Mock

import pytest
import requests

from universal_video_ai.tts.premium import OpenAITTSBackend, ElevenLabsTTSBackend
from universal_video_ai.tts.exceptions import SynthesisError


@pytest.mark.parametrize("backend", [OpenAITTSBackend, ElevenLabsTTSBackend])
def test_premium_tts_retries_timeout_and_closes_response(tmp_path, monkeypatch, backend):
    response = requests.Response()
    response.status_code = 200
    response._content = b"audio"
    response._content_consumed = True
    close = Mock(wraps=response.close)
    response.close = close
    post = Mock(side_effect=[requests.Timeout("interrupted"), response])
    monkeypatch.setattr(requests, "post", post)
    monkeypatch.setattr("universal_video_ai.network.time.sleep", lambda _: None)
    monkeypatch.setattr("universal_video_ai.tts.premium._convert_to_wav", lambda src, dst: src)
    result = backend(api_key="test", default_voice="test").synthesize("hello", tmp_path / "voice.wav")
    assert result.read_bytes() == b"audio"
    assert post.call_count == 2
    close.assert_called_once()


@pytest.mark.parametrize("backend", [OpenAITTSBackend, ElevenLabsTTSBackend])
def test_premium_tts_exhausted_retries_raise_domain_error(tmp_path, monkeypatch, backend):
    post = Mock(side_effect=requests.ConnectionError("offline"))
    monkeypatch.setattr(requests, "post", post)
    monkeypatch.setattr("universal_video_ai.network.time.sleep", lambda _: None)
    with pytest.raises(SynthesisError) as error:
        backend(api_key="test", default_voice="test").synthesize("hello", tmp_path / "voice.wav")
    assert isinstance(error.value.__cause__, requests.ConnectionError)
    assert post.call_count == 3
    assert not list(tmp_path.iterdir())
