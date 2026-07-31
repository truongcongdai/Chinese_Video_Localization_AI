"""
Tests for adapters (TTS, subtitle, timeline).
"""
import pytest
from universal_video_ai.content_os.adapters import (
    TTSAdapter, SubtitleAdapter, TimelineAdapter,
    TTSSegment, SubtitleSegment, TimelineEvent, Timeline
)


@pytest.fixture
def temp_db(tmp_path):
    """Temporary database path."""
    return str(tmp_path / "test.db")


@pytest.fixture
def repo(temp_db):
    """Repository instance with initialized schema."""
    from universal_video_ai.web.store import Store
    Store(db_path=temp_db)
    from universal_video_ai.content_os.repository import ContentOSRepository
    return ContentOSRepository(temp_db)


@pytest.fixture
def script_segments():
    """Sample script segments."""
    return [
        {
            "segment_id": "seg1",
            "start_second": 0.0,
            "end_second": 3.0,
            "narration": "Hook text",
            "subtitle_text": "Hook subtitle",
            "visual_instruction": "Visual 1",
        },
        {
            "segment_id": "seg2",
            "start_second": 3.0,
            "end_second": 45.0,
            "narration": "Main content",
            "subtitle_text": "Main subtitle",
            "visual_instruction": "Visual 2",
        },
    ]


class TestTTSAdapter:
    """Test TTS adapter."""
    
    def test_generate_audio(self, repo, script_segments):
        """Test generating audio from script segments."""
        adapter = TTSAdapter(repo)
        
        segments = adapter.generate_audio(
            run_id=1,
            user_id=1,
            script_segments=script_segments,
            voice_id="voice_1",
            target_language="vi",
        )
        
        assert len(segments) == 2
        assert segments[0].segment_id == "tts_0"
        assert segments[0].text == "Hook text"
        assert segments[0].voice_id == "voice_1"


class TestSubtitleAdapter:
    """Test subtitle adapter."""
    
    def test_generate_subtitles(self, repo, script_segments):
        """Test generating subtitles from script segments."""
        adapter = SubtitleAdapter(repo)
        
        segments = adapter.generate_subtitles(
            run_id=1,
            user_id=1,
            script_segments=script_segments,
            target_language="vi",
            subtitle_style_id="style_1",
        )
        
        assert len(segments) == 2
        assert segments[0].segment_id == "sub_0"
        assert segments[0].text == "Hook subtitle"
        assert segments[0].language == "vi"


class TestTimelineAdapter:
    """Test timeline adapter."""
    
    def test_build_timeline(self, repo, script_segments):
        """Test building timeline from components."""
        adapter = TimelineAdapter(repo)
        
        # Create TTS segments
        tts_segments = [
            TTSSegment(
                segment_id="tts_0",
                text="Hook text",
                start_time=0.0,
                end_time=3.0,
                audio_path="/audio/1_seg_0.wav",
                voice_id="voice_1",
                duration=3.0,
            ),
            TTSSegment(
                segment_id="tts_1",
                text="Main content",
                start_time=3.0,
                end_time=45.0,
                audio_path="/audio/1_seg_1.wav",
                voice_id="voice_1",
                duration=42.0,
            ),
        ]
        
        # Create subtitle segments
        subtitle_segments = [
            SubtitleSegment(
                segment_id="sub_0",
                text="Hook subtitle",
                start_time=0.0,
                end_time=3.0,
                language="vi",
            ),
            SubtitleSegment(
                segment_id="sub_1",
                text="Main subtitle",
                start_time=3.0,
                end_time=45.0,
                language="vi",
            ),
        ]
        
        # Create storyboard scenes
        storyboard_scenes = [
            {
                "scene_id": "scene_1",
                "start_second": 0.0,
                "end_second": 3.0,
                "visual_instruction": "Visual 1",
                "camera_angle": "front",
                "transition": "cut",
            },
            {
                "scene_id": "scene_2",
                "start_second": 3.0,
                "end_second": 45.0,
                "visual_instruction": "Visual 2",
                "camera_angle": "front",
                "transition": "cut",
            },
        ]
        
        timeline = adapter.build_timeline(
            run_id=1,
            user_id=1,
            storyboard_scenes=storyboard_scenes,
            tts_segments=tts_segments,
            subtitle_segments=subtitle_segments,
            asset_manifest={},
        )
        
        assert timeline.run_id == 1
        assert timeline.user_id == 1
        assert len(timeline.events) == 6  # 2 audio + 2 video + 2 subtitle
        assert timeline.total_duration == 45.0
