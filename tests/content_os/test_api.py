"""
Test Content OS API endpoints.

Tests the Content OS REST API router with authentication.
"""
import pytest
import tempfile
from pathlib import Path
from fastapi.testclient import TestClient

from universal_video_ai.web.app import app
from universal_video_ai.web.store import Store
from universal_video_ai.content_os.repository import ContentOSRepository
from universal_video_ai.config import CONTENT_OS_ENABLED


class TestContentOSAPI:
    """Test Content OS API endpoints."""
    
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
    def client(self, temp_db, monkeypatch):
        """Create a test client with temporary database."""
        # Initialize store schema
        Store(db_path=temp_db)
        
        # Create a test user
        store = Store(db_path=temp_db)
        user_id = store.create_user("testuser", "password_hash", credits=10)
        
        # Mock authentication to return this user
        def mock_get_current_user_id():
            return user_id
        
        from universal_video_ai.web import auth
        from fastapi import Request
        
        # Mock the get_current_user_id to bypass cookie check
        def mock_get_current_user_id_dep(request: Request = None):
            return user_id
        
        # Mock the get_content_os_components function to use temp_db
        def mock_get_components(user_id):
            from universal_video_ai.content_os.repository import ContentOSRepository
            from universal_video_ai.content_os.artifact_store import ArtifactStore
            from universal_video_ai.content_os.workflow import ContentOSWorkflow, WorkflowConfig
            from universal_video_ai.content_os.pipeline_adapter import PipelineAdapter
            
            repo = ContentOSRepository(temp_db)
            artifact_store = ArtifactStore(base_dir=Path("local_data/content_os"))
            
            workflow = ContentOSWorkflow(
                repository=repo,
                artifact_store=artifact_store,
                config=WorkflowConfig(auto_approve=False, max_revision_attempts=3),
            )
            
            adapter = PipelineAdapter(
                repository=repo,
                artifact_store=artifact_store,
                web_store_db_path=temp_db,
            )
            
            return repo, artifact_store, workflow, adapter
        
        from universal_video_ai.web import content_os_router
        content_os_router.get_content_os_components = mock_get_components
        
        # Override the dependency in the app
        app.dependency_overrides[auth.get_current_user_id] = mock_get_current_user_id_dep
        
        with TestClient(app) as test_client:
            yield test_client
        
        # Clean up dependency override
        app.dependency_overrides = {}
    
    @pytest.fixture
    def repo(self, temp_db):
        """Create repository with initialized schema."""
        Store(db_path=temp_db)
        return ContentOSRepository(temp_db)
    
    def test_health_check(self, client):
        """Test health check endpoint."""
        response = client.get("/api/content-os/health")
        
        # May return 403 if feature disabled, or 200 with status
        assert response.status_code in [200, 403]
        
        if response.status_code == 200:
            data = response.json()
            assert "enabled" in data
            assert "llm_provider" in data
    
    def test_create_project(self, client):
        """Test creating a project."""
        if not CONTENT_OS_ENABLED:
            pytest.skip("Content OS feature is disabled")
        
        response = client.post(
            "/api/content-os/projects",
            json={
                "channel_id": None,
                "channel_name": "Test Channel",
                "mode": "ai_video",
                "topic": "AI gadgets",
                "objective": "Showcase AI technology",
                "target_platform": "youtube_shorts",
                "target_duration_seconds": 45,
                "target_language": "vi",
                "content_style": "trend_decode",
                "visual_style": "modern_documentary",
                "voice_id": "",
                "subtitle_style_id": "",
                "background_music_enabled": True,
                "user_instructions": "",
            },
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["id"] > 0
        assert data["channel_name"] == "Test Channel"
        assert data["topic"] == "AI gadgets"
    
    def test_list_projects(self, client, repo):
        """Test listing projects."""
        if not CONTENT_OS_ENABLED:
            pytest.skip("Content OS feature is disabled")
        
        # Create a test project
        repo.create_project(
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
        
        response = client.get("/api/content-os/projects")
        
        assert response.status_code == 200
        data = response.json()
        assert len(data) >= 1
        assert data[0]["channel_name"] == "Test Channel"
    
    def test_get_project(self, client, repo):
        """Test getting a specific project."""
        if not CONTENT_OS_ENABLED:
            pytest.skip("Content OS feature is disabled")
        
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
            user_instructions="",
        )
        
        response = client.get(f"/api/content-os/projects/{project.id}")
        
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == project.id
        assert data["channel_name"] == "Test Channel"
    
    def test_create_run(self, client, repo):
        """Test creating a run."""
        if not CONTENT_OS_ENABLED:
            pytest.skip("Content OS feature is disabled")
        
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
            user_instructions="",
        )
        
        response = client.post(
            "/api/content-os/runs",
            json={"project_id": project.id},
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["id"] > 0
        assert data["project_id"] == project.id
        assert data["status"] == "created"
    
    def test_list_runs(self, client, repo):
        """Test listing runs."""
        if not CONTENT_OS_ENABLED:
            pytest.skip("Content OS feature is disabled")
        
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
            user_instructions="",
        )
        
        repo.create_run(project_id=project.id, user_id=1)
        
        response = client.get("/api/content-os/runs")
        
        assert response.status_code == 200
        data = response.json()
        assert len(data) >= 1
    
    def test_get_run(self, client, repo):
        """Test getting a specific run."""
        if not CONTENT_OS_ENABLED:
            pytest.skip("Content OS feature is disabled")
        
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
            user_instructions="",
        )
        
        run = repo.create_run(project_id=project.id, user_id=1)
        
        response = client.get(f"/api/content-os/runs/{run.id}")
        
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == run.id
    
    def test_feature_disabled_when_flag_off(self, client, monkeypatch):
        """Test that health endpoint shows feature as disabled when flag is off."""
        # Skip this test if CONTENT_OS_ENABLED is currently true in environment
        from universal_video_ai.config import CONTENT_OS_ENABLED
        if CONTENT_OS_ENABLED:
            pytest.skip("CONTENT_OS_ENABLED is currently true in environment")
        
        monkeypatch.setattr("universal_video_ai.config.CONTENT_OS_ENABLED", False)
        
        response = client.get("/api/content-os/health")
        
        assert response.status_code == 200
        data = response.json()
        assert data["enabled"] is False
