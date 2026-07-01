# tests/test_orchestrator_service.py
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from universal_video_ai.orchestrator.service import LocalizationService, LocalizationConfig, LocalizationResult
from universal_video_ai.downloader.download_result import DownloadResult
from universal_video_ai.audio.pipeline import AudioPipelineResult, AudioResult
from universal_video_ai.translate.service import TranslateService
from universal_video_ai.tts.service import TTSService
from universal_video_ai.timeline.service import TimelineService, TimelineSegment
from universal_video_ai.mixer.service import MixerService
from universal_video_ai.render.renderer import Renderer
from universal_video_ai.downloader.service import DownloadService


def test_localization_service_basic_flow(tmp_path: Path):
    """Test basic localization without translation, TTS, or rendering."""
    # Mock services
    downloader = MagicMock(spec=DownloadService)
    timeline = MagicMock(spec=TimelineService)
    mixer = MagicMock(spec=MixerService)
    renderer = MagicMock(spec=Renderer)

    # Mock download result
    download_result = MagicMock(spec=DownloadResult)
    download_result.success = True
    download_result.video_path = tmp_path / "video.mp4"
    downloader.download.return_value = download_result

    # Create service
    config = LocalizationConfig(render_video=False)
    service = LocalizationService(
        downloader=downloader,
        timeline=timeline,
        mixer=mixer,
        renderer=renderer,
        config=config,
    )

    # Mock audio pipeline
    with patch("universal_video_ai.orchestrator.service.create_audio_pipeline") as mock_pipeline_factory:
        audio_result_obj = MagicMock()
        audio_result_obj.audio_path = tmp_path / "audio.wav"
        audio_result_obj.duration = 10.0

        audio_result = MagicMock(spec=AudioPipelineResult)
        audio_result.transcript = "Hello world"
        audio_result.audio_result = audio_result_obj

        mock_pipeline = MagicMock()
        mock_pipeline.process.return_value = audio_result
        mock_pipeline_factory.return_value = mock_pipeline

        result = service.localize(str(tmp_path), tmp_path)

        assert isinstance(result, LocalizationResult)
        assert result.download_result == download_result
        assert result.audio_pipeline_result == audio_result


def test_localization_service_with_translation_and_tts(tmp_path: Path):
    """Test localization with translation and TTS enabled."""
    downloader = MagicMock(spec=DownloadService)
    translate_service = MagicMock(spec=TranslateService)
    tts_service = MagicMock(spec=TTSService)
    mixer = MagicMock(spec=MixerService)
    renderer = MagicMock(spec=Renderer)

    # Mock download
    download_result = MagicMock(spec=DownloadResult)
    download_result.success = True
    download_result.video_path = tmp_path / "video.mp4"
    downloader.download.return_value = download_result

    # Mock translation
    translate_service.translate.return_value = "Xin chào thế giới"

    # Mock TTS
    tts_service.synthesize.return_value = None

    # Mock mixer
    mixer.mix.return_value = None

    config = LocalizationConfig(
        run_translation=True,
        target_language="vi",
        run_tts=True,
        mix_audio=True,
        render_video=False,
    )
    service = LocalizationService(
        downloader=downloader,
        translate_service=translate_service,
        tts_service=tts_service,
        mixer=mixer,
        renderer=renderer,
        config=config,
    )

    with patch("universal_video_ai.orchestrator.service.create_audio_pipeline") as mock_pipeline_factory:
        audio_result_obj = MagicMock()
        audio_result_obj.audio_path = tmp_path / "audio.wav"
        audio_result_obj.duration = 10.0

        audio_result = MagicMock(spec=AudioPipelineResult)
        audio_result.transcript = "Hello world"
        audio_result.audio_result = audio_result_obj

        mock_pipeline = MagicMock()
        mock_pipeline.process.return_value = audio_result
        mock_pipeline_factory.return_value = mock_pipeline

        result = service.localize(str(tmp_path), tmp_path)

        assert result.translated_text == "Xin chào thế giới"
        assert result.tts_audio_path is not None
        assert result.mixed_audio_path is not None
        translate_service.translate.assert_called_once()
        tts_service.synthesize.assert_called_once()
        mixer.mix.assert_called_once()


