# tests/test_orchestrator_service.py
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from universal_video_ai.orchestrator.service import (
    LocalizationService, LocalizationConfig, LocalizationResult, PreparedLocalization,
)
from universal_video_ai.downloader.download_result import DownloadResult
from universal_video_ai.audio.pipeline import AudioPipelineResult, AudioResult
from universal_video_ai.audio.demucs import DemucsOutput
from universal_video_ai.translate.service import TranslateService
from universal_video_ai.tts.service import TTSService
from universal_video_ai.timeline.service import TimelineService, TimelineSegment
from universal_video_ai.mixer.service import MixerService
from universal_video_ai.render.animated_subtitles import SubtitleEffect
from universal_video_ai.render.renderer import Renderer, RenderConfig, AnimatedSubtitleConfig, TextOverlay
from universal_video_ai.render.text_detector import SubtitleOffsetEstimate
from universal_video_ai.downloader.service import DownloadService
from universal_video_ai.segment import TranscriptSegment


@pytest.fixture(autouse=True)
def _disable_global_download_rate_limit():
    """Keep orchestrator unit tests deterministic and independent of Redis."""
    limiter = MagicMock()
    limiter.acquire = AsyncMock()
    async def run_inline(function, *args, **kwargs):
        return function(*args, **kwargs)
    with patch(
        "universal_video_ai.orchestrator.service.get_rate_limiter",
        return_value=limiter,
    ), patch(
        "universal_video_ai.orchestrator.service.asyncio.to_thread",
        side_effect=run_inline,
    ):
        yield


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

        result = asyncio.run(service.localize(str(tmp_path), tmp_path))

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

        result = asyncio.run(service.localize(str(tmp_path), tmp_path))

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
    tts_service.synthesize.side_effect = (
        lambda text, output_path, language, voice=None: output_path.write_bytes(b"tts")
    )

    # Mock subtitles
    # Mock subtitles
    segments = [TimelineSegment(start_time=0.0, end_time=5.0, text="Hello")]
    timeline.align_transcript.return_value = segments
    timeline.generate_srt.return_value = "1\n00:00:00,000 --> 00:00:05,000\nHello\n"
    timeline.generate_ass_karaoke.return_value = (
        "[Script Info]\nScriptType: v4.00+\n[V4+ Styles]\n[Events]\n"
    )
    renderer._get_video_dimensions.return_value = (1080, 1920)
    renderer.config = RenderConfig(
        animated_subtitle_config=AnimatedSubtitleConfig(enabled=True)
    )

    # Mock mixer
    mixed_audio = tmp_path / "audio_mixed.wav"
    mixer.mix.side_effect = lambda mix_spec, output_path: output_path.write_bytes(b"mixed")

    # Mock renderer
    final_video = tmp_path / "output_final.mp4"
    renderer.render.side_effect = (
        lambda video_path, audio_path, subtitles, output_path, **kwargs:
        output_path.write_bytes(b"final")
    )

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

        result = asyncio.run(service.localize(str(tmp_path), tmp_path))

        assert result.final_video_path is not None
        assert result.final_video_path.exists()
        renderer.render.assert_called_once()
        assert renderer.render.call_args.kwargs["subtitles"] is None
        assert renderer.render.call_args.kwargs["subtitle_segments"] == [
            {"text": "Hello", "start": 0.0, "end": 5.0}
        ]


def test_localization_service_uses_ass_for_karaoke_rendering(tmp_path: Path):
    downloader = MagicMock(spec=DownloadService)
    translate_service = MagicMock(spec=TranslateService)
    tts_service = MagicMock(spec=TTSService)
    timeline = MagicMock(spec=TimelineService)
    mixer = MagicMock(spec=MixerService)
    renderer = MagicMock(spec=Renderer)

    download_result = MagicMock(spec=DownloadResult)
    download_result.success = True
    download_result.video_path = tmp_path / "video.mp4"
    downloader.download.return_value = download_result

    translate_service.translate.return_value = "Xin chào thế giới"
    tts_service.synthesize.side_effect = (
        lambda text, output_path, language, voice=None: output_path.write_bytes(b"tts")
    )

    segments = [TimelineSegment(start_time=0.0, end_time=5.0, text="Hello")]
    timeline.align_transcript.return_value = segments
    timeline.generate_ass_karaoke.return_value = (
        "[Script Info]\nScriptType: v4.00+\n[V4+ Styles]\n[Events]\n"
        r"Dialogue: 0,0:00:00.00,0:00:05.00,Default,,0,0,0,,{\kf500}Hello"
    )
    renderer._get_video_dimensions.return_value = (1080, 1920)
    renderer.config = RenderConfig(
        animated_subtitle_config=AnimatedSubtitleConfig(
            enabled=True,
            effect=SubtitleEffect.KARAOKE,
        )
    )

    mixer.mix.side_effect = lambda mix_spec, output_path: output_path.write_bytes(b"mixed")
    renderer.render.side_effect = (
        lambda video_path, audio_path, subtitles, output_path, **kwargs:
        output_path.write_bytes(b"final")
    )

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

        result = asyncio.run(service.localize(str(tmp_path), tmp_path))

        assert result.final_video_path is not None
        renderer.render.assert_called_once()
        assert renderer.render.call_args.kwargs["subtitles"].name == "subtitles.ass"
        assert renderer.render.call_args.kwargs["subtitle_segments"] is None


def test_text_cover_places_ass_captions_in_cover_box_by_default(tmp_path: Path):
    download_result = MagicMock(spec=DownloadResult)
    download_result.video_path = tmp_path / "video.mp4"

    audio_result_obj = MagicMock()
    audio_result_obj.audio_path = tmp_path / "audio.wav"
    audio_result_obj.duration = 2.0

    audio_result = MagicMock(spec=AudioPipelineResult)
    audio_result.audio_result = audio_result_obj
    audio_result.detected_language = "zh"
    audio_result.transcript = "你好"

    timeline = MagicMock(spec=TimelineService)
    timeline.from_segments.return_value = [TimelineSegment(start_time=0.0, end_time=1.0, text="Xin chào")]
    timeline.generate_ass_karaoke.side_effect = ["base ass", "cover ass"]

    service = LocalizationService(
        downloader=MagicMock(spec=DownloadService),
        timeline=timeline,
        config=LocalizationConfig(generate_subtitles=True, enable_text_cover=True),
    )
    service._build_text_overlays = MagicMock(return_value=[
        TextOverlay(start=0.0, end=1.0, x=10, y=20, width=200, height=80, text="Xin chào", font_size=36)
    ])

    prepared = PreparedLocalization(
        download_result=download_result,
        audio_result=audio_result,
        source_segments=[TranscriptSegment(start=0.0, end=1.0, text="你好")],
        translated_segments=[TranscriptSegment(start=0.0, end=1.0, text="Xin chào")],
        tts_segments=[TranscriptSegment(start=0.0, end=1.0, text="Xin chào")],
        translated_text="Xin chào",
        target_language="vi",
        output_dir=tmp_path,
    )

    result = asyncio.run(service._finalize(prepared))

    assert timeline.generate_ass_karaoke.call_count == 2
    assert timeline.generate_ass_karaoke.call_args.kwargs["positions"] == {
        (0.0, 1.0): (110, 60)
    }
    assert timeline.generate_ass_karaoke.call_args.kwargs["font_size"] == 36
    assert result.text_overlays is not None
    assert result.text_overlays[0].text == ""


