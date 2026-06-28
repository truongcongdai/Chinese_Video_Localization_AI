# tests/test_orchestrator_service.py
from pathlib import Path
from dataclasses import dataclass
from typing import Optional

import pytest

from universal_video_ai.downloader.platform import Platform
from universal_video_ai.downloader.download_result import DownloadResult
from universal_video_ai.audio.audio_result import AudioResult
from universal_video_ai.translate.service import TranslateService
from universal_video_ai.tts.service import TTSService
from universal_video_ai.orchestrator.service import LocalizationService, LocalizationConfig, LocalizationResult


@dataclass
class DummyDownloadService:
    def __init__(self, video_path: Path):
        self.video_path = video_path

    def download(self, url: str, output_dir: Path):
        return DownloadResult(
            success=True,
            platform=Platform.GENERIC,
            original_url=url,
            final_url=url,
            video_path=self.video_path,
            title="test",
            uploader="test",
            duration=1.0,
            width=640,
            height=360,
            filesize=1024,
            extension="mp4",
        )


@dataclass
class DummyTranslateBackend:
    def translate(self, text: str, source_lang: str, target_lang: str) -> str:
        return f"[{target_lang}] {text}"


@dataclass
class DummyTTSBackend:
    def synthesize(self, text: str, output_path: Path, language: str = "en") -> Path:
        output_path = Path(output_path)
        output_path.write_bytes(b"tts_audio")
        return output_path


def test_localization_basic(tmp_path: Path, monkeypatch):
    """Test basic localization without translation/TTS."""
    video_path = tmp_path / "video.mp4"
    video_path.write_bytes(b"\x00" * 1024)
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

    service = LocalizationService(
        downloader=DummyDownloadService(video_path),
        config=LocalizationConfig(),
    )
    result = service.localize("http://example.com/video", output_dir=tmp_path / "output")

    assert isinstance(result, LocalizationResult)
    assert result.download_result.success
    assert result.audio_pipeline_result.audio_result.audio_path == audio_path


def test_localization_with_translation(tmp_path: Path, monkeypatch):
    """Test localization with translation."""
    video_path = tmp_path / "video.mp4"
    video_path.write_bytes(b"\x00" * 1024)
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

    # Mock AudioPipeline to return transcript
    def fake_process(self, download_result, output_dir=None):
        audio_res = AudioResult(
            success=True,
            audio_path=audio_path,
            duration=1.0,
            sample_rate=44100,
            channels=1,
            bitrate=None,
            format="wav",
            filesize=1024,
        )
        from universal_video_ai.audio.pipeline import AudioPipelineResult
        return AudioPipelineResult(audio_result=audio_res, transcript="hello world")

    from universal_video_ai.audio.pipeline import AudioPipeline
    monkeypatch.setattr(AudioPipeline, "process", fake_process)

    translate_backend = DummyTranslateBackend()
    translate_svc = TranslateService(backend=translate_backend)

    service = LocalizationService(
        downloader=DummyDownloadService(video_path),
        translate_service=translate_svc,
        config=LocalizationConfig(run_translation=True, target_language="vi"),
    )
    result = service.localize("http://example.com/video", output_dir=tmp_path / "output")

    assert result.translated_text is not None
    assert "vi" in result.translated_text


def test_localization_with_tts(tmp_path: Path, monkeypatch):
    """Test localization with TTS."""
    video_path = tmp_path / "video.mp4"
    video_path.write_bytes(b"\x00" * 1024)
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

    def fake_process(self, download_result, output_dir=None):
        audio_res = AudioResult(
            success=True,
            audio_path=audio_path,
            duration=1.0,
            sample_rate=44100,
            channels=1,
            bitrate=None,
            format="wav",
            filesize=1024,
        )
        from universal_video_ai.audio.pipeline import AudioPipelineResult
        return AudioPipelineResult(audio_result=audio_res, transcript="hello world")

    from universal_video_ai.audio.pipeline import AudioPipeline
    monkeypatch.setattr(AudioPipeline, "process", fake_process)

    translate_backend = DummyTranslateBackend()
    translate_svc = TranslateService(backend=translate_backend)
    tts_backend = DummyTTSBackend()
    tts_svc = TTSService(backend=tts_backend)

    service = LocalizationService(
        downloader=DummyDownloadService(video_path),
        translate_service=translate_svc,
        tts_service=tts_svc,
        config=LocalizationConfig(
            run_translation=True,
            run_tts=True,
            target_language="vi",
        ),
    )
    result = service.localize("http://example.com/video", output_dir=tmp_path / "output")

    assert result.translated_text is not None
    assert result.tts_audio_path is not None
    assert result.tts_audio_path.exists()