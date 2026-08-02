"""
Test Content OS workflow orchestration.

Tests workflow lifecycle, state transitions, agent execution, and approval gates.
"""
import pytest
import tempfile
import wave
from pathlib import Path

from universal_video_ai.web.store import Store
from universal_video_ai.content_os.repository import ContentOSRepository
from universal_video_ai.content_os.artifact_store import ArtifactStore
from universal_video_ai.content_os.workflow import ContentOSWorkflow, WorkflowConfig
from universal_video_ai.content_os.enums import WorkflowStage, ApprovalType, ArtifactType
from universal_video_ai.content_os.exceptions import WorkflowError
from universal_video_ai.content_os.renderer import RenderJob, RenderStatus


class TestContentOSWorkflow:
    """Test workflow orchestration."""
    
    @pytest.fixture
    def temp_dir(self):
        """Create a temporary directory for testing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)
    
    @pytest.fixture
    def temp_db(self):
        """Create a temporary database for testing."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = Path(f.name)
        yield db_path
        import time
        import gc
        time.sleep(0.1)
        gc.collect()
        try:
            if db_path.exists():
                db_path.unlink()
        except PermissionError:
            pass
    
    @pytest.fixture
    def repo(self, temp_db):
        """Create repository with initialized schema."""
        Store(db_path=temp_db)
        return ContentOSRepository(temp_db)
    
    @pytest.fixture
    def artifact_store(self, temp_dir):
        """Create artifact store with temporary directory."""
        return ArtifactStore(base_dir=temp_dir)
    
    @pytest.fixture
    def workflow(self, repo, artifact_store):
        """Create workflow instance."""
        return ContentOSWorkflow(
            repository=repo,
            artifact_store=artifact_store,
            config=WorkflowConfig(auto_approve=True, max_revision_attempts=2),
        )
    
    @pytest.fixture
    def project(self, repo):
        """Create a test project."""
        return repo.create_project(
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
    
    @pytest.fixture
    def run(self, repo, project):
        """Create a test run."""
        return repo.create_run(project_id=project.id, user_id=1)
    
    def test_workflow_initialization(self, workflow):
        """Test workflow initializes correctly."""
        assert workflow.repository is not None
        assert workflow.artifact_store is not None
        assert len(workflow.agents) == 6
        assert "trend_radar" in workflow.agents
        assert "script_writer" in workflow.agents
        # Check new production components
        assert workflow.storyboard_manager is not None
        assert workflow.asset_resolver is not None
        assert workflow.renderer is not None
        assert workflow.mp4_validator is not None
        assert workflow.tts_adapter is not None
        assert workflow.subtitle_adapter is not None
        assert workflow.timeline_adapter is not None
    
    def test_start_run_basic(self, workflow, run):
        """Test starting a workflow run."""
        result = workflow.start_run(run.id, user_id=1)
        
        # With auto_approve=True, workflow runs through all stages to completion
        assert result["status"] in ["ready_for_localization", "completed"]
        assert result["run_id"] == run.id
        # When completed, result includes output_path and validation
        if result["status"] == "completed":
            assert "output_path" in result
            assert "validation" in result
    
    def test_workflow_creates_steps(self, workflow, run, repo):
        """Test workflow creates step records."""
        workflow.start_run(run.id, user_id=1)
        
        steps = repo.list_steps(run.id)
        
        # Should have steps for each agent executed
        assert len(steps) > 0
        
        # Check step statuses
        completed_steps = [s for s in steps if s.status == "completed"]
        assert len(completed_steps) > 0
    
    def test_workflow_creates_artifacts(self, workflow, run, repo):
        """Test workflow creates artifact records."""
        workflow.start_run(run.id, user_id=1)
        
        artifacts = repo.list_artifacts(run.id)
        
        assert len(artifacts) > 0
        
        # Check for expected artifact types
        artifact_types = {a.artifact_type for a in artifacts}
        assert "trend_report" in artifact_types
        assert "script" in artifact_types
    
    def test_workflow_state_transitions(self, workflow, run, repo):
        """Test workflow advances through stages correctly."""
        workflow.start_run(run.id, user_id=1)
        
        updated_run = repo.get_run(run.id, user_id=1)
        
        # Should end in ready_for_localization or completed
        assert updated_run.current_stage in ["ready_for_localization", "completed"]
        assert updated_run.status == "completed"
        assert updated_run.progress_percent == 100
    
    def test_cancel_run(self, workflow, run, repo):
        """Test cancelling a run."""
        workflow.cancel_run(run.id, user_id=1)
        
        updated_run = repo.get_run(run.id, user_id=1)
        
        assert updated_run.status == "cancelled"
        assert updated_run.current_stage == "cancelled"
    
    def test_submit_approval_approved(self, workflow, run, repo):
        """Test submitting approval decision (approved)."""
        # First, run workflow to awaiting_approval
        workflow_no_auto = ContentOSWorkflow(
            repository=repo,
            artifact_store=workflow.artifact_store,
            config=WorkflowConfig(auto_approve=False),
        )
        
        # Manually set to awaiting_approval for testing
        repo.update_run(
            run_id=run.id,
            user_id=1,
            status="running",
            current_stage="awaiting_approval",
        )
        
        result = workflow_no_auto.submit_approval(
            run_id=run.id,
            user_id=1,
            approval_type=ApprovalType.SCRIPT,
            decision="approved",
            note="Looks good",
        )
        
        # Should advance to approved
        updated_run = repo.get_run(run.id, user_id=1)
        assert updated_run.current_stage in ["approved", "ready_for_localization"]
    
    def test_submit_approval_rejected(self, workflow, run, repo):
        """Test submitting approval decision (rejected)."""
        # Manually set to awaiting_approval for testing
        repo.update_run(
            run_id=run.id,
            user_id=1,
            status="running",
            current_stage="awaiting_approval",
        )
        
        result = workflow.submit_approval(
            run_id=run.id,
            user_id=1,
            approval_type=ApprovalType.SCRIPT,
            decision="rejected",
            note="Not good",
        )
        
        assert result["status"] == "cancelled"
        
        updated_run = repo.get_run(run.id, user_id=1)
        assert updated_run.status == "cancelled"
    
    def test_workflow_with_sources(self, workflow, run, repo):
        """Test workflow with pre-existing sources."""
        # Add a source
        repo.create_source(
            run_id=run.id,
            user_id=1,
            platform="youtube",
            provider="manual",
            source_url="https://youtube.com/watch?v=test",
            canonical_url="https://youtube.com/watch?v=test",
            title="Test Video",
            author="Test Author",
            metrics={"view_count": 1000},
        )
        
        result = workflow.start_run(run.id, user_id=1)
        
        assert result["status"] in ["ready_for_localization", "completed"]
    
    def test_workflow_error_handling(self, workflow, run, repo):
        """Test workflow handles errors gracefully."""
        # This test verifies that errors are caught and recorded
        # Since agents use mock outputs, this should complete successfully
        result = workflow.start_run(run.id, user_id=1)
        
        assert result["status"] in ["ready_for_localization", "completed"]

    def test_generate_voice_respects_project_target_language_for_vietnamese(
        self, workflow, run
    ):
        """Vietnamese scripts are mostly ASCII; do not auto-switch them to English TTS."""
        captured = {}

        def fake_generate_audio(text, language="vi", voice_id=None, output_dir=None):
            captured["text"] = text
            captured["language"] = language
            captured["voice_id"] = voice_id
            output_path = Path(output_dir) / "voice.wav"
            with wave.open(str(output_path), "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(24000)
                wav.writeframes(b"\x00\x00" * 24000)
            return output_path

        workflow.tts_adapter.generate_audio = fake_generate_audio
        manifest = workflow._generate_voice(
            run.id,
            user_id=1,
            context={
                "script": {
                    "segments": [
                        {
                            "narration": "Dừng ngay việc học tiếng Anh kiểu cũ! Đây là 3 tính năng AI trên điện thoại.",
                            "text": "Caption ngắn",
                        }
                    ]
                }
            },
        )

        assert captured["language"] == "vi"
        assert captured["voice_id"] is None
        assert "Dừng ngay" in captured["text"]
        assert manifest["language"] == "vi"
        assert manifest["voice_id"] == ""

    def test_retry_failed_run_resets_stage_before_full_execution(
        self, workflow, run, repo, monkeypatch
    ):
        """A failed partial run can restart without an invalid backward transition."""
        repo.update_run(
            run_id=run.id,
            user_id=1,
            status="failed",
            current_stage="source_analysis",
            progress_percent=13,
            revision_count=2,
            error_json='{"error":"previous failure"}',
        )
        state_at_execution = {}

        def fake_execute(run_id, user_id):
            current = repo.get_run(run_id, user_id)
            state_at_execution.update(
                status=current.status,
                stage=current.current_stage,
                progress=current.progress_percent,
                revision_count=current.revision_count,
                error_json=current.error_json,
            )
            return {"status": "completed", "run_id": run_id}

        monkeypatch.setattr(workflow, "_execute_workflow", fake_execute)

        workflow.start_run(run.id, user_id=1)

        assert state_at_execution == {
            "status": "running",
            "stage": "created",
            "progress": 0,
            "revision_count": 0,
            "error_json": None,
        }

    def test_start_rejects_duplicate_running_run(self, workflow, run, repo):
        repo.update_run(run_id=run.id, user_id=1, status="running")

        with pytest.raises(WorkflowError, match="already running"):
            workflow.start_run(run.id, user_id=1)

    def test_render_failure_is_not_reported_as_completed(
        self, workflow, run, repo
    ):
        context = {
            "timeline": {"total_duration": 1.0},
            "voice_manifest": {},
            "subtitle_manifest": {},
            "resolved_assets": {"assets": []},
        }
        failed_job = RenderJob(
            job_id="render_test",
            run_id=run.id,
            user_id=1,
            status=RenderStatus.FAILED,
            timeline_path="timeline.json",
            output_path="final.mp4",
            progress=0.0,
            error_message="ffmpeg failed",
            started_at=0.0,
            completed_at=1.0,
            metadata={},
        )
        workflow.renderer.submit_render_job = lambda **_: failed_job
        workflow.renderer.start_render = lambda job: failed_job

        with pytest.raises(RuntimeError, match="ffmpeg failed"):
            workflow._render_video(run.id, 1, context)

        artifact = workflow.artifact_store.read(
            user_id=1,
            project_id=run.project_id,
            run_id=run.id,
            artifact_type=ArtifactType.RENDER_REPORT,
        )
        assert artifact["data"]["status"] == "failed"
    
    def test_calculate_progress(self, workflow):
        """Test progress calculation for stages."""
        assert workflow._calculate_progress(WorkflowStage.CREATED) == 0
        assert workflow._calculate_progress(WorkflowStage.TREND_RESEARCH) > 0
        # COMPLETED is last of 22 stages, so 21/22 ≈ 95%
        assert workflow._calculate_progress(WorkflowStage.COMPLETED) == 95
    
    def test_max_revision_attempts(self, workflow, run, repo):
        """Test workflow respects max revision attempts."""
        config = WorkflowConfig(auto_approve=True, max_revision_attempts=1)
        workflow_limited = ContentOSWorkflow(
            repository=repo,
            artifact_store=workflow.artifact_store,
            config=config,
        )
        
        result = workflow_limited.start_run(run.id, user_id=1)
        
        # Should still complete despite limited revisions
        assert result["status"] in ["ready_for_localization", "completed"]