def test_replace_source_audio_uses_demucs_non_vocal_bed(tmp_path: Path):
    video = tmp_path / "video.mp4"
    source_audio = tmp_path / "source.wav"
    for path in (video, source_audio):
        path.write_bytes(b"x")
    stems = {}
    for name in ("vocals", "drums", "bass", "other"):
        path = tmp_path / f"{name}.wav"
        path.write_bytes(name.encode())
        stems[name] = path

    tts_service = MagicMock(spec=TTSService)
    tts_service.synthesize.side_effect = (
        lambda text, output_path, language, voice=None: output_path.write_bytes(b"tts")
    )
    mixer = MagicMock(spec=MixerService)
    mixer.build_dubbed_track.side_effect = lambda clips, total_duration, output_path: output_path.write_bytes(b"dub")
    mixer.build_source_effects_bed.side_effect = lambda stems, total_duration, output_path, volume: output_path.write_bytes(b"bed")
    mixer.mix_dub_with_source_and_background.side_effect = lambda spec, output_path: output_path.write_bytes(b"mixed")

    service = LocalizationService(
        tts_service=tts_service,
        mixer=mixer,
        config=LocalizationConfig(
            run_tts=True,
            mix_audio=True,
            replace_source_audio=True,
        ),
    )
    audio_result = AudioPipelineResult(
        audio_result=AudioResult(True, source_audio, 5.0, 44100, 2, None, "wav", 1),
        demucs_output=DemucsOutput(
            vocals=stems["vocals"],
            drums=stems["drums"],
            bass=stems["bass"],
            other=stems["other"],
        ),
        transcript="hello",
    )
    prepared = PreparedLocalization(
        download_result=DownloadResult(True, platform=None, original_url="", final_url="", video_path=video),
        audio_result=audio_result,
        source_segments=[],
        translated_segments=[TranscriptSegment(start=0.0, end=1.0, text="Xin chào")],
        tts_segments=[TranscriptSegment(start=0.0, end=1.0, text="Xin chào")],
        translated_text="Xin chào",
        target_language="vi",
        output_dir=tmp_path,
    )

    result = asyncio.run(service._finalize(prepared))

    mixer.build_source_effects_bed.assert_called_once()
    used_stems = mixer.build_source_effects_bed.call_args.args[0]
    assert used_stems == [stems["drums"], stems["bass"], stems["other"]]
    mix_spec = mixer.mix_dub_with_source_and_background.call_args.args[0]
    assert mix_spec.source_audio.name == "source_effects_bed.wav"
    assert result.mixed_audio_path == tmp_path / "audio_safe_mix.wav"


def test_replace_source_audio_without_demucs_does_not_mix_original_voice(tmp_path: Path):
    video = tmp_path / "video.mp4"
    source_audio = tmp_path / "source.wav"
    for path in (video, source_audio):
        path.write_bytes(b"x")

    tts_service = MagicMock(spec=TTSService)
    tts_service.synthesize.side_effect = (
        lambda text, output_path, language, voice=None: output_path.write_bytes(b"tts")
    )
    mixer = MagicMock(spec=MixerService)
    mixer.build_dubbed_track.side_effect = lambda clips, total_duration, output_path: output_path.write_bytes(b"dub")

    service = LocalizationService(
        tts_service=tts_service,
        mixer=mixer,
        config=LocalizationConfig(
            run_tts=True,
            mix_audio=True,
            replace_source_audio=True,
        ),
    )
    audio_result = AudioPipelineResult(
        audio_result=AudioResult(True, source_audio, 5.0, 44100, 2, None, "wav", 1),
        demucs_output=None,
        transcript="hello",
    )
    prepared = PreparedLocalization(
        download_result=DownloadResult(True, platform=None, original_url="", final_url="", video_path=video),
        audio_result=audio_result,
        source_segments=[],
        translated_segments=[TranscriptSegment(start=0.0, end=1.0, text="Xin chào")],
        tts_segments=[TranscriptSegment(start=0.0, end=1.0, text="Xin chào")],
        translated_text="Xin chào",
        target_language="vi",
        output_dir=tmp_path,
    )

    result = asyncio.run(service._finalize(prepared))

    mixer.mix.assert_not_called()
    mixer.mix_dub_with_source_and_background.assert_not_called()
    assert result.mixed_audio_path == tmp_path / "tts_audio.wav"


def test_localization_service_download_failure(tmp_path: Path):
    """Test failure handling when download fails."""
    downloader = MagicMock(spec=DownloadService)
    download_result = MagicMock(spec=DownloadResult)
    download_result.success = False
    downloader.download.return_value = download_result

    config = LocalizationConfig(render_video=False)
    service = LocalizationService(downloader=downloader, config=config)

    with pytest.raises(ValueError, match="Download failed"):
        asyncio.run(service.localize("http://invalid.url", tmp_path))


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

        result = asyncio.run(service.localize(str(tmp_path), tmp_path))

        # Translation, TTS, and subtitles should not be called
        translate_service.translate.assert_not_called()
        tts_service.synthesize.assert_not_called()
        assert result.translated_text is None
        assert result.tts_audio_path is None
        assert result.subtitle_segments is None


def test_align_source_segments_to_burned_subtitle_offset(tmp_path: Path):
    detector = MagicMock()
    detector.estimate_subtitle_time_offset.return_value = SubtitleOffsetEstimate(
        offset=7.75,
        confidence=0.91,
        matches=3,
    )
    service = LocalizationService(
        text_detector=detector,
        config=LocalizationConfig(enable_text_cover=True),
    )

    aligned = service._align_source_segments_to_burned_subtitles(
        video_path=tmp_path / "video.mp4",
        source_segments=[
            TranscriptSegment(start=34.24, end=36.32, text="我正好想出去透透气"),
            TranscriptSegment(start=119.5, end=122.5, text="结尾"),
        ],
        detected_language="zh",
        audio_duration=122.0,
    )

    assert aligned[0] == TranscriptSegment(start=41.99, end=44.07, text="我正好想出去透透气")
    assert aligned[1] == TranscriptSegment(start=122.0, end=122.0, text="结尾")
    detector.estimate_subtitle_time_offset.assert_called_once()


def test_align_source_segments_keeps_asr_timing_without_confident_offset(tmp_path: Path):
    detector = MagicMock()
    detector.estimate_subtitle_time_offset.return_value = None
    service = LocalizationService(
        text_detector=detector,
        config=LocalizationConfig(enable_text_cover=True),
    )
    segments = [TranscriptSegment(start=1.0, end=2.0, text="hello")]

    aligned = service._align_source_segments_to_burned_subtitles(
        video_path=tmp_path / "video.mp4",
        source_segments=segments,
        detected_language="en",
        audio_duration=10.0,
    )

    assert aligned is segments


def test_subtitle_alignment_passes_small_offset_threshold_to_detector(tmp_path: Path):
    detector = MagicMock()
    detector.estimate_subtitle_time_offset.return_value = None
    service = LocalizationService(
        text_detector=detector,
        config=LocalizationConfig(enable_text_cover=True, min_subtitle_alignment_offset=0.05),
    )

    service._align_source_segments_to_burned_subtitles(
        video_path=tmp_path / "video.mp4",
        source_segments=[TranscriptSegment(start=0.0, end=1.0, text="柳夫人")],
        detected_language="zh",
        audio_duration=10.0,
    )

    assert detector.estimate_subtitle_time_offset.call_args.kwargs["min_offset"] == 0.05


def test_align_source_segments_applies_offset_only_after_detected_cluster(tmp_path: Path):
    detector = MagicMock()
    detector.estimate_subtitle_time_offset.return_value = SubtitleOffsetEstimate(
        offset=8.0,
        confidence=0.95,
        matches=5,
        apply_after=30.0,
    )
    service = LocalizationService(
        text_detector=detector,
        config=LocalizationConfig(enable_text_cover=True),
    )

    aligned = service._align_source_segments_to_burned_subtitles(
        video_path=tmp_path / "video.mp4",
        source_segments=[
            TranscriptSegment(start=0.0, end=1.6, text="我不喜欢妈妈"),
            TranscriptSegment(start=30.0, end=30.96, text="李女士"),
            TranscriptSegment(start=34.24, end=36.32, text="我正好想出去透透气"),
        ],
        detected_language="zh",
        audio_duration=122.0,
    )

    assert aligned[0] == TranscriptSegment(start=0.0, end=1.6, text="我不喜欢妈妈")
    assert aligned[1] == TranscriptSegment(start=38.0, end=38.96, text="李女士")
    assert aligned[2] == TranscriptSegment(start=42.24, end=44.32, text="我正好想出去透透气")


def test_burned_subtitle_alignment_does_not_shift_tts_audio_clock(tmp_path: Path):
    downloader = MagicMock(spec=DownloadService)
    download_result = MagicMock(spec=DownloadResult)
    download_result.success = True
    download_result.video_path = tmp_path / "video.mp4"
    downloader.download.return_value = download_result

    translate_service = MagicMock(spec=TranslateService)
    translate_service.translate_segments.return_value = [
        TranscriptSegment(start=0.0, end=1.0, text="Con không thích mẹ")
    ]

    tts_service = MagicMock(spec=TTSService)
    tts_service.synthesize.side_effect = (
        lambda text, output_path, language, voice=None: output_path.write_bytes(b"tts")
    )

    mixer = MagicMock(spec=MixerService)
    mixer.build_dubbed_track.side_effect = lambda clips, total_duration, output_path: output_path.write_bytes(b"dub")

    detector = MagicMock()
    detector.estimate_subtitle_time_offset.return_value = SubtitleOffsetEstimate(
        offset=8.0,
        confidence=0.95,
        matches=4,
    )

    service = LocalizationService(
        downloader=downloader,
        translate_service=translate_service,
        tts_service=tts_service,
        mixer=mixer,
        text_detector=detector,
        config=LocalizationConfig(
            run_transcription=True,
            run_translation=True,
            target_language="vi",
            run_tts=True,
            enable_text_cover=True,
        ),
    )

    with patch("universal_video_ai.orchestrator.service.create_audio_pipeline") as mock_pipeline_factory:
        audio_result_obj = MagicMock()
        audio_result_obj.audio_path = tmp_path / "audio.wav"
        audio_result_obj.duration = 20.0

        audio_result = MagicMock(spec=AudioPipelineResult)
        audio_result.transcript = "我不喜欢妈妈"
        audio_result.detected_language = "zh"
        audio_result.audio_result = audio_result_obj
        audio_result.segments = [TranscriptSegment(start=0.0, end=1.0, text="我不喜欢妈妈")]

        mock_pipeline = MagicMock()
        mock_pipeline.process.return_value = audio_result
        mock_pipeline_factory.return_value = mock_pipeline

        result = asyncio.run(service.localize(str(tmp_path), tmp_path))

    tts_clips = mixer.build_dubbed_track.call_args.args[0]
    assert tts_clips[0].start == 0.0
    assert tts_clips[0].end == 1.0
    assert result.tts_segments == [TranscriptSegment(start=0.0, end=1.0, text="Con không thích mẹ")]
    assert result.translated_segments == [TranscriptSegment(start=8.0, end=9.0, text="Con không thích mẹ")]
    assert result.source_segments == [TranscriptSegment(start=8.0, end=9.0, text="我不喜欢妈妈")]


def test_small_global_subtitle_offset_also_syncs_tts_clock(tmp_path: Path):
    downloader = MagicMock(spec=DownloadService)
    download_result = MagicMock(spec=DownloadResult)
    download_result.success = True
    download_result.video_path = tmp_path / "video.mp4"
    downloader.download.return_value = download_result

    translate_service = MagicMock(spec=TranslateService)
    translate_service.translate_segments.return_value = [
        TranscriptSegment(start=0.0, end=2.0, text="Mật danh của ngươi là Độc Xà.")
    ]

    tts_service = MagicMock(spec=TTSService)
    tts_service.synthesize.side_effect = (
        lambda text, output_path, language, voice=None: output_path.write_bytes(b"tts")
    )

    mixer = MagicMock(spec=MixerService)
    mixer.build_dubbed_track.side_effect = lambda clips, total_duration, output_path: output_path.write_bytes(b"dub")

    detector = MagicMock()
    detector.estimate_subtitle_time_offset.return_value = SubtitleOffsetEstimate(
        offset=0.3,
        confidence=0.9,
        matches=3,
    )

    service = LocalizationService(
        downloader=downloader,
        translate_service=translate_service,
        tts_service=tts_service,
        mixer=mixer,
        text_detector=detector,
        config=LocalizationConfig(
            run_transcription=True,
            run_translation=True,
            target_language="vi",
            run_tts=True,
            enable_text_cover=True,
        ),
    )

    with patch("universal_video_ai.orchestrator.service.create_audio_pipeline") as mock_pipeline_factory:
        audio_result_obj = MagicMock()
        audio_result_obj.audio_path = tmp_path / "audio.wav"
        audio_result_obj.duration = 20.0

        audio_result = MagicMock(spec=AudioPipelineResult)
        audio_result.transcript = "你的代号是毒蛇"
        audio_result.detected_language = "zh"
        audio_result.audio_result = audio_result_obj
        audio_result.segments = [TranscriptSegment(start=0.0, end=2.0, text="你的代号是毒蛇")]

        mock_pipeline = MagicMock()
        mock_pipeline.process.return_value = audio_result
        mock_pipeline_factory.return_value = mock_pipeline

        result = asyncio.run(service.localize(str(tmp_path), tmp_path))

    tts_clips = mixer.build_dubbed_track.call_args.args[0]
    assert tts_clips[0].start == pytest.approx(0.3)
    assert tts_clips[0].end == pytest.approx(2.3)
    assert result.tts_segments == [
        TranscriptSegment(start=0.3, end=2.3, text="Mật danh của ngươi là Độc Xà.")
    ]
    assert result.translated_segments == [
        TranscriptSegment(start=0.3, end=2.3, text="Mật danh của ngươi là Độc Xà.")
    ]


