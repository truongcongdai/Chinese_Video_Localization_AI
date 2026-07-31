"""
Test Content OS pipeline adapter.

Tests conversion of Content OS runs to localization jobs.
"""
import pytest
import tempfile
from pathlib import Path

from universal_video_ai.web.store import Store
from universal_video_ai.content_os.repository import ContentOSRepository
from universal_video_ai.content_os.artifact_store import ArtifactStore
from universal_video_ai.content_os.pipeline_adapter import PipelineAdapter
from universal_video_ai.content_os.enums import ArtifactType


class TestPipelineAdapter:
    """Test pipeline adapter."""
    
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
    def adapter(self, repo, artifact_store, temp_db):
        """Create pipeline adapter."""
        return PipelineAdapter(
            repository=repo,
            artifact_store=artifact_store,
            web_store_db_path=temp_db,
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
        """Create a test run in ready_for_localization state."""
        run = repo.create_run(project_id=project.id, user_id=1)
        repo.update_run(
            run_id=run.id,
            user_id=1,
            current_stage="ready_for_localization",
            status="running",
        )
        return run
    
    @pytest.fixture
    def script_artifact(self, artifact_store, run, project):
        """Create a script artifact."""
        return artifact_store.write(
            user_id=1,
            project_id=project.id,
            run_id=run.id,
            artifact_type=ArtifactType.SCRIPT,
            data={
                "title_options": ["AI Gadgets Video"],
                "hook": "Check out this amazing AI gadget",
                "narration_text": "This is the narration text for the video.",
                "segments": [
                    {"start_second": 0, "end_second": 3, "text": "First segment"},
                    {"start_second": 3, "end_second": 6, "text": "Second segment"},
                ],
            },
            created_by_agent="script_writer",
        )
    
    @pytest.fixture
    def content_plan_artifact(self, artifact_store, run, project):
        """Create a content plan artifact."""
        return artifact_store.write(
            user_id=1,
            project_id=project.id,
            run_id=run.id,
            artifact_type=ArtifactType.CONTENT_PLAN,
            data={
                "content_angle": "Educational",
                "hook": "Hook text",
                "core_message": "AI is changing everything",
                "beats": [],
            },
            created_by_agent="content_planner",
        )
    
    def test_adapter_initialization(self, adapter):
        """Test adapter initializes correctly."""
        assert adapter.repository is not None
        assert adapter.artifact_store is not None
        assert adapter.web_store_db_path is not None
    
    def test_create_job_from_run(self, adapter, run, script_artifact, content_plan_artifact):
        """Test creating a job from a run."""
        job_id = adapter.create_job_from_run(run.id, user_id=1)
        
        assert job_id is not None
        assert isinstance(job_id, str)
    
    def test_create_job_with_source_url(self, adapter, run, script_artifact, content_plan_artifact):
        """Test creating a job with a specific source URL."""
        job_id = adapter.create_job_from_run(
            run.id,
            user_id=1,
            source_url="https://youtube.com/watch?v=test123",
        )
        
        assert job_id is not None
        
        # Verify job was created in web store
        job_status = adapter.get_job_status(job_id)
        assert job_status is not None
        assert job_status["source_url"] == "https://youtube.com/watch?v=test123"
    
    def test_create_job_with_selected_source(self, adapter, run, script_artifact, content_plan_artifact, repo):
        """Test creating a job using selected source."""
        # Add a selected source
        repo.create_source(
            run_id=run.id,
            user_id=1,
            platform="youtube",
            provider="manual",
            source_url="https://youtube.com/watch?v=source1",
            canonical_url="https://youtube.com/watch?v=source1",
            title="Source Video",
            author="Author",
        )
        repo.update_source(
            source_id=1,
            user_id=1,
            selected=1,
        )
        
        job_id = adapter.create_job_from_run(run.id, user_id=1)
        
        assert job_id is not None
        
        job_status = adapter.get_job_status(job_id)
        # URL may be normalized by the adapter
        assert job_status["source_url"] is not None
        assert "source1" in job_status["source_url"]
    
    def test_get_job_status(self, adapter, run, script_artifact, content_plan_artifact):
        """Test getting job status."""
        job_id = adapter.create_job_from_run(run.id, user_id=1)
        
        status = adapter.get_job_status(job_id)
        
        assert status is not None
        assert status["id"] == job_id
        # Content OS jobs are marked as "done" immediately since they don't run the full pipeline
        assert status["status"] in ["queued", "done"]
    
    def test_get_job_status_not_found(self, adapter):
        """Test getting status for non-existent job."""
        status = adapter.get_job_status("nonexistent-job-id")
        assert status is None
    
    def test_create_job_requires_ready_state(self, adapter, repo, project, script_artifact, content_plan_artifact):
        """Test that job creation requires run to be in ready state."""
        run = repo.create_run(project_id=project.id, user_id=1)
        # Don't update to ready_for_localization
        
        with pytest.raises(Exception):  # WorkflowError
            adapter.create_job_from_run(run.id, user_id=1)
    
    def test_update_run_from_job_status(self, adapter, run, script_artifact, content_plan_artifact):
        """Test updating run based on job status."""
        job_id = adapter.create_job_from_run(run.id, user_id=1)
        
        # Simulate job progression: queued -> running -> rendered -> done
        import sqlite3
        with sqlite3.connect(str(adapter.web_store_db_path)) as conn:
            conn.execute(
                "UPDATE jobs SET status = 'running' WHERE id = ?",
                (job_id,),
            )
            conn.commit()
        
        adapter.update_run_from_job_status(run.id, user_id=1, job_id=job_id)
        
        updated_run = adapter.repository.get_run(run.id, user_id=1)
        assert updated_run.current_stage == "localization_running"
        assert updated_run.status == "running"
        
        # Simulate rendering phase
        with sqlite3.connect(str(adapter.web_store_db_path)) as conn:
            conn.execute(
                "UPDATE jobs SET status = 'review' WHERE id = ?",
                (job_id,),
            )
            conn.commit()
        
        adapter.update_run_from_job_status(run.id, user_id=1, job_id=job_id)
        
        updated_run = adapter.repository.get_run(run.id, user_id=1)
        assert updated_run.current_stage == "rendered"
        
        # Now simulate completion
        with sqlite3.connect(str(adapter.web_store_db_path)) as conn:
            conn.execute(
                "UPDATE jobs SET status = 'done' WHERE id = ?",
                (job_id,),
            )
            conn.commit()
        
        adapter.update_run_from_job_status(run.id, user_id=1, job_id=job_id)
        
        updated_run = adapter.repository.get_run(run.id, user_id=1)
        assert updated_run.current_stage == "completed"
        assert updated_run.status == "done"
    
    def test_update_run_from_job_status_running(self, adapter, run, script_artifact, content_plan_artifact):
        """Test updating run when job is running."""
        job_id = adapter.create_job_from_run(run.id, user_id=1)
        
        # Simulate job running
        import sqlite3
        with sqlite3.connect(str(adapter.web_store_db_path)) as conn:
            conn.execute(
                "UPDATE jobs SET status = 'running' WHERE id = ?",
                (job_id,),
            )
            conn.commit()
        
        adapter.update_run_from_job_status(run.id, user_id=1, job_id=job_id)
        
        updated_run = adapter.repository.get_run(run.id, user_id=1)
        assert updated_run.current_stage == "localization_running"
        assert updated_run.status == "running"
