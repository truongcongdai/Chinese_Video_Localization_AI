# tests/test_audio_pipeline_transcription.py
from pathlib import Path
from dataclasses import dataclass
from typing import Optional

import pytest

from universal_video_ai.downloader.download_result import DownloadResult
from universal_video_ai.downloader.platform import Platform
from universal_video_ai.audio.audio_result import AudioResult
from universal_video_ai.audio.pipeline import AudioPipeline, AudioPipelineConfig, AudioPipelineResult
from universal_video_ai.speech.service import SpeechService


@dataclass
class DummyBackend:
    """Simple dummy speech backend implementing transcribe()."""

    def transcribe(self, audio_path: Path, language: Optional[str] = None) -> str:
        return f"TRANSCRIPT for {audio_path.name} lang={language}"


def make_download_result(tmp_path: Path) -> DownloadResult:
    video = tmp_path / "video.mp4"
    video.write_bytes(b"\x00" * 1024)
    return DownloadResult(
        success=True,
        platform=Platform.GENERIC,
        original_url="http://example.com/video",
        final_url="http://example.com/video",
        video_path=video,
        title="video",
        uploader="uploader",
        duration=1.0,
        width=640,
        height=360,
        filesize=1024,
        extension="mp4",
    )


def test_pipeline_transcription_success(tmp_path: Path, monkeypatch):
    # Arrange: mock extractor.extract to return a predictable AudioResult
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

    backend = DummyBackend()
    service = SpeechService(backend=backend)

    pipeline = AudioPipeline(config=AudioPipelineConfig(run_transcription=True, transcription_language="en"),
                             extractor=AudioExtractor(), speech_service=service)

    dr = make_download_result(tmp_path)
    result = pipeline.process(dr, output_dir=tmp_path)

    assert isinstance(result, AudioPipelineResult)
    assert result.audio_result.audio_path == audio_path
    assert result.transcript is not None
    assert "TRANSCRIPT" in result.transcript
    assert "lang=en" in result.transcript


def test_pipeline_transcription_no_service_raises(tmp_path: Path, monkeypatch):
    # mock extractor again
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

    pipeline = AudioPipeline(config=AudioPipelineConfig(run_transcription=True), extractor=AudioExtractor())

    dr = make_download_result(tmp_path)
    with pytest.raises(RuntimeError):
        pipeline.process(dr, output_dir=tmp_path)