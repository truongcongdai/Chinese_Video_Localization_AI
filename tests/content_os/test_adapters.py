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
        },
        {
            "text": "Main content",
            "narration": "Main content",
        },
    ]


class TestTTSAdapter:
    """Test TTS adapter."""
    
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
        assert subtitle_path.suffix == ".srt"


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
