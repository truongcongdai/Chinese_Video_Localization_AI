"""Test subtitle offset detection in production scenario."""
import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from universal_video_ai.orchestrator.service import LocalizationService, LocalizationConfig
from universal_video_ai.segment import TranscriptSegment
from universal_video_ai.downloader.download_result import DownloadResult
from universal_video_ai.audio.pipeline import AudioPipelineResult
from universal_video_ai.render.text_detector import (
    OnScreenTextDetector, SubtitleTimingWindow, SubtitleOffsetEstimate
)


def test_subtitles_use_source_segments_when_translated_segments_missing(tmp_path: Path):
    """
    Test that visual_source_segments are used for subtitle generation
    when visual_translated_segments is None.
    
    This simulates: translation disabled, OCR detected 0.3s offset
    Expected: subtitles start at 0.3s, not 0.0s
    """
    downloader = MagicMock()
    download_result = MagicMock(spec=DownloadResult)
    download_result.success = True
    download_result.video_path = tmp_path / "video.mp4"
    downloader.download.return_value = download_result

    translate_service = None  # No translation
    
    tts_service = MagicMock()
    tts_service.synthesize.side_effect = (
        lambda text, output_path, language, voice=None: output_path.write_bytes(b"tts")
    )

    mixer = MagicMock()
    mixer.build_dubbed_track.side_effect = lambda clips, total_duration, output_path: output_path.write_bytes(b"dub")

    detector = OnScreenTextDetector()
    detector.detect_subtitle_windows_for_segments = MagicMock(return_value=[
        SubtitleTimingWindow(start=0.3, end=1.3, confidence=0.9)
    ])

    service = LocalizationService(
        downloader=downloader,
        translate_service=translate_service,
        tts_service=tts_service,
        mixer=mixer,
        text_detector=detector,
        config=LocalizationConfig(
            run_transcription=True,
            run_translation=False,
            target_language="vi",
            run_tts=False,
            generate_subtitles=True,
            enable_text_cover=True,
        ),
    )

    with patch("universal_video_ai.orchestrator.service.create_audio_pipeline") as mock_pipeline_factory:
        audio_result_obj = MagicMock()
        audio_result_obj.audio_path = tmp_path / "audio.wav"
        audio_result_obj.duration = 5.0

        audio_result = MagicMock(spec=AudioPipelineResult)
        audio_result.transcript = "Test subtitle"
        audio_result.detected_language = "en"
        audio_result.audio_result = audio_result_obj
        audio_result.segments = [TranscriptSegment(start=0.0, end=1.0, text="Test subtitle")]

        mock_pipeline = MagicMock()
        mock_pipeline.process.return_value = audio_result
        mock_pipeline_factory.return_value = mock_pipeline

        result = asyncio.run(service.localize(str(tmp_path), tmp_path))

    # Should use source segments with 0.3s offset
    assert result.subtitle_segments is not None
    assert len(result.subtitle_segments) > 0
    first_start = result.subtitle_segments[0].start_time
    print(f"First subtitle starts at: {first_start}s")
    assert first_start == pytest.approx(0.3, abs=0.05), \
        f"Expected subtitle at 0.3s (source offset), got {first_start}s"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
