# tests/test_orchestrator_service.py
from pathlib import Path
from dataclasses import dataclass

import pytest

from universal_video_ai.downloader.platform import Platform
from universal_video_ai.downloader.download_result import DownloadResult
from universal_video_ai.audio.audio_result import AudioResult
from universal_video_ai.orchestrator.service import LocalizationService, LocalizationConfig, LocalizationResult


def test_localization_service_basic(tmp_path: Path, monkeypatch):
    """Test basic localization without demucs/transcription."""
    video_path = tmp_path / "video.mp4"
    video_path.write_bytes(b"\x00" * 1024)

    # Mock DownloadService.download
    @dataclass
    class DummyDownloadService:
        def download(self, url: str, output_dir: Path):
            return DownloadResult(
                success=True,
                platform=Platform.GENERIC,
                original_url=url,
                final_url=url,
                video_path=video_path,
                title="test",
                uploader="test",
                duration=1.0,
                width=640,
                height=360,
                filesize=1024,
                extension="mp4",
            )

    # Mock AudioExtractor.extract
    audio_path = tmp_path / "audio.wav"
    audio_path.write_bytes(b"\x00" * 1024)

    def fake_extract(video_path, output_dir=None):
        return AudioResult(
            success=True,
            audio_path=audio_path,
            duration=1.0,
            sample_rate=44100,
            channels=1,
            bitrate=None,
            format="wav",
            filesize=1024,
        )

    from universal_video_ai.audio.extractor import AudioExtractor
    monkeypatch.setattr(AudioExtractor, "extract", staticmethod(fake_extract))

    # Create service and run
    service = LocalizationService(downloader=DummyDownloadService(), config=LocalizationConfig())
    result = service.localize("http://example.com/video.mp4", output_dir=tmp_path / "output")

    assert isinstance(result, LocalizationResult)
    assert result.download_result.success is True
    assert result.audio_pipeline_result.audio_result.audio_path == audio_path
    assert result.audio_pipeline_result.transcript is None  # no transcription
    assert result.audio_pipeline_result.demucs_output is None  # no demucs


def test_localization_service_failed_download(tmp_path: Path):
    """Test that failed download raises ValueError."""
    @dataclass
    class DummyDownloadService:
        def download(self, url: str, output_dir: Path):
            return DownloadResult(
                success=False,
                platform=Platform.GENERIC,
                original_url=url,
                final_url=url,
                video_path=tmp_path / "nonexistent.mp4",
            )

    service = LocalizationService(downloader=DummyDownloadService())
    with pytest.raises(ValueError):
        service.localize("http://example.com/video.mp4", output_dir=tmp_path / "output")