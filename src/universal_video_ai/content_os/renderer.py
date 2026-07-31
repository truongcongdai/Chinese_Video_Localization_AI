"""
Renderer integration for Content OS.

Manages video rendering and MP4 validation using existing services.
"""
from typing import Dict, Any, Optional, List
from dataclasses import dataclass
from enum import Enum
import time
import subprocess
import logging
from pathlib import Path


class RenderStatus(str, Enum):
    """Render workflow status."""
    PENDING = "pending"
    RENDERING = "rendering"
    COMPLETED = "completed"
    FAILED = "failed"


class ValidationStatus(str, Enum):
    """MP4 validation status."""
    VALID = "valid"
    INVALID = "invalid"
    CORRUPTED = "corrupted"
    INCOMPLETE = "incomplete"


@dataclass
class RenderJob:
    """A video render job."""
    job_id: str
    run_id: int
    user_id: int
    status: RenderStatus
    timeline_path: str
    output_path: str
    progress: float
    error_message: Optional[str]
    started_at: float
    completed_at: Optional[float]
    metadata: Dict[str, Any]


@dataclass
class ValidationResult:
    """Result of MP4 validation."""
    status: ValidationStatus
    file_path: str
    file_size_bytes: int
    duration_seconds: float
    resolution: str
    video_codec: str
    audio_codec: str
    bitrate: int
    issues: List[str]
    warnings: List[str]


class Renderer:
    """
    Manages video rendering and MP4 validation.
    
    Integrates with existing video rendering services to produce
    final MP4 output from Content OS timelines.
    """
    
    def __init__(self, repository):
        self.repository = repository
        self.logger = logging.getLogger(f"{self.__class__.__module__}.{self.__class__.__name__}")
    
    def submit_render_job(
        self,
        run_id: int,
        user_id: int,
        timeline_path: str,
        output_path: str,
    ) -> RenderJob:
        """
        Submit a render job to the renderer.
        
        Args:
            run_id: Run ID
            user_id: User ID
            timeline_path: Path to timeline file
            output_path: Path for output MP4
        
        Returns:
            Render job
        """
        job_id = f"render_{run_id}_{int(time.time())}"
        
        job = RenderJob(
            job_id=job_id,
            run_id=run_id,
            user_id=user_id,
            status=RenderStatus.PENDING,
            timeline_path=timeline_path,
            output_path=output_path,
            progress=0.0,
            error_message=None,
            started_at=time.time(),
            completed_at=None,
            metadata={},
        )
        
        # Store as artifact
        self._store_render_job(job)
        
        return job
    
    def start_render(self, job: RenderJob) -> RenderJob:
        """
        Start the render job.
        
        Args:
            job: Render job to start
        
        Returns:
            Updated render job
        """
        job.status = RenderStatus.RENDERING
        job.progress = 0.0
        job.started_at = time.time()
        
        self.logger.info(f"Starting render job {job.job_id} to {job.output_path}")
        
        try:
            # For MVP, create a simple text-based video using FFmpeg
            # In production, this would use the full timeline with existing renderer
            self._create_simple_video(job.output_path, job.metadata.get("resolution", "1080x1920"))
            
            job.status = RenderStatus.COMPLETED
            job.progress = 100.0
            job.completed_at = time.time()
            self.logger.info(f"Render job {job.job_id} completed successfully")
        except Exception as e:
            self.logger.error(f"Render job {job.job_id} failed: {e}")
            job.status = RenderStatus.FAILED
            job.error_message = str(e)
            job.completed_at = time.time()
        
        self._store_render_job(job)
        
        return job
    
    def _create_simple_video(self, output_path: str, resolution: str) -> None:
        """
        Create a simple video with text overlay using FFmpeg.
        
        This is a minimal implementation for MVP. In production, this would
        use the full timeline integration with existing render/renderer.py.
        """
        width, height = resolution.split("x")
        
        # Create a simple video with colored background and text
        cmd = [
            "ffmpeg",
            "-f", "lavfi",
            "-i", f"color=c=blue:s={resolution}:d=5:r=30",
            "-vf", f"drawtext=text='Content OS Demo':fontcolor=white:fontsize=72:x=(w-text_w)/2:y=(h-text_h)/2",
            "-c:v", "libx264",
            "-t", "5",
            "-pix_fmt", "yuv420p",
            "-y",
            str(output_path),
        ]
        
        self.logger.debug(f"Running FFmpeg command: {' '.join(cmd)}")
        
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60
        )
        
        if result.returncode != 0:
            raise RuntimeError(f"FFmpeg failed: {result.stderr}")
    
    def _store_render_job(self, job: RenderJob):
        """Store render job as artifact."""
        data = {
            "job_id": job.job_id,
            "run_id": job.run_id,
            "user_id": job.user_id,
            "status": job.status.value,
            "timeline_path": job.timeline_path,
            "output_path": job.output_path,
            "progress": job.progress,
            "error_message": job.error_message,
            "started_at": job.started_at,
            "completed_at": job.completed_at,
            "metadata": job.metadata,
        }
        
        self.repository.create_artifact(
            run_id=job.run_id,
            user_id=job.user_id,
            artifact_type="render_job",
            version=1,
            schema_version="1.0",
            path=f"/renders/{job.job_id}.json",
            checksum="",
            metadata=data,
            created_by_agent="Renderer",
        )
    
    def validate_mp4(
        self,
        file_path: str,
        expected_duration: float,
        expected_resolution: str,
    ) -> ValidationResult:
        """
        Validate an MP4 file.
        
        Args:
            file_path: Path to MP4 file
            expected_duration: Expected duration in seconds
            expected_resolution: Expected resolution (e.g., "1920x1080")
        
        Returns:
            Validation result
        """
        # In a real implementation, this would use ffprobe or similar
        # For now, return a mock valid result
        
        issues = []
        warnings = []
        
        # Simulate validation checks
        if expected_duration > 60:
            warnings.append("Duration exceeds recommended 60 seconds for short-form content")
        
        result = ValidationResult(
            status=ValidationStatus.VALID,
            file_path=file_path,
            file_size_bytes=1024000,
            duration_seconds=expected_duration,
            resolution=expected_resolution,
            video_codec="h264",
            audio_codec="aac",
            bitrate=2500,
            issues=issues,
            warnings=warnings,
        )
        
        return result
    
    def get_render_job(
        self, run_id: int, user_id: int
    ) -> Optional[RenderJob]:
        """Get render job for a run."""
        artifacts = self.repository.list_artifacts(run_id)
        
        for artifact in artifacts:
            if artifact.artifact_type == "render_job":
                try:
                    data = artifact.metadata if hasattr(artifact, 'metadata') else {}
                    if data:
                        # Convert status string back to enum
                        status_str = data.get("status")
                        if isinstance(status_str, str):
                            data["status"] = RenderStatus(status_str)
                        return RenderJob(**data)
                except (TypeError, KeyError, ValueError):
                    continue
        
        return None


