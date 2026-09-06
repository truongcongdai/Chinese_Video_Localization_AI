from pathlib import Path

from universal_video_ai.tts.registry import ProviderHealth, TTSVoice, TTSVoiceRegistry


class FakeProvider:
    provider_id = "local"

    def __init__(self):
        self.synthesis_calls = 0

    def health_check(self):
        return ProviderHealth("local", True, "ready")

    def supports_language(self, language):
        return language.startswith("vi")

    def list_voices(self, language, refresh=False):
        if not self.supports_language(language):
            return []
        return [
            TTSVoice("local", "one", "One", "vi", "vi-VN", None, "speaker-one", True, False, "free-local", "test", True),
            TTSVoice("local", "one-fast", "One fast", "vi", "vi-VN", None, "speaker-one", True, False, "free-local", "test", True),
            TTSVoice("local", "two", "Two", "vi", "vi-VN", None, "speaker-two", True, False, "free-local", "test", True),
        ]

    def synthesize(self, text, output_path, voice_id, language):
        self.synthesis_calls += 1
        output_path.write_bytes(b"audio")
        return output_path


def test_registry_filters_vietnamese_and_counts_real_speakers():
    registry = TTSVoiceRegistry([FakeProvider()])
    voices = registry.list_voices("vi")
    assert len(voices) == 3
    assert registry.distinct_speaker_count(voices) == 2
    assert registry.list_voices("en") == []


def test_pitch_or_rate_style_does_not_create_speaker_identity():
    registry = TTSVoiceRegistry([FakeProvider()])
    assert registry.distinct_speaker_count(registry.list_voices("vi")) == 2


def test_provider_health_and_synthesis(tmp_path: Path):
    registry = TTSVoiceRegistry([FakeProvider()])
    assert registry.health()[0].available
    output = registry.synthesize("Xin chào", tmp_path / "voice.wav", "vi", "local:one")
    assert output.read_bytes() == b"audio"


def test_runtime_voice_verification_is_real_and_cached():
    provider = FakeProvider()
    registry = TTSVoiceRegistry([provider])
    first = registry.list_voices("vi", verify=True)
    assert len(first) == 3
    assert all(voice.verified_synthesis for voice in first)
    assert provider.synthesis_calls == 3
    second = registry.list_voices("vi", verify=True)
    assert all(voice.verified_synthesis for voice in second)
    assert provider.synthesis_calls == 3


def test_edge_catalog_contains_only_two_native_vietnamese_speakers():
    registry = TTSVoiceRegistry()
    voices = registry.list_voices("vi", provider="edge")
    identities = {voice.speaker_identity for voice in voices}
    assert identities == {
        "edge:vi-VN-HoaiMyNeural",
        "edge:vi-VN-NamMinhNeural",
    }
    assert all("|rate=" not in voice.voice_id and "|pitch=" not in voice.voice_id for voice in voices)


def test_unavailable_local_provider_is_not_exposed():
    registry = TTSVoiceRegistry()
    voices = registry.list_voices("vi")
    assert all(voice.available for voice in voices)
    assert all(voice.provider != "piper" for voice in voices)
