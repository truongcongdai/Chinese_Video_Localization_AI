"""
Tests for adapters (TTS, subtitle, timeline).
"""
import pytest
from pathlib import Path
from universal_video_ai.content_os.adapters import (
    TTSAdapter, SubtitleAdapter, TimelineAdapter,
)


@pytest.fixture
def temp_dir(tmp_path):
    """Temporary directory for outputs."""
    return tmp_path


@pytest.fixture
def script_segments():
    """Sample script segments."""
    return [
        {
            "text": "Hook text",
            "narration": "Hook text",
            "start_second": 0.0,
            "end_second": 6.0,
        },
        {
            "text": "Main content",
            "narration": "Main content",
            "start_second": 6.0,
            "end_second": 45.0,
        },
    ]


class TestTTSAdapter:
    """Test TTS adapter."""

    def test_tts_chunk_helpers_are_on_tts_adapter(self):
        """Regression: helper methods must live on TTSAdapter, not SubtitleAdapter."""
        adapter = TTSAdapter()
        text = "Câu một khá dài để kiểm tra chia nhỏ. Câu hai cũng đủ dài để vượt giới hạn."
        chunks = adapter._split_tts_chunks(text, max_chars=36)
        assert len(chunks) >= 2
        assert adapter._estimate_speech_duration(text) >= 5.0
    
    def test_generate_audio(self, temp_dir, script_segments):
        """Test generating audio from text."""
        adapter = TTSAdapter()
        
        text = "Hook text Main content"
        
        audio_path = adapter.generate_audio(
            text=text,
            language="vi",
            voice_id="voice_1",
            output_dir=temp_dir,
        )
        
        assert isinstance(audio_path, Path)
        assert audio_path.exists()


class TestSubtitleAdapter:
    """Test subtitle adapter."""
    
    def test_generate_subtitles(self, temp_dir, script_segments):
        """Test generating subtitles from script segments."""
        adapter = SubtitleAdapter()
        
        subtitle_path = adapter.generate_subtitles(
            segments=script_segments,
            duration=45.0,
            output_dir=temp_dir,
        )
        
        assert isinstance(subtitle_path, Path)
        assert subtitle_path.exists()
        assert subtitle_path.suffix == ".ass"
        content = subtitle_path.read_text(encoding="utf-8")
        assert "\\kf" in content
        assert "Dialogue:" in content

    def test_generate_subtitles_scales_script_timing_to_audio_duration(self, temp_dir):
        """Subtitles should match actual TTS audio duration, not fixed script duration."""
        adapter = SubtitleAdapter()
        subtitle_path = adapter.generate_subtitles(
            segments=[
                {"narration": "First sentence", "start_second": 0.0, "end_second": 10.0},
                {"narration": "Second sentence", "start_second": 10.0, "end_second": 20.0},
            ],
            duration=10.0,
            output_dir=temp_dir,
        )
        content = subtitle_path.read_text(encoding="utf-8")
        assert "0:00:10.00" in content
        assert "0:00:20.00" not in content

    def test_generate_subtitles_prefers_narration_over_short_caption(self, temp_dir):
        """Burned-in subtitles should match the narration source used by TTS."""
        adapter = SubtitleAdapter()
        subtitle_path = adapter.generate_subtitles(
            segments=[
                {
                    "narration": "Đây là câu narration đầy đủ mà voice sẽ đọc.",
                    "text": "Caption ngắn",
                    "subtitle_text": "Caption ngắn",
                    "start_second": 0.0,
                    "end_second": 5.0,
                },
            ],
            duration=5.0,
            output_dir=temp_dir,
        )
        content = subtitle_path.read_text(encoding="utf-8")
        assert "narration" in content
        assert "đầy" in content
        assert "đủ" in content
        assert "Caption ngắn" not in content


class TestTimelineAdapter:
    """Test timeline adapter."""
    
    def test_build_timeline(self, script_segments):
        """Test building timeline from components."""
        adapter = TimelineAdapter()
        
        script = {
            "segments": script_segments,
            "language": "vi",
        }
        
        voice_manifest = {
            "audio_path": "/audio/voice.wav",
            "duration_seconds": 45.0,
        }
        
        subtitle_manifest = {
            "subtitle_path": "/subtitles/subs.srt",
        }
        
        assets = {
            "assets": [],
        }
        
        timeline = adapter.build_timeline(
            script=script,
            voice_manifest=voice_manifest,
            subtitle_manifest=subtitle_manifest,
            assets=assets,
            target_platform="youtube_shorts",
            target_duration=45.0,
        )
        
        assert isinstance(timeline, dict)
        assert timeline["duration_seconds"] == 45.0
        assert timeline["resolution"] == "1080x1920"
        assert "audio_tracks" in timeline
        assert "subtitle_tracks" in timeline
        assert "segments" in timeline

    def test_build_timeline_uses_voice_duration_and_scales_segments(self, script_segments):
        adapter = TimelineAdapter()
        timeline = adapter.build_timeline(
            script={"segments": script_segments, "language": "vi"},
            voice_manifest={"audio_path": "/audio/voice.wav", "duration_seconds": 30.0},
            subtitle_manifest={"subtitle_path": "/subtitles/subs.ass"},
            assets={"assets": []},
            target_platform="youtube_shorts",
            target_duration=45.0,
        )
        assert timeline["duration_seconds"] == 30.0
        assert timeline["total_duration"] == 30.0
        assert timeline["segments"][-1]["end"] == 30.0
