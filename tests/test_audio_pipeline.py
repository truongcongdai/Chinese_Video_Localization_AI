from pathlib import Path
from dataclasses import dataclass
from typing import Optional

import pytest

from universal_video_ai.downloader.download_result import DownloadResult
from universal_video_ai.downloader.platform import Platform
from universal_video_ai.audio.audio_result import AudioResult
from universal_video_ai.audio.pipeline import AudioPipeline, AudioPipelineConfig, AudioPipelineResult
from universal_video_ai.audio.demucs import DemucsOutput


@dataclass
class DummyDemucs:
    """Simple dummy demucs processor for tests."""

    def separate(self, audio_path: Path, output_dir: Optional[Path] = None) -> DemucsOutput:
        base = output_dir or audio_path.parent / "demucs_output"
        base.mkdir(parents=True, exist_ok=True)
        # create fake stem files
        vocals = base / "vocals.wav"
        drums = base / "drums.wav"
        bass = base / "bass.wav"
        other = base / "other.wav"
        for p in (vocals, drums, bass, other):
            p.write_bytes(b"dummy")
        return DemucsOutput(vocals=vocals, drums=drums, bass=bass, other=other)


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


def test_pipeline_extract_only(tmp_path: Path, monkeypatch):
    # Arrange: mock extractor.extract to return a predictable AudioResult
    from universal_video_ai.audio.audio_result import AudioResult as AR

    audio_path = tmp_path / "audio.wav"
    audio_path.write_bytes(b"\x00" * 1024)

    def fake_extract(video_path, output_dir=None):
        return AR(
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

    pipeline = AudioPipeline(config=AudioPipelineConfig(run_demucs=False), extractor=AudioExtractor())

    dr = make_download_result(tmp_path)
    result = pipeline.process(dr, output_dir=tmp_path)

    assert isinstance(result, AudioPipelineResult)
    assert result.audio_result.audio_path == audio_path
    assert result.demucs_output is None


def test_pipeline_with_demucs(tmp_path: Path, monkeypatch):
    # Arrange: fake extractor + inject dummy Demucs
    from universal_video_ai.audio.audio_result import AudioResult as AR
    audio_path = tmp_path / "audio.wav"
    audio_path.write_bytes(b"\x00" * 1024)

    def fake_extract(video_path, output_dir=None):
        return AR(
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

    demucs = DummyDemucs()
    pipeline = AudioPipeline(config=AudioPipelineConfig(run_demucs=True, demucs_output_dir=tmp_path / "demucs_out"),
                             extractor=AudioExtractor(), demucs_processor=demucs)

    dr = make_download_result(tmp_path)
    result = pipeline.process(dr, output_dir=tmp_path)

    assert result.audio_result.audio_path == audio_path
    assert result.demucs_output is not None
    assert result.demucs_output.vocals.exists()
    assert result.demucs_output.drums.exists()
    assert result.demucs_output.bass.exists()
    assert result.demucs_output.other.exists()


def test_pipeline_fails_on_unsuccessful_download(tmp_path: Path):
    dr = DownloadResult(
        success=False,
        platform=Platform.GENERIC,
        original_url="http://x",
        final_url="http://x",
        video_path=tmp_path / "no.mp4",
    )
    pipeline = AudioPipeline()
    with pytest.raises(ValueError):
        pipeline.process(dr)