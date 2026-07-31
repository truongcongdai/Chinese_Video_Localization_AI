"""
Test Content OS repository CRUD operations.

Verifies that the repository layer works correctly with the web store database.
"""
import pytest
import tempfile
from pathlib import Path
import time
import json

from universal_video_ai.web.store import Store
from universal_video_ai.content_os.repository import ContentOSRepository
from universal_video_ai.content_os.enums import WorkflowStage, RunStatus, StepStatus, ArtifactType, ApprovalType, MemoryType


class TestContentOSRepository:
    """Test Content OS repository CRUD operations."""
    
    @pytest.fixture
    def temp_db_path(self):
        """Create a temporary database for testing."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = Path(f.name)
        yield db_path
        # Cleanup
        import gc
        gc.collect()
        try:
            if db_path.exists():
                db_path.unlink(missing_ok=True)
        except PermissionError:
            pass
    
    @pytest.fixture
    def store(self, temp_db_path):
        """Create a Store instance with temporary database."""
        return Store(db_path=temp_db_path)
    
    @pytest.fixture
    def repo(self, temp_db_path):
        """Create a ContentOSRepository instance with temporary database."""
        # Initialize web store schema first (which creates Content OS tables)
        Store(db_path=temp_db_path)
        return ContentOSRepository(db_path=temp_db_path)
    
    def test_create_project(self, repo):
        """Test creating a project."""
        project = repo.create_project(
            user_id=1,
            channel_id=None,
            channel_name="Test Channel",
            mode="ai_video",
            topic="AI gadgets",
            objective="Showcase AI technology",
            target_platform="youtube_shorts",
            target_duration_seconds=45,
            target_language="vi",
            content_style="trend_decode",
            visual_style="modern_documentary",
            voice_id="",
            subtitle_style_id="",
            background_music_enabled=True,
            user_instructions="Test instructions",
        )
        
        assert project.id is not None
        assert project.user_id == 1
        assert project.channel_name == "Test Channel"
        assert project.topic == "AI gadgets"
        assert project.target_platform == "youtube_shorts"
        assert project.mode == "ai_video"
        assert project.status == "active"
    
    def test_get_project(self, repo):
        """Test retrieving a project."""
        created = repo.create_project(
            user_id=1,
            channel_id=None,
            channel_name="Test Channel",
            mode="ai_video",
            topic="AI gadgets",
            objective="Showcase AI technology",
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
        
        retrieved = repo.get_project(created.id, user_id=1)
        
        assert retrieved is not None
        assert retrieved.id == created.id
        assert retrieved.channel_name == "Test Channel"
        assert retrieved.mode == "ai_video"
    
    def test_get_project_user_isolation(self, repo):
        """Test that users cannot access other users' projects."""
        project = repo.create_project(
            user_id=1,
            channel_id=None,
            channel_name="User 1 Channel",
            mode="ai_video",
            topic="Topic",
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
        
        # User 2 should not be able to access user 1's project
        retrieved = repo.get_project(project.id, user_id=2)
        assert retrieved is None
    
    def test_list_projects(self, repo):
        """Test listing projects for a user."""
        repo.create_project(
            user_id=1,
            channel_id=None,
            channel_name="Channel 1",
            mode="ai_video",
            topic="Topic 1",
            objective="Test objective 1",
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
        
        repo.create_project(
            user_id=1,
            channel_id=None,
            channel_name="Channel 2",
            mode="ai_video",
            topic="Topic 2",
            objective="Test objective 2",
            target_platform="facebook_reels",
            target_duration_seconds=60,
            target_language="vi",
            content_style="trend_decode",
            visual_style="modern_documentary",
            voice_id="",
            subtitle_style_id="",
            background_music_enabled=True,
            user_instructions="",
        )
        
        # Create a project for another user
        repo.create_project(
            user_id=2,
            channel_id=None,
            channel_name="Other Channel",
            mode="ai_video",
            topic="Other Topic",
            objective="Other objective",
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
        
        projects = repo.list_projects(user_id=1)
        
        assert len(projects) == 2
        assert all(p.user_id == 1 for p in projects)
    
    def test_create_run(self, repo):
        """Test creating a run from a project."""
        project = repo.create_project(
            user_id=1,
            channel_id=None,
            channel_name="Test Channel",
            mode="ai_video",
            topic="AI gadgets",
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
        
        run = repo.create_run(
            project_id=project.id,
            user_id=1,
        )
        
        assert run.id is not None
        assert run.project_id == project.id
        assert run.user_id == 1
        assert run.status == "created"
        assert run.current_stage == "created"
        assert run.progress_percent == 0
        assert run.revision_count == 0
    
    def test_update_run_status(self, repo):
        """Test updating run status and stage."""
        project = repo.create_project(
            user_id=1,
            channel_id=None,
            channel_name="Test Channel",
            mode="ai_video",
            topic="AI gadgets",
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
        
        run = repo.create_run(project_id=project.id, user_id=1)
        
        repo.update_run(
            run_id=run.id,
            user_id=1,
            status="running",
            current_stage="trend_research",
            progress_percent=10,
        )
        
        updated = repo.get_run(run.id, user_id=1)
        
        assert updated.status == "running"
        assert updated.current_stage == "trend_research"
        assert updated.progress_percent == 10
    
    def test_create_artifact(self, repo):
        """Test creating an artifact."""
        project = repo.create_project(
            user_id=1,
            channel_id=None,
            channel_name="Test Channel",
            mode="ai_video",
            topic="AI gadgets",
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
        
        run = repo.create_run(project_id=project.id, user_id=1)
        
        artifact = repo.create_artifact(
            run_id=run.id,
            user_id=1,
            artifact_type=ArtifactType.TREND_REPORT,
            version=1,
            schema_version="1.0",
            path="/test/path.json",
            checksum="abc123",
            metadata={"test": "data"},
            created_by_agent="trend_radar",
        )
        
        assert artifact.id is not None
        assert artifact.artifact_type == "trend_report"
        assert artifact.version == 1
        assert artifact.path == "/test/path.json"
    
    def test_create_source(self, repo):
        """Test creating a source."""
        project = repo.create_project(
            user_id=1,
            channel_id=None,
            channel_name="Test Channel",
            mode="ai_video",
            topic="AI gadgets",
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
        
        run = repo.create_run(project_id=project.id, user_id=1)
        
        source = repo.create_source(
            run_id=run.id,
            user_id=1,
            platform="youtube",
            provider="downloader_adapter",
            source_url="https://youtube.com/watch?v=test",
            canonical_url="https://youtube.com/watch?v=test",
            title="Test Video",
            author="Test Author",
            metrics={"view_count": 1000, "like_count": 100},
            trend_score=0.5,
        )
        
        # Update to set selected and risk
        repo.update_source(
            source_id=source.id,
            user_id=1,
            selected=1,
            risk_json=json.dumps({"reuse_risk": "low", "copyright_risk": "low"}),
        )
        
        updated = repo.get_source(source.id, user_id=1)
        
        assert updated.id is not None
        assert updated.platform == "youtube"
        assert updated.title == "Test Video"
        assert updated.selected == 1
        assert updated.trend_score == 0.5
    
    def test_create_approval(self, repo):
        """Test creating an approval record."""
        project = repo.create_project(
            user_id=1,
            channel_id=None,
            channel_name="Test Channel",
            mode="ai_video",
            topic="AI gadgets",
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
        
        run = repo.create_run(project_id=project.id, user_id=1)
        
        approval = repo.create_approval(
            run_id=run.id,
            user_id=1,
            approval_type=ApprovalType.SCRIPT,
            decision="approved",
            note="Script looks good",
        )
        
        assert approval.id is not None
        assert approval.approval_type == "script"
        assert approval.decision == "approved"
        assert approval.note == "Script looks good"
    
    def test_upsert_memory(self, repo):
        """Test creating/updating a memory record."""
        memory = repo.upsert_memory(
            user_id=1,
            channel_key="test_channel",
            memory_type=MemoryType.WINNING_TOPIC,
            memory_key="ai_gadgets",
            value={"topic": "AI gadgets", "performance": "high"},
            confidence=0.9,
        )
        
        assert memory.id is not None
        assert memory.channel_key == "test_channel"
        assert memory.memory_type == "winning_topic"
        assert memory.confidence == 0.9
        assert memory.active == 1
    
    def test_get_memory(self, repo):
        """Test retrieving a memory."""
        created = repo.upsert_memory(
            user_id=1,
            channel_key="test_channel",
            memory_type=MemoryType.WINNING_TOPIC,
            memory_key="ai_gadgets",
            value={"topic": "AI gadgets"},
            confidence=0.8,
        )
        
        retrieved = repo.get_memory(
            user_id=1,
            channel_key="test_channel",
            memory_type="winning_topic",
            memory_key="ai_gadgets",
        )
        
        assert retrieved is not None
        assert retrieved.id == created.id
        assert retrieved.value["topic"] == "AI gadgets"
    
    def test_list_active_memories(self, repo):
        """Test retrieving active memories for a channel."""
        repo.upsert_memory(
            user_id=1,
            channel_key="test_channel",
            memory_type=MemoryType.WINNING_TOPIC,
            memory_key="ai_gadgets",
            value={"topic": "AI gadgets"},
            confidence=0.8,
        )
        
        repo.upsert_memory(
            user_id=1,
            channel_key="test_channel",
            memory_type=MemoryType.WEAK_HOOK,
            memory_key="bad_hook",
            value={"hook": "bad"},
            confidence=0.3,
        )
        
        # Deactivate one memory by upserting with active=False
        repo.upsert_memory(
            user_id=1,
            channel_key="test_channel",
            memory_type=MemoryType.WEAK_HOOK,
            memory_key="bad_hook",
            value={"hook": "bad"},
            confidence=0.3,
            active=False,
        )
        
        active = repo.list_memories(user_id=1, channel_key="test_channel", active_only=True)
        
        # Only active memories should be returned
        assert all(m.active for m in active)
        assert len(active) == 1
