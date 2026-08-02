"""
Tests for storyboard system.
"""
import pytest
from universal_video_ai.content_os.storyboard import StoryboardManager, Storyboard, StoryboardScene, StoryboardStatus


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
def manager(repo):
    """Storyboard manager instance."""
    return StoryboardManager(repo)


@pytest.fixture
def sample_script():
    """Sample script data."""
    return {
        "script_id": "script_1",
        "title_options": ["Title 1", "Title 2"],
        "hook": "Hook text",
        "narration_text": "Full narration",
        "segments": [
            {
                "segment_id": "seg1",
                "start_second": 0.0,
                "end_second": 3.0,
                "narration": "Narration 1",
                "subtitle_text": "Subtitle 1",
                "visual_instruction": "Visual 1",
            },
            {
                "segment_id": "seg2",
                "start_second": 3.0,
                "end_second": 45.0,
                "narration": "Narration 2",
                "subtitle_text": "Subtitle 2",
                "visual_instruction": "Visual 2",
            },
        ],
        "estimated_duration_seconds": 45.0,
    }


class TestStoryboardManager:
    """Test storyboard manager."""
    
    def test_create_from_script(self, manager, repo, sample_script):
        """Test creating storyboard from script."""
        project = repo.create_project(
            user_id=1,
            channel_id=None,
            channel_name="Test Channel",
            mode="ai_video",
            topic="Test",
            objective="Test objective",
            target_platform="youtube_shorts",
            target_duration_seconds=45,
            target_language="vi",
            content_style="trend_decode",
            visual_style="modern_documentary",
            voice_id="",
            subtitle_style_id="",
            background_music_enabled=True,
            user_instructions="",
        )
        run = repo.create_run(project.id, user_id=1)
        
        storyboard = manager.create_from_script(run_id=run.id, user_id=1, script=sample_script)
        
        assert storyboard.run_id == run.id
        assert storyboard.user_id == 1
        assert storyboard.version == 1
        assert storyboard.status == StoryboardStatus.DRAFT
        assert len(storyboard.scenes) == 2
        assert storyboard.total_duration == 45.0
        assert storyboard.scenes[0].visual_instruction != "Visual 1"
        assert "Vertical 9:16" in storyboard.scenes[0].visual_instruction
    
    def test_update_scene(self, manager, repo, sample_script):
        """Test updating a scene."""
        project = repo.create_project(
            user_id=1,
            channel_id=None,
            channel_name="Test Channel",
            mode="ai_video",
            topic="Test",
            objective="Test objective",
            target_platform="youtube_shorts",
            target_duration_seconds=45,
            target_language="vi",
            content_style="trend_decode",
            visual_style="modern_documentary",
            voice_id="",
            subtitle_style_id="",
            background_music_enabled=True,
            user_instructions="",
        )
        run = repo.create_run(project.id, user_id=1)
        
        storyboard = manager.create_from_script(run_id=run.id, user_id=1, script=sample_script)
        
        updated = manager.update_scene(
            run_id=run.id, user_id=1, scene_id="scene_1", updates={"visual_instruction": "New visual"}
        )
        
        assert updated.version == 2
        assert updated.scenes[0].visual_instruction == "New visual"
    
    def test_add_scene(self, manager, repo, sample_script):
        """Test adding a scene."""
        project = repo.create_project(
            user_id=1,
            channel_id=None,
            channel_name="Test Channel",
            mode="ai_video",
            topic="Test",
            objective="Test objective",
            target_platform="youtube_shorts",
            target_duration_seconds=45,
            target_language="vi",
            content_style="trend_decode",
            visual_style="modern_documentary",
            voice_id="",
            subtitle_style_id="",
            background_music_enabled=True,
            user_instructions="",
        )
        run = repo.create_run(project.id, user_id=1)
        
        storyboard = manager.create_from_script(run_id=run.id, user_id=1, script=sample_script)
        
        new_scene = StoryboardScene(
            scene_id="scene_3",
            order=3,
            start_second=45.0,
            end_second=50.0,
            visual_instruction="Visual 3",
            subtitle_text="Subtitle 3",
            narration_text="Narration 3",
        )
        
        updated = manager.add_scene(run_id=run.id, user_id=1, scene=new_scene)
        
        assert len(updated.scenes) == 3
        assert updated.version == 2
    
    def test_delete_scene(self, manager, repo, sample_script):
        """Test deleting a scene."""
        project = repo.create_project(
            user_id=1,
            channel_id=None,
            channel_name="Test Channel",
            mode="ai_video",
            topic="Test",
            objective="Test objective",
            target_platform="youtube_shorts",
            target_duration_seconds=45,
            target_language="vi",
            content_style="trend_decode",
            visual_style="modern_documentary",
            voice_id="",
            subtitle_style_id="",
            background_music_enabled=True,
            user_instructions="",
        )
        run = repo.create_run(project.id, user_id=1)
        
        storyboard = manager.create_from_script(run_id=run.id, user_id=1, script=sample_script)
        
        updated = manager.delete_scene(run_id=run.id, user_id=1, scene_id="scene_1")
        
        assert len(updated.scenes) == 1
        assert updated.version == 2
    
    def test_submit_for_review(self, manager, repo, sample_script):
        """Test submitting storyboard for review."""
        project = repo.create_project(
            user_id=1,
            channel_id=None,
            channel_name="Test Channel",
            mode="ai_video",
            topic="Test",
            objective="Test objective",
            target_platform="youtube_shorts",
            target_duration_seconds=45,
            target_language="vi",
            content_style="trend_decode",
            visual_style="modern_documentary",
            voice_id="",
            subtitle_style_id="",
            background_music_enabled=True,
            user_instructions="",
        )
        run = repo.create_run(project.id, user_id=1)
        
        storyboard = manager.create_from_script(run_id=run.id, user_id=1, script=sample_script)
        
        updated = manager.submit_for_review(run_id=run.id, user_id=1)
        
        assert updated.status == StoryboardStatus.IN_REVIEW
    
    def test_approve_storyboard(self, manager, repo, sample_script):
        """Test approving storyboard."""
        project = repo.create_project(
            user_id=1,
            channel_id=None,
            channel_name="Test Channel",
            mode="ai_video",
            topic="Test",
            objective="Test objective",
            target_platform="youtube_shorts",
            target_duration_seconds=45,
            target_language="vi",
            content_style="trend_decode",
            visual_style="modern_documentary",
            voice_id="",
            subtitle_style_id="",
            background_music_enabled=True,
            user_instructions="",
        )
        run = repo.create_run(project.id, user_id=1)
        
        storyboard = manager.create_from_script(run_id=run.id, user_id=1, script=sample_script)
        
        updated = manager.approve_storyboard(run_id=run.id, user_id=1, approver_id=2, notes="Looks good")
        
        assert updated.status == StoryboardStatus.APPROVED
        assert updated.metadata["approved_by"] == 2
        assert updated.metadata["approval_notes"] == "Looks good"
    
    def test_reject_storyboard(self, manager, repo, sample_script):
        """Test rejecting storyboard."""
        project = repo.create_project(
            user_id=1,
            channel_id=None,
            channel_name="Test Channel",
            mode="ai_video",
            topic="Test",
            objective="Test objective",
            target_platform="youtube_shorts",
            target_duration_seconds=45,
            target_language="vi",
            content_style="trend_decode",
            visual_style="modern_documentary",
            voice_id="",
            subtitle_style_id="",
            background_music_enabled=True,
            user_instructions="",
        )
        run = repo.create_run(project.id, user_id=1)
        
        storyboard = manager.create_from_script(run_id=run.id, user_id=1, script=sample_script)
        
        updated = manager.reject_storyboard(run_id=run.id, user_id=1, approver_id=2, reason="Needs more work")
        
        assert updated.status == StoryboardStatus.REJECTED
        assert updated.metadata["rejected_by"] == 2
        assert updated.metadata["rejection_reason"] == "Needs more work"