def test_global_subtitle_offset_is_applied_once(tmp_path: Path):
    downloader = MagicMock(spec=DownloadService)
    download_result = MagicMock(spec=DownloadResult)
    download_result.success = True
    download_result.video_path = tmp_path / "video.mp4"
    downloader.download.return_value = download_result

    translate_service = MagicMock(spec=TranslateService)
    translate_service.translate_segments.return_value = [
        TranscriptSegment(start=0.0, end=1.0, text="Xin chào.")
    ]

    tts_service = MagicMock(spec=TTSService)
    tts_service.synthesize.side_effect = (
        lambda text, output_path, language, voice=None: output_path.write_bytes(b"tts")
    )

    mixer = MagicMock(spec=MixerService)
    mixer.build_dubbed_track.side_effect = lambda clips, total_duration, output_path: output_path.write_bytes(b"dub")

    service = LocalizationService(
        downloader=downloader,
        translate_service=translate_service,
        tts_service=tts_service,
        mixer=mixer,
        config=LocalizationConfig(
            run_transcription=True,
            run_translation=True,
            target_language="vi",
            run_tts=True,
            global_subtitle_offset=0.2,
        ),
    )

    with patch("universal_video_ai.orchestrator.service.create_audio_pipeline") as mock_pipeline_factory:
        audio_result_obj = MagicMock()
        audio_result_obj.audio_path = tmp_path / "audio.wav"
        audio_result_obj.duration = 10.0

        audio_result = MagicMock(spec=AudioPipelineResult)
        audio_result.transcript = "hello"
        audio_result.detected_language = "en"
        audio_result.audio_result = audio_result_obj
        audio_result.segments = [TranscriptSegment(start=0.0, end=1.0, text="hello")]

        mock_pipeline = MagicMock()
        mock_pipeline.process.return_value = audio_result
        mock_pipeline_factory.return_value = mock_pipeline

        result = asyncio.run(service.localize(str(tmp_path), tmp_path))

    assert result.source_segments == [TranscriptSegment(start=0.2, end=1.2, text="hello")]
    assert result.translated_segments == [TranscriptSegment(start=0.2, end=1.2, text="Xin chào.")]


