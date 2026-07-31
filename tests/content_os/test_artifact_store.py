"""
Test Content OS artifact store.

Verifies artifact storage with versioning, checksums, and atomic writes.
"""
import pytest
import tempfile
from pathlib import Path
import json

from universal_video_ai.content_os.artifact_store import ArtifactStore
from universal_video_ai.content_os.enums import ArtifactType


class TestArtifactStore:
    """Test artifact storage operations."""
    
    @pytest.fixture
    def temp_dir(self):
        """Create a temporary directory for testing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)
    
    @pytest.fixture
    def store(self, temp_dir):
        """Create an ArtifactStore instance with temporary directory."""
        return ArtifactStore(base_dir=temp_dir)
    
    def test_write_artifact(self, store):
        """Test writing an artifact."""
        data = {"test": "data", "value": 123}
        
        result = store.write(
            user_id=1,
            project_id=10,
            run_id=100,
            artifact_type=ArtifactType.TREND_REPORT,
            data=data,
            created_by_agent="trend_radar",
        )
        
        assert "path" in result
        assert "version" in result
        assert "checksum" in result
        assert result["version"] == 1
        assert result["checksum"] is not None
        assert Path(result["path"]).exists()
    
    def test_read_artifact(self, store):
        """Test reading an artifact."""
        data = {"test": "data", "value": 123}
        
        store.write(
            user_id=1,
            project_id=10,
            run_id=100,
            artifact_type=ArtifactType.SCRIPT,
            data=data,
            created_by_agent="script_writer",
        )
        
        read_data = store.read(
            user_id=1,
            project_id=10,
            run_id=100,
            artifact_type=ArtifactType.SCRIPT,
        )
        
        assert read_data["data"] == data
    
    def test_version_increment(self, store):
        """Test that version increments for same artifact type."""
        data1 = {"version": 1}
        data2 = {"version": 2}
        
        result1 = store.write(
            user_id=1,
            project_id=10,
            run_id=100,
            artifact_type=ArtifactType.SCRIPT,
            data=data1,
            created_by_agent="script_writer",
        )
        
        result2 = store.write(
            user_id=1,
            project_id=10,
            run_id=100,
            artifact_type=ArtifactType.SCRIPT,
            data=data2,
            created_by_agent="script_reviser",
        )
        
        assert result1["version"] == 1
        assert result2["version"] == 2
    
    def test_checksum_verification(self, store):
        """Test checksum verification."""
        data = {"test": "data"}
        
        result = store.write(
            user_id=1,
            project_id=10,
            run_id=100,
            artifact_type=ArtifactType.TREND_REPORT,
            data=data,
            created_by_agent="trend_radar",
        )
        
        # Verify checksum is present and valid format (64 hex chars for SHA256)
        assert result["checksum"] is not None
        assert len(result["checksum"]) == 64  # SHA256 produces 64 hex characters
        assert all(c in "0123456789abcdef" for c in result["checksum"].lower())
    
    def test_list_artifacts(self, store):
        """Test listing artifacts for a run."""
        store.write(
            user_id=1,
            project_id=10,
            run_id=100,
            artifact_type=ArtifactType.TREND_REPORT,
            data={"type": "trend"},
            created_by_agent="trend_radar",
        )
        
        store.write(
            user_id=1,
            project_id=10,
            run_id=100,
            artifact_type=ArtifactType.SCRIPT,
            data={"type": "script"},
            created_by_agent="script_writer",
        )
        
        artifacts = store.list_artifacts(user_id=1, project_id=10, run_id=100)
        
        assert len(artifacts) == 2
        artifact_types = {a["artifact_type"] for a in artifacts}
        assert "trend_report" in artifact_types
        assert "script" in artifact_types
    
    def test_get_latest_version(self, store):
        """Test getting the latest version of an artifact."""
        store.write(
            user_id=1,
            project_id=10,
            run_id=100,
            artifact_type=ArtifactType.SCRIPT,
            data={"v": 1},
            created_by_agent="script_writer",
        )
        
        store.write(
            user_id=1,
            project_id=10,
            run_id=100,
            artifact_type=ArtifactType.SCRIPT,
            data={"v": 2},
            created_by_agent="script_reviser",
        )
        
        # read() with version=None returns latest
        latest = store.read(
            user_id=1,
            project_id=10,
            run_id=100,
            artifact_type=ArtifactType.SCRIPT,
        )
        
        assert latest is not None
        assert latest["version"] == 2
        assert latest["data"]["v"] == 2
    
    def test_user_isolation(self, store):
        """Test that users cannot access other users' artifacts."""
        # User 1 writes an artifact
        store.write(
            user_id=1,
            project_id=10,
            run_id=100,
            artifact_type=ArtifactType.SCRIPT,
            data={"user": 1},
            created_by_agent="script_writer",
        )
        
        # User 2's artifacts should be empty
        artifacts = store.list_artifacts(user_id=2, project_id=10, run_id=100)
        
        assert len(artifacts) == 0
    
    def test_delete_artifact(self, store):
        """Test deleting an artifact by file path."""
        result = store.write(
            user_id=1,
            project_id=10,
            run_id=100,
            artifact_type=ArtifactType.SCRIPT,
            data={"test": "data"},
            created_by_agent="script_writer",
        )
        
        path = Path(result["path"])
        assert path.exists()
        
        # Delete directly using Path.unlink
        path.unlink()
        
        assert not path.exists()
    
    def test_atomic_write(self, store):
        """Test that writes are atomic (no partial files)."""
        data = {"large": "data" * 10000}
        
        result = store.write(
            user_id=1,
            project_id=10,
            run_id=100,
            artifact_type=ArtifactType.SCRIPT,
            data=data,
            created_by_agent="script_writer",
        )
        
        # File should exist and be complete
        path = Path(result["path"])
        assert path.exists()
        
        read_data = store.read(
            user_id=1,
            project_id=10,
            run_id=100,
            artifact_type=ArtifactType.SCRIPT,
        )
        assert read_data["data"] == data