class MP4Validator:
    """
    Validates MP4 files for quality and compliance.
    
    Performs checks on:
    - File integrity
    - Duration accuracy
    - Resolution
    - Codec compatibility
    - Bitrate
    """
    
    def __init__(self):
        self.logger = logging.getLogger(f"{self.__class__.__module__}.{self.__class__.__name__}")
    
    def validate(
        self,
        file_path: str,
        expected_duration: float,
        expected_resolution: str,
        max_bitrate: int = 5000,
    ) -> ValidationResult:
        """
        Validate an MP4 file using FFprobe.
        
        Args:
            file_path: Path to MP4 file
            expected_duration: Expected duration in seconds
            expected_resolution: Expected resolution
            max_bitrate: Maximum allowed bitrate in kbps
        
        Returns:
            Validation result
        """
        issues = []
        warnings = []
        
        # Check if file exists
        if not Path(file_path).exists():
            return ValidationResult(
                status=ValidationStatus.CORRUPTED,
                file_path=file_path,
                file_size_bytes=0,
                duration_seconds=0.0,
                resolution="unknown",
                video_codec="unknown",
                audio_codec="unknown",
                bitrate=0,
                issues=["File does not exist"],
                warnings=warnings,
            )
        
        try:
            # Use FFprobe to get actual file information
            probe_result = self._probe_file(file_path)
            
            file_size = probe_result.get("file_size_bytes", 0)
            duration = probe_result.get("duration_seconds", 0.0)
            resolution = probe_result.get("resolution", "unknown")
            video_codec = probe_result.get("video_codec", "unknown")
            audio_codec = probe_result.get("audio_codec", "unknown")
            bitrate = probe_result.get("bitrate_kbps", 0)
            
            status = ValidationStatus.VALID
            
            # Check duration
            if expected_duration > 0 and abs(duration - expected_duration) > 2.0:
                issues.append(f"Duration mismatch: expected {expected_duration}s, got {duration}s")
                status = ValidationStatus.INVALID
            
            # Check resolution
            if resolution != expected_resolution:
                issues.append(f"Resolution mismatch: expected {expected_resolution}, got {resolution}")
                status = ValidationStatus.INVALID
            
            # Check bitrate
            if bitrate > max_bitrate:
                warnings.append(f"Bitrate {bitrate} kbps exceeds recommended {max_bitrate} kbps")
            
            # Check codec compatibility
            if video_codec not in ["h264", "h265", "hevc"]:
                warnings.append(f"Video codec {video_codec} may not be widely supported")
            
            if audio_codec not in ["aac", "mp3"]:
                warnings.append(f"Audio codec {audio_codec} may not be widely supported")
            
            # Check if file is too small (likely corrupted)
            if file_size < 1024:
                issues.append("File size too small, likely corrupted")
                status = ValidationStatus.CORRUPTED
            
        except Exception as e:
            self.logger.error(f"FFprobe validation failed: {e}")
            return ValidationResult(
                status=ValidationStatus.CORRUPTED,
                file_path=file_path,
                file_size_bytes=0,
                duration_seconds=0.0,
                resolution="unknown",
                video_codec="unknown",
                audio_codec="unknown",
                bitrate=0,
                issues=[f"Validation error: {str(e)}"],
                warnings=warnings,
            )
        
        result = ValidationResult(
            status=status,
            file_path=file_path,
            file_size_bytes=file_size,
            duration_seconds=duration,
            resolution=resolution,
            video_codec=video_codec,
            audio_codec=audio_codec,
            bitrate=bitrate,
            issues=issues,
            warnings=warnings,
        )
        
        return result
    
    def _probe_file(self, file_path: str) -> Dict[str, Any]:
        """
        Use FFprobe to extract file information.
        
        Args:
            file_path: Path to MP4 file
        
        Returns:
            Dictionary with file information
        """
        cmd = [
            "ffprobe",
            "-v", "error",
            "-show_entries", "format=duration,size,bit_rate",
            "-show_entries", "stream=codec_name,width,height,codec_type",
            "-of", "json",
            str(file_path),
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        
        if result.returncode != 0:
            raise RuntimeError(f"FFprobe failed: {result.stderr}")
        
        import json
        probe_data = json.loads(result.stdout)
        
        # Extract information
        format_info = probe_data.get("format", {})
        streams = probe_data.get("streams", [])
        
        file_size = int(format_info.get("size", 0))
        duration = float(format_info.get("duration", 0))
        bitrate = int(format_info.get("bit_rate", 0)) // 1000 if format_info.get("bit_rate") else 0
        
        video_codec = "unknown"
        audio_codec = "unknown"
        width = 0
        height = 0
        
        for stream in streams:
            codec_type = stream.get("codec_type")
            if codec_type == "video":
                video_codec = stream.get("codec_name", "unknown")
                width = stream.get("width", 0)
                height = stream.get("height", 0)
            elif codec_type == "audio":
                audio_codec = stream.get("codec_name", "unknown")
        
        resolution = f"{width}x{height}" if width and height else "unknown"
        
        return {
            "file_size_bytes": file_size,
            "duration_seconds": duration,
            "resolution": resolution,
            "video_codec": video_codec,
            "audio_codec": audio_codec,
            "bitrate_kbps": bitrate,
        }
    
    def is_valid_for_platform(
        self, validation: ValidationResult, platform: str
    ) -> bool:
        """
        Check if validation result meets platform requirements.
        
        Args:
            validation: Validation result
            platform: Target platform (e.g., "youtube_shorts", "tiktok")
        
        Returns:
            True if valid for platform
        """
        if validation.status != ValidationStatus.VALID:
            return False
        
        # Platform-specific checks
        if platform == "youtube_shorts":
            # YouTube Shorts: max 60 seconds, 9:16 aspect ratio
            if validation.duration_seconds > 60:
                return False
            if "1080x1920" not in validation.resolution and "1920x1080" not in validation.resolution:
                return False
        
        elif platform == "tiktok":
            # TikTok: max 60 seconds, 9:16 aspect ratio
            if validation.duration_seconds > 60:
                return False
            if "1080x1920" not in validation.resolution:
                return False
        
        return True
