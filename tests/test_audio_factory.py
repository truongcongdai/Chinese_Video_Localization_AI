# tests/test_audio_factory.py
from pathlib import Path
import pytest

from universal_video_ai.audio.factory import create_audio_pipeline


def test_create_pipeline_basic():
    """Test basic pipeline creation without transcription/demucs."""
    pipeline = create_audio_pipeline(run_demucs=False, run_transcription=False)
    assert pipeline is not None
    assert pipeline.config.run_demucs is False
    assert pipeline.config.run_transcription is False
    assert pipeline.extractor is not None


def test_create_pipeline_with_transcription():
    """Test pipeline creation with transcription requested."""
    pipeline = create_audio_pipeline(run_transcription=True, transcription_language="en")
    assert pipeline is not None
    assert pipeline.config.run_transcription is True
    assert pipeline.config.transcription_language == "en"
    # Note: speech_service may be None if whisper not available; factory logged warning
    # We don't assert speech_service is not None here — test machines may not have whisper


def test_create_pipeline_with_demucs():
    """Test pipeline creation with demucs requested."""
    pipeline = create_audio_pipeline(run_demucs=True)
    assert pipeline is not None
    assert pipeline.config.run_demucs is True
    # Note: demucs_processor may be None if demucs not available; factory logged warning


def test_create_pipeline_with_all_features():
    """Test pipeline creation with all features requested."""
    demucs_output_dir = Path("/tmp/demucs_out")
    pipeline = create_audio_pipeline(
        run_demucs=True,
        run_transcription=True,
        transcription_language="zh",
        demucs_output_dir=demucs_output_dir,
    )
    assert pipeline is not None
    assert pipeline.config.run_demucs is True
    assert pipeline.config.run_transcription is True
    assert pipeline.config.transcription_language == "zh"
    assert pipeline.config.demucs_output_dir == demucs_output_dir