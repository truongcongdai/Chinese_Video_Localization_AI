"""
Tests for renderer and MP4 validator.
"""
import pytest
from pathlib import Path
from universal_video_ai.content_os.renderer import (
    Renderer, MP4Validator, RenderJob, RenderStatus,
    ValidationResult, ValidationStatus
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
def renderer(repo):
    """Renderer instance."""
    return Renderer(repo)


@pytest.fixture
def validator():
    """MP4 validator instance."""
    return MP4Validator()


@pytest.fixture
def temp_output_dir(tmp_path):
    """Temporary output directory."""
    output_dir = tmp_path / "renders"
    output_dir.mkdir()
    return output_dir


class TestRenderer:
    """Test renderer."""
    
    def test_submit_render_job(self, renderer, temp_output_dir):
        """Test submitting a render job."""
        output_path = temp_output_dir / "test.mp4"
        job = renderer.submit_render_job(
            run_id=1,
            user_id=1,
            timeline_path="/timelines/1.json",
            output_path=str(output_path),
        )
        
        assert job.run_id == 1
        assert job.user_id == 1
        assert job.status == RenderStatus.PENDING
        assert job.progress == 0.0
        assert job.timeline_path == "/timelines/1.json"
        assert job.output_path == str(output_path)
    
    def test_start_render(self, renderer, temp_output_dir):
        """Test starting a render job - requires FFmpeg."""
        pytest.skip("Requires FFmpeg - skipped for CI")
    
    def test_get_render_job(self, renderer, repo, temp_output_dir):
        """Test retrieving render job."""
        output_path = temp_output_dir / "test.mp4"
        job = renderer.submit_render_job(
            run_id=1,
            user_id=1,
            timeline_path="/timelines/1.json",
            output_path=str(output_path),
        )
        
        retrieved = renderer.get_render_job(run_id=1, user_id=1)
        
        assert retrieved is not None
        assert retrieved.job_id == job.job_id
        assert retrieved.status == RenderStatus.PENDING


class TestMP4Validator:
    """Test MP4 validator."""
    
    def test_validate_nonexistent_file(self, validator):
        """Test validating a non-existent file."""
        result = validator.validate(
            file_path="/nonexistent.mp4",
            expected_duration=45.0,
            expected_resolution="1920x1080",
        )
        
        assert result.status == ValidationStatus.CORRUPTED
        assert "File does not exist" in result.issues
    
    def test_is_valid_for_platform_youtube_shorts(self, validator):
        """Test platform validation for YouTube Shorts."""
        result = ValidationResult(
            status=ValidationStatus.VALID,
            file_path="/output/1.mp4",
            file_size_bytes=1024000,
            duration_seconds=45.0,
            resolution="1920x1080",
            video_codec="h264",
            audio_codec="aac",
            bitrate=2500,
            issues=[],
            warnings=[],
        )
        
        assert validator.is_valid_for_platform(result, "youtube_shorts") is True
    
    def test_is_valid_for_platform_tiktok(self, validator):
        """Test platform validation for TikTok."""
        result = ValidationResult(
            status=ValidationStatus.VALID,
            file_path="/output/1.mp4",
            file_size_bytes=1024000,
            duration_seconds=45.0,
            resolution="1080x1920",
            video_codec="h264",
            audio_codec="aac",
            bitrate=2500,
            issues=[],
            warnings=[],
        )
        
        assert validator.is_valid_for_platform(result, "tiktok") is True
    
    def test_is_invalid_for_platform_duration(self, validator):
        """Test platform validation with invalid duration."""
        result = ValidationResult(
            status=ValidationStatus.VALID,
            file_path="/output/1.mp4",
            file_size_bytes=1024000,
            duration_seconds=90.0,  # Too long for shorts
            resolution="1080x1920",
            video_codec="h264",
            audio_codec="aac",
            bitrate=2500,
            issues=[],
            warnings=[],
        )
        
        assert validator.is_valid_for_platform(result, "youtube_shorts") is False
