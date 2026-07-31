"""
Tests for Content OS foundation components.

Tests feature flag, state machine, artifact store, and repository.
"""
import json
import tempfile
import time
from pathlib import Path
import pytest

from universal_video_ai.content_os.enums import (
    WorkflowStage,
    RunStatus,
    StepStatus,
    RiskLevel,
    AuditDecision,
    ApprovalType,
    MemoryType,
    ArtifactType,
)
from universal_video_ai.content_os.exceptions import (
    InvalidTransitionError,
    ApprovalRequiredError,
    ArtifactNotFoundError,
    ArtifactValidationError,
)
from universal_video_ai.content_os.state_machine import StateMachine
from universal_video_ai.content_os.artifact_store import ArtifactStore
from universal_video_ai.content_os.repository import ContentOSRepository
from universal_video_ai.content_os.schemas import (
    CreateProjectRequest,
    TrendRadarResult,
    ContentPlan,
    GeneratedScript,
    AuditResult,
)


class TestFeatureFlag:
    """Test Content OS feature flag."""
    
    def test_content_os_disabled_by_default(self):
        """Content OS should be disabled by default."""
        from universal_video_ai.config import CONTENT_OS_ENABLED
        # Skip if currently enabled in environment
        if CONTENT_OS_ENABLED:
            pytest.skip("CONTENT_OS_ENABLED is currently true in environment")
        assert CONTENT_OS_ENABLED is False
    
    def test_config_values_exist(self):
        """All Content OS config values should be defined."""
        from universal_video_ai.config import (
            CONTENT_OS_MAX_AUTO_REVISIONS,
            CONTENT_OS_MAX_SOURCE_ITEMS,
            CONTENT_OS_ARTIFACT_DIR,
            CONTENT_OS_PROVIDER_TIMEOUT_SECONDS,
            CONTENT_OS_LLM_PROVIDER,
            CONTENT_OS_REQUIRE_RENDER_APPROVAL,
            CONTENT_OS_REQUIRE_PUBLISH_APPROVAL,
        )
        assert CONTENT_OS_MAX_AUTO_REVISIONS >= 0
        assert CONTENT_OS_MAX_SOURCE_ITEMS >= 1
        assert CONTENT_OS_ARTIFACT_DIR is not None
        assert CONTENT_OS_PROVIDER_TIMEOUT_SECONDS >= 5
        assert isinstance(CONTENT_OS_LLM_PROVIDER, str)
        assert isinstance(CONTENT_OS_REQUIRE_RENDER_APPROVAL, bool)
        assert isinstance(CONTENT_OS_REQUIRE_PUBLISH_APPROVAL, bool)


class TestStateMachine:
    """Test workflow state machine."""
    
    def test_valid_transition_created_to_trend_research(self):
        """Should allow transition from CREATED to TREND_RESEARCH."""
        StateMachine.validate_transition(
            WorkflowStage.CREATED,
            WorkflowStage.TREND_RESEARCH,
        )
    
    def test_valid_transition_trend_to_source_selection(self):
        """Should allow transition from TREND_RESEARCH to SOURCE_SELECTION."""
        StateMachine.validate_transition(
            WorkflowStage.TREND_RESEARCH,
            WorkflowStage.SOURCE_SELECTION,
        )
    
    def test_valid_transition_audit_to_awaiting_approval(self):
        """Should allow transition from SCRIPT_AUDIT to AWAITING_APPROVAL."""
        StateMachine.validate_transition(
            WorkflowStage.SCRIPT_AUDIT,
            WorkflowStage.AWAITING_APPROVAL,
        )
    
    def test_valid_transition_audit_to_revision(self):
        """Should allow transition from SCRIPT_AUDIT to SCRIPT_REVISION."""
        StateMachine.validate_transition(
            WorkflowStage.SCRIPT_AUDIT,
            WorkflowStage.SCRIPT_REVISION,
        )
    
    def test_invalid_transition(self):
        """Should reject invalid transition."""
        with pytest.raises(InvalidTransitionError):
            StateMachine.validate_transition(
                WorkflowStage.CREATED,
                WorkflowStage.COMPLETED,
            )
    
    def test_approval_required_for_awaiting_approval(self):
        """Should require approval to leave AWAITING_APPROVAL."""
        with pytest.raises(InvalidTransitionError):
            StateMachine.validate_transition(
                WorkflowStage.AWAITING_APPROVAL,
                WorkflowStage.APPROVED,
                has_approval=False,
            )
    
    def test_approval_allows_transition(self):
        """Should allow transition with approval."""
        StateMachine.validate_transition(
            WorkflowStage.AWAITING_APPROVAL,
            WorkflowStage.APPROVED,
            has_approval=True,
        )
    
    def test_terminal_states(self):
        """Should identify terminal states correctly."""
        assert StateMachine.is_terminal(WorkflowStage.COMPLETED)
        assert StateMachine.is_terminal(WorkflowStage.CANCELLED)
        assert StateMachine.is_terminal(WorkflowStage.FAILED)
        assert not StateMachine.is_terminal(WorkflowStage.TREND_RESEARCH)
    
    def test_control_states(self):
        """Should identify control states correctly."""
        assert StateMachine.is_control_state(WorkflowStage.PAUSED)
        assert StateMachine.is_control_state(WorkflowStage.CANCELLED)
        assert StateMachine.is_control_state(WorkflowStage.FAILED)
        assert StateMachine.is_control_state(WorkflowStage.BLOCKED)
        assert not StateMachine.is_control_state(WorkflowStage.TREND_RESEARCH)


class TestArtifactStore:
    """Test artifact store."""
    
    @pytest.fixture
    def temp_dir(self):
        """Temporary directory for artifact storage."""
        with tempfile.TemporaryDirectory() as tmp:
            yield Path(tmp)
    
    @pytest.fixture
    def store(self, temp_dir):
        """Artifact store instance."""
        return ArtifactStore(temp_dir)
    
    def test_write_and_read_artifact(self, store):
        """Should write and read artifact correctly."""
        data = {"test": "value", "number": 42}
        result = store.write(
            user_id=1,
            project_id=1,
            run_id=1,
            artifact_type=ArtifactType.SCRIPT,
            data=data,
            created_by_agent="TestAgent",
        )
        
        assert result["version"] == 1
        assert result["checksum"]
        assert Path(result["path"]).exists()
        
        # Read back
        read_data = store.read(
            user_id=1,
            project_id=1,
            run_id=1,
            artifact_type=ArtifactType.SCRIPT,
            version=1,
        )
        assert read_data["data"] == data
        assert read_data["created_by_agent"] == "TestAgent"
    
    def test_version_increment(self, store):
        """Should increment version for same artifact type."""
        store.write(
            user_id=1,
            project_id=1,
            run_id=1,
            artifact_type=ArtifactType.SCRIPT,
            data={"v": 1},
            created_by_agent="TestAgent",
        )
        
        result = store.write(
            user_id=1,
            project_id=1,
            run_id=1,
            artifact_type=ArtifactType.SCRIPT,
            data={"v": 2},
            created_by_agent="TestAgent",
        )
        
        assert result["version"] == 2
    
    def test_read_latest_version(self, store):
        """Should read latest version when version is None."""
        store.write(
            user_id=1,
            project_id=1,
            run_id=1,
            artifact_type=ArtifactType.SCRIPT,
            data={"v": 1},
            created_by_agent="TestAgent",
        )
        store.write(
            user_id=1,
            project_id=1,
            run_id=1,
            artifact_type=ArtifactType.SCRIPT,
            data={"v": 2},
            created_by_agent="TestAgent",
        )
        
        read_data = store.read(
            user_id=1,
            project_id=1,
            run_id=1,
            artifact_type=ArtifactType.SCRIPT,
            version=None,
        )
        assert read_data["data"]["v"] == 2
    
    def test_read_nonexistent_artifact(self, store):
        """Should raise error for nonexistent artifact."""
        with pytest.raises(ArtifactNotFoundError):
            store.read(
                user_id=1,
                project_id=1,
                run_id=1,
                artifact_type=ArtifactType.SCRIPT,
                version=999,
            )
    
    def test_list_artifacts(self, store):
        """Should list all artifacts for a run."""
        store.write(
            user_id=1,
            project_id=1,
            run_id=1,
            artifact_type=ArtifactType.SCRIPT,
            data={"type": "script"},
            created_by_agent="TestAgent",
        )
        store.write(
            user_id=1,
            project_id=1,
            run_id=1,
            artifact_type=ArtifactType.CONTENT_PLAN,
            data={"type": "plan"},
            created_by_agent="TestAgent",
        )
        
        artifacts = store.list_artifacts(user_id=1, project_id=1, run_id=1)
        assert len(artifacts) == 2
        artifact_types = {a["artifact_type"] for a in artifacts}
        assert "script" in artifact_types
        assert "content_plan" in artifact_types
    
    def test_delete_run_artifacts(self, store):
        """Should delete all artifacts for a run."""
        store.write(
            user_id=1,
            project_id=1,
            run_id=1,
            artifact_type=ArtifactType.SCRIPT,
            data={"test": "value"},
            created_by_agent="TestAgent",
        )
        
        store.delete_run_artifacts(user_id=1, project_id=1, run_id=1)
        
        artifacts = store.list_artifacts(user_id=1, project_id=1, run_id=1)
        assert len(artifacts) == 0
    
    def test_path_validation(self, store):
        """Should validate path is within user directory."""
        # Valid path
        assert store.validate_path(1, str(store.base_dir / "1" / "test.json"))
        
        # Invalid path (outside user dir)
        assert not store.validate_path(1, "/etc/passwd")
        assert not store.validate_path(1, str(store.base_dir / "999" / "test.json"))


class TestRepository:
    """Test Content OS repository."""
    
    @pytest.fixture
    def temp_db(self):
        """Temporary database for testing."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = Path(f.name)
        yield db_path
        # Give SQLite time to release the file handle
        import time
        time.sleep(0.1)
        try:
            if db_path.exists():
                db_path.unlink()
        except PermissionError:
            # Windows file locking - skip cleanup
            pass
    
    @pytest.fixture
    def repo(self, temp_db):
        """Repository instance with initialized schema."""
        from universal_video_ai.web.store import Store
        Store(db_path=temp_db)
        return ContentOSRepository(temp_db)
    
    def test_create_and_get_project(self, repo):
        """Should create and retrieve project."""
        project = repo.create_project(
            user_id=1,
            channel_id=None,
            channel_name="Test Channel",
            mode="ai_video",
            topic="Test Topic",
            objective="Test objective",
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
        assert project.channel_name == "Test Channel"
        assert project.topic == "Test Topic"
        assert project.target_platform == "youtube_shorts"
        
        retrieved = repo.get_project(project.id, user_id=1)
        assert retrieved.id == project.id
        assert retrieved.channel_name == "Test Channel"
    
    def test_list_projects(self, repo):
        """Should list projects for user."""
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
        
        projects = repo.list_projects(user_id=1)
        assert len(projects) == 2
    
    def test_create_and_get_run(self, repo):
        """Should create and retrieve run."""
        project = repo.create_project(
            user_id=1,
            channel_id=None,
            channel_name="Test Channel",
            mode="ai_video",
            topic="Test Topic",
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
        assert run.id is not None
        assert run.project_id == project.id
        assert run.status == "created"
        assert run.current_stage == "created"
        
        retrieved = repo.get_run(run.id, user_id=1)
        assert retrieved.id == run.id
    
    def test_update_run(self, repo):
        """Should update run fields."""
        project = repo.create_project(
            user_id=1,
            channel_id=None,
            channel_name="Test Channel",
            mode="ai_video",
            topic="Test Topic",
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
        
        repo.update_run(run.id, user_id=1, status="running", current_stage="trend_research")
        
        updated = repo.get_run(run.id, user_id=1)
        assert updated.status == "running"
        assert updated.current_stage == "trend_research"
    
    def test_create_and_get_step(self, repo):
        """Should create and retrieve step."""
        project = repo.create_project(
            user_id=1,
            channel_id=None,
            channel_name="Test Channel",
            mode="ai_video",
            topic="Test Topic",
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
        
        step = repo.create_step(run.id, "trend_research", "TrendRadarAgent")
        assert step.id is not None
        assert step.stage == "trend_research"
        assert step.agent_name == "TrendRadarAgent"
        assert step.status == "pending"
    
    def test_create_and_get_artifact(self, repo):
        """Should create and retrieve artifact."""
        project = repo.create_project(
            user_id=1,
            channel_id=None,
            channel_name="Test Channel",
            mode="ai_video",
            topic="Test Topic",
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
        
        artifact = repo.create_artifact(
            run_id=run.id,
            user_id=1,
            artifact_type="script",
            version=1,
            schema_version="1.0",
            path="/test/path.json",
            checksum="abc123",
            metadata={"test": "value"},
            created_by_agent="TestAgent",
        )
        
        assert artifact.id is not None
        assert artifact.artifact_type == "script"
        assert artifact.version == 1
    
    def test_create_and_get_source(self, repo):
        """Should create and retrieve source."""
        project = repo.create_project(
            user_id=1,
            channel_id=None,
            channel_name="Test Channel",
            mode="ai_video",
            topic="Test Topic",
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
        
        source = repo.create_source(
            run_id=run.id,
            user_id=1,
            platform="youtube",
            provider="youtube_api",
            source_url="https://youtube.com/watch?v=test",
            canonical_url="https://youtube.com/watch?v=test",
            title="Test Video",
            author="Test Author",
            metrics={"views": 1000},
            trend_score=0.8,
        )
        
        assert source.id is not None
        assert source.platform == "youtube"
        assert source.title == "Test Video"
        assert source.trend_score == 0.8
    
    def test_create_and_get_approval(self, repo):
        """Should create and retrieve approval."""
        project = repo.create_project(
            user_id=1,
            channel_id=None,
            channel_name="Test Channel",
            mode="ai_video",
            topic="Test Topic",
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
        
        approval = repo.create_approval(
            run_id=run.id,
            user_id=1,
            approval_type="script",
            decision="approved",
            note="Looks good",
        )
        
        assert approval.id is not None
        assert approval.decision == "approved"
        assert approval.note == "Looks good"
    
    def test_upsert_memory(self, repo):
        """Should upsert memory."""
        memory = repo.upsert_memory(
            user_id=1,
            channel_key="test_channel",
            memory_type="winning_topic",
            memory_key="cooking",
            value={"score": 0.9},
            confidence=0.8,
        )
        
        assert memory.id is not None
        assert memory.value == {"score": 0.9}
        
        # Update
        updated = repo.upsert_memory(
            user_id=1,
            channel_key="test_channel",
            memory_type="winning_topic",
            memory_key="cooking",
            value={"score": 0.95},
            confidence=0.9,
        )
        
        assert updated.id == memory.id  # Same ID
        assert updated.value == {"score": 0.95}
    
    def test_list_memories(self, repo):
        """Should list memories."""
        repo.upsert_memory(
            user_id=1,
            channel_key="test_channel",
            memory_type="winning_topic",
            memory_key="cooking",
            value={"score": 0.9},
        )
        repo.upsert_memory(
            user_id=1,
            channel_key="test_channel",
            memory_type="winning_topic",
            memory_key="fitness",
            value={"score": 0.8},
        )
        
        memories = repo.list_memories(user_id=1, channel_key="test_channel")
        assert len(memories) == 2


class TestSchemas:
    """Test Pydantic schemas."""
    
    def test_create_project_request_valid(self):
        """Should validate valid project request."""
        request = CreateProjectRequest(
            channel_name="Test Channel",
            topic="Test Topic",
            target_platforms=["youtube_shorts"],
            source_platforms=["youtube"],
            target_market="Vietnam",
            target_language="vi",
            target_duration_seconds=45,
            content_format="trend_decode",
            max_source_items=10,
        )
        assert request.channel_name == "Test Channel"
    
    def test_create_project_invalid_platform(self):
        """Should reject invalid target platform."""
        with pytest.raises(ValueError, match="Invalid target platforms"):
            CreateProjectRequest(
                channel_name="Test",
                topic="Test",
                target_platforms=["invalid_platform"],
                source_platforms=["youtube"],
                target_market="Vietnam",
                target_language="vi",
                target_duration_seconds=45,
                content_format="trend_decode",
                max_source_items=10,
            )
    
    def test_create_project_max_sources_exceeds_limit(self):
        """Should reject max_source_items exceeding limit."""
        with pytest.raises(ValueError):
            CreateProjectRequest(
                channel_name="Test",
                topic="Test",
                target_platforms=["youtube_shorts"],
                source_platforms=["youtube"],
                target_market="Vietnam",
                target_language="vi",
                target_duration_seconds=45,
                content_format="trend_decode",
                max_source_items=999,
            )
    
    def test_trend_radar_result(self):
        """Should create trend radar result."""
        result = TrendRadarResult(
            topic="cooking",
            expanded_keywords=["food", "recipe"],
        )
        assert result.topic == "cooking"
        assert len(result.expanded_keywords) == 2
    
    def test_content_plan(self):
        """Should create content plan."""
        plan = ContentPlan(
            content_angle="educational",
            target_platforms=["youtube_shorts"],
            target_duration_seconds=45,
            core_message="Learn cooking fast",
            hook="Want to cook like a pro?",
        )
        assert plan.content_angle == "educational"
        assert plan.hook == "Want to cook like a pro?"
    
    def test_generated_script(self):
        """Should create generated script."""
        script = GeneratedScript(
            title_options=["Title 1", "Title 2"],
            hook="Amazing hook",
            narration_text="Full narration",
            description="Video description",
            hashtags=["cooking", "recipe"],
            estimated_duration_seconds=45.0,
        )
        assert len(script.title_options) == 2
        assert script.estimated_duration_seconds == 45.0
    
    def test_audit_result(self):
        """Should create audit result."""
        result = AuditResult(
            decision=AuditDecision.PASS,
            overall_score=0.85,
            hook_strength=0.9,
            originality_score=0.8,
            clarity_score=0.85,
            retention_score=0.85,
            source_dependency=RiskLevel.LOW,
            copyright_risk=RiskLevel.LOW,
            factual_risk=RiskLevel.LOW,
            timing_valid=True,
        )
        assert result.decision == AuditDecision.PASS
        assert result.overall_score == 0.85