def test_small_detected_offset_moves_first_subtitle_and_tts_segment(tmp_path: Path):
    downloader = MagicMock(spec=DownloadService)
    download_result = MagicMock(spec=DownloadResult)
    download_result.success = True
    download_result.video_path = tmp_path / "video.mp4"
    downloader.download.return_value = download_result

    translate_service = MagicMock(spec=TranslateService)
    translate_service.translate_segments.return_value = [
        TranscriptSegment(start=0.0, end=1.0, text="Liễu phu nhân.")
    ]

    tts_service = MagicMock(spec=TTSService)
    tts_service.synthesize.side_effect = (
        lambda text, output_path, language, voice=None: output_path.write_bytes(b"tts")
    )

    mixer = MagicMock(spec=MixerService)
    mixer.build_dubbed_track.side_effect = lambda clips, total_duration, output_path: output_path.write_bytes(b"dub")

    detector = MagicMock()
    detector.estimate_subtitle_time_offset.return_value = SubtitleOffsetEstimate(
        offset=0.2,
        confidence=0.44,
        matches=8,
    )

    service = LocalizationService(
        downloader=downloader,
        translate_service=translate_service,
        tts_service=tts_service,
        mixer=mixer,
        text_detector=detector,
        config=LocalizationConfig(
            run_transcription=True,
            run_translation=True,
            target_language="vi",
            run_tts=True,
            enable_text_cover=True,
        ),
    )

    with patch("universal_video_ai.orchestrator.service.create_audio_pipeline") as mock_pipeline_factory:
        audio_result_obj = MagicMock()
        audio_result_obj.audio_path = tmp_path / "audio.wav"
        audio_result_obj.duration = 10.0

        audio_result = MagicMock(spec=AudioPipelineResult)
        audio_result.transcript = "柳夫人"
        audio_result.detected_language = "zh"
        audio_result.audio_result = audio_result_obj
        audio_result.segments = [TranscriptSegment(start=0.0, end=1.0, text="柳夫人")]

        mock_pipeline = MagicMock()
        mock_pipeline.process.return_value = audio_result
        mock_pipeline_factory.return_value = mock_pipeline

        result = asyncio.run(service.localize(str(tmp_path), tmp_path))

    tts_clips = mixer.build_dubbed_track.call_args.args[0]
    assert tts_clips[0].start == pytest.approx(0.2)
    assert tts_clips[0].end == pytest.approx(1.2)
    assert result.translated_segments == [
        TranscriptSegment(start=0.2, end=1.2, text="Liễu phu nhân.")
    ]
    assert result.source_segments == [
        TranscriptSegment(start=0.2, end=1.2, text="柳夫人")
    ]


def test_default_visual_timing_padding_does_not_pull_subtitle_start_back() -> None:
    service = LocalizationService(config=LocalizationConfig())
    service._last_subtitle_alignment_estimate = SubtitleOffsetEstimate(
        offset=0.2,
        confidence=0.8,
        matches=8,
    )

    segments = [TranscriptSegment(start=0.2, end=1.2, text="Liễu phu nhân.")]

    padded = service._pad_visual_segments(
        segments,
        audio_duration=10.0,
        padding=service._visual_timing_padding_for_current_video(),
    )

    assert padded == segments


def test_visual_subtitle_padding_does_not_overlap_or_move_tts_clock():
    segments = [
        TranscriptSegment(start=1.0, end=2.0, text="Một"),
        TranscriptSegment(start=2.05, end=3.0, text="Hai"),
    ]

    padded = LocalizationService._pad_visual_segments(
        segments,
        audio_duration=5.0,
        padding=0.08,
    )

    assert padded[0].start == pytest.approx(0.92)
    assert padded[0].end <= padded[1].start
    assert padded[1].end == pytest.approx(3.08)
    assert segments[0].start == 1.0


def test_visual_timing_padding_only_applies_after_detected_offset():
    service = LocalizationService(config=LocalizationConfig())

    assert service._visual_timing_padding_for_current_video() == 0.0

    service._last_subtitle_alignment_estimate = SubtitleOffsetEstimate(
        offset=0.1,
        confidence=0.8,
        matches=2,
    )

    assert service._visual_timing_padding_for_current_video() == 0.0

    service = LocalizationService(config=LocalizationConfig(visual_subtitle_timing_padding=0.08))
    service._last_subtitle_alignment_estimate = SubtitleOffsetEstimate(
        offset=0.1,
        confidence=0.8,
        matches=2,
    )

    assert service._visual_timing_padding_for_current_video() == pytest.approx(0.08)


def test_static_text_watermark_boxes_are_added_to_render_config(tmp_path: Path):
    detector = MagicMock()
    detector.detect_persistent_text_regions.return_value = (
        (0.0, 0.02, 0.25, 0.12),
        (0.14, 0.18, 0.34, 0.31),
    )
    service = LocalizationService(
        text_detector=detector,
        config=LocalizationConfig(enable_text_cover=True, auto_blur_static_text=True),
    )

    boxes = service._detect_static_text_watermark_boxes(
        video_path=tmp_path / "video.mp4",
        detected_language="zh",
        duration=120.0,
    )

    assert boxes == (
        (0.0, 0.02, 0.25, 0.12),
        (0.14, 0.18, 0.34, 0.31),
    )
    detector.detect_persistent_text_regions.assert_called_once()
