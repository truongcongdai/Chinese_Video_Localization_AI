# tests/test_orchestrator_factory.py
from pathlib import Path

from universal_video_ai.orchestrator.factory import create_localization_service


def test_create_localization_service_basic():
    """Test basic service creation without optional features."""
    service = create_localization_service()
    assert service is not None
    assert service.downloader is not None
    assert service.config.run_transcription is False
    assert service.config.run_translation is False
    assert service.config.run_tts is False


def test_create_localization_service_with_transcription():
    """Test service creation with transcription."""
    service = create_localization_service(
        run_transcription=True,
        transcription_language="en",
        transcription_model="small",
    )
    assert service is not None
    assert service.config.run_transcription is True
    assert service.config.transcription_language == "en"
    assert service.config.transcription_model == "small"


def test_create_localization_service_with_translation():
    """Test service creation with translation."""
    service = create_localization_service(
        run_translation=True,
        target_language="vi",
    )
    assert service is not None
    assert service.config.run_translation is True
    assert service.config.target_language == "vi"


def test_create_localization_service_full_pipeline():
    """Test service creation with all features."""
    service = create_localization_service(
        run_demucs=True,
        run_transcription=True,
        transcription_language="en",
        run_translation=True,
        target_language="vi",
        run_tts=True,
        generate_subtitles=True,
        mix_audio=True,
        demucs_output_dir=Path("/tmp/demucs"),
    )
    assert service is not None
    assert service.config.run_demucs is True
    assert service.config.run_transcription is True
    assert service.config.run_translation is True
    assert service.config.run_tts is True
    assert service.config.generate_subtitles is True
    assert service.config.mix_audio is True