def test_localization_service_with_rendering(tmp_path: Path):
    """Test complete localization workflow with rendering enabled."""
    downloader = MagicMock(spec=DownloadService)
    translate_service = MagicMock(spec=TranslateService)
    tts_service = MagicMock(spec=TTSService)
    timeline = MagicMock(spec=TimelineService)
    mixer = MagicMock(spec=MixerService)
    renderer = MagicMock(spec=Renderer)

    # Mock download
    download_result = MagicMock(spec=DownloadResult)
    download_result.success = True
    download_result.video_path = tmp_path / "video.mp4"
    downloader.download.return_value = download_result

    # Mock translation
    translate_service.translate.return_value = "Xin chào thế giới"

    # Mock TTS
    tts_audio = tmp_path / "tts_audio.wav"
    tts_audio.write_bytes(b"tts")
    tts_service.synthesize.side_effect = lambda text, output_path, language: output_path.write_bytes(b"tts")

    # Mock subtitles
    # Mock subtitles
    segments = [TimelineSegment(start_time=0.0, end_time=5.0, text="Hello")]
    timeline.align_transcript.return_value = segments
    timeline.generate_srt.return_value = "1\n00:00:00,000 --> 00:00:05,000\nHello\n"

    # Mock mixer
    mixed_audio = tmp_path / "audio_mixed.wav"
    mixer.mix.side_effect = lambda mix_spec, output_path: output_path.write_bytes(b"mixed")

    # Mock renderer
    final_video = tmp_path / "output_final.mp4"
    renderer.render.side_effect = lambda video_path, audio_path, subtitles, output_path: output_path.write_bytes(b"final")

    config = LocalizationConfig(
        run_transcription=True,
        run_translation=True,
        target_language="vi",
        run_tts=True,
        generate_subtitles=True,
        mix_audio=True,
        render_video=True,
    )
    service = LocalizationService(
        downloader=downloader,
        translate_service=translate_service,
        tts_service=tts_service,
        timeline=timeline,
        mixer=mixer,
        renderer=renderer,
        config=config,
    )

    with patch("universal_video_ai.orchestrator.service.create_audio_pipeline") as mock_pipeline_factory:
        audio_result_obj = MagicMock()
        audio_result_obj.audio_path = tmp_path / "audio.wav"
        audio_result_obj.duration = 10.0

        audio_result = MagicMock(spec=AudioPipelineResult)
        audio_result.transcript = "Hello world"
        audio_result.audio_result = audio_result_obj

        mock_pipeline = MagicMock()
        mock_pipeline.process.return_value = audio_result
        mock_pipeline_factory.return_value = mock_pipeline

        result = service.localize(str(tmp_path), tmp_path)

        assert result.final_video_path is not None
        assert result.final_video_path.exists()
        renderer.render.assert_called_once()


def test_localization_service_download_failure(tmp_path: Path):
    """Test failure handling when download fails."""
    downloader = MagicMock(spec=DownloadService)
    download_result = MagicMock(spec=DownloadResult)
    download_result.success = False
    downloader.download.return_value = download_result

    config = LocalizationConfig(render_video=False)
    service = LocalizationService(downloader=downloader, config=config)

    with pytest.raises(ValueError, match="Download failed"):
        service.localize("http://invalid.url", tmp_path)


def test_localization_service_no_transcript(tmp_path: Path):
    """Test that service skips translation/TTS/subtitles when no transcript."""
    downloader = MagicMock(spec=DownloadService)
    translate_service = MagicMock(spec=TranslateService)
    tts_service = MagicMock(spec=TTSService)

    download_result = MagicMock(spec=DownloadResult)
    download_result.success = True
    download_result.video_path = tmp_path / "video.mp4"
    downloader.download.return_value = download_result

    config = LocalizationConfig(
        run_translation=True,
        run_tts=True,
        generate_subtitles=True,
        render_video=False,
    )
    service = LocalizationService(
        downloader=downloader,
        translate_service=translate_service,
        tts_service=tts_service,
        config=config,
    )

    with patch("universal_video_ai.orchestrator.service.create_audio_pipeline") as mock_pipeline_factory:
        audio_result_obj = MagicMock()
        audio_result_obj.audio_path = tmp_path / "audio.wav"
        audio_result_obj.duration = 10.0

        audio_result = MagicMock(spec=AudioPipelineResult)
        audio_result.transcript = None  # No transcript
        audio_result.audio_result = audio_result_obj

        mock_pipeline = MagicMock()
        mock_pipeline.process.return_value = audio_result
        mock_pipeline_factory.return_value = mock_pipeline

        result = service.localize(str(tmp_path), tmp_path)

        # Translation, TTS, and subtitles should not be called
        translate_service.translate.assert_not_called()
        tts_service.synthesize.assert_not_called()
        assert result.translated_text is None
        assert result.tts_audio_path is None
        assert result.subtitle_segments is None