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
            # Extract audio, subtitle, and duration from metadata
            audio_path = job.metadata.get("audio_path")
            subtitle_path = job.metadata.get("subtitle_path")
            duration = job.metadata.get("duration", 5.0)
            resolution = job.metadata.get("resolution", "1080x1920")
            
            # Create video with actual audio and subtitles
            self._create_simple_video(
                output_path=job.output_path,
                resolution=resolution,
                audio_path=audio_path,
                subtitle_path=subtitle_path,
                duration=duration,
                assets=job.metadata.get("assets"),
            )
            
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
    
    def _create_simple_video(self, output_path: str, resolution: str, audio_path: Optional[str] = None, subtitle_path: Optional[str] = None, duration: float = 5.0, assets: Optional[Dict[str, Any]] = None) -> None:
        """
        Create a video with audio and subtitles using FFmpeg.
        
        This implementation uses the actual audio and subtitle files generated
        by the production pipeline instead of placeholder content.
        """
        width, height = resolution.split("x")
        output_path_obj = Path(output_path).resolve()
        output_path_obj.parent.mkdir(parents=True, exist_ok=True)
        
        # Check if we have generated assets to use
        asset_media = []
        if assets and assets.get("assets"):
            for asset in assets["assets"]:
                local_path = asset.get("local_path")
                if local_path and Path(local_path).exists():
                    path_obj = Path(local_path).resolve()
                    declared_type = str(asset.get("asset_type") or "").lower()
                    media_type = "video" if declared_type == "video" or path_obj.suffix.lower() in {".mp4", ".mov", ".webm", ".mkv"} else "image"
                    asset_media.append({
                        "path": str(path_obj),
                        "duration": float(asset.get("duration_seconds") or 0.0),
                        "scene_id": asset.get("scene_id"),
                        "media_type": media_type,
                        "motion": str(asset.get("motion") or "slow_zoom_in"),
                        "transition": str(asset.get("transition") or "fade"),
                    })

        # Build FFmpeg command
        if asset_media:
            cmd = ["ffmpeg"]
            default_scene_duration = max(0.1, duration / len(asset_media))
            total_asset_duration = sum(asset["duration"] or default_scene_duration for asset in asset_media)
            duration_scale = (duration / total_asset_duration) if total_asset_duration > 0 and duration > 0 else 1.0
            self.logger.info(
                "Rendering %d visual scenes over %.2fs (asset planned %.2fs, scale %.3f): %s",
                len(asset_media),
                duration,
                total_asset_duration,
                duration_scale,
                ", ".join(str(asset.get("scene_id") or index) for index, asset in enumerate(asset_media, start=1)),
            )
            for asset in asset_media:
                scene_duration = max(0.1, (asset["duration"] or default_scene_duration) * duration_scale)
                asset["render_duration"] = scene_duration
                if asset["media_type"] == "video":
                    cmd.extend(["-stream_loop", "-1", "-t", f"{scene_duration:.3f}", "-i", asset["path"]])
                else:
                    cmd.extend(["-loop", "1", "-framerate", "30", "-t", f"{scene_duration:.3f}", "-i", asset["path"]])
        else:
            cmd = [
                "ffmpeg",
                "-f", "lavfi",
                "-i", f"color=c=#1a1a2e:s={resolution}:d={duration}:r=30",
            ]

        # Add audio if available and valid
        audio_index = None
        if audio_path and Path(audio_path).exists() and Path(audio_path).stat().st_size > 0:
            cmd.extend(["-i", str(Path(audio_path).resolve())])
            audio_index = len(asset_media) if asset_media else 1
        else:
            self.logger.warning(f"Audio file not valid or not found: {audio_path}, creating video without audio")

        subtitle_filter = None
        ffmpeg_cwd = None
        if subtitle_path and Path(subtitle_path).exists() and Path(subtitle_path).stat().st_size > 0:
            # libass treats backslashes in Windows paths as escapes. Run from
            # the subtitle directory and pass only the file name to the filter.
            subtitle_path_obj = Path(subtitle_path).resolve()
            ffmpeg_cwd = str(subtitle_path_obj.parent)
            if subtitle_path_obj.suffix.lower() == ".ass":
                subtitle_filter = f"subtitles=filename={subtitle_path_obj.name}"
            else:
                subtitle_filter = (
                    "subtitles="
                    f"filename={subtitle_path_obj.name}:"
                    "force_style='Fontsize=18,PrimaryColour=&HFFFFFF&,BackColour=&H80000000&,BorderStyle=4,Alignment=2,MarginV=30'"
                )

        video_filters = []
        if asset_media:
            for index, asset in enumerate(asset_media):
                scene_duration = float(asset.get("render_duration") or default_scene_duration)
                frames = max(1, round(scene_duration * 30))
                if asset["media_type"] == "video":
                    chain = (
                        f"[{index}:v]trim=duration={scene_duration:.3f},setpts=PTS-STARTPTS,"
                        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
                        f"crop={width}:{height},fps=30,setsar=1,format=yuv420p,"
                        f"fade=t=in:st=0:d={min(0.25, scene_duration/4):.3f},"
                        f"fade=t=out:st={max(0.0, scene_duration-min(0.25, scene_duration/4)):.3f}:d={min(0.25, scene_duration/4):.3f}[v{index}]"
                    )
                else:
                    motion = asset.get("motion", "slow_zoom_in")
                    if motion == "slow_pan_left":
                        zexpr, xexpr, yexpr = "1.08", f"(iw-iw/zoom)*(1-on/{frames})", "(ih-ih/zoom)/2"
                    elif motion == "slow_pan_right":
                        zexpr, xexpr, yexpr = "1.08", f"(iw-iw/zoom)*(on/{frames})", "(ih-ih/zoom)/2"
                    elif motion == "gentle_push_in":
                        zexpr, xexpr, yexpr = f"min(1.0+0.10*on/{frames},1.10)", "(iw-iw/zoom)/2", "(ih-ih/zoom)/2"
                    else:
                        zexpr, xexpr, yexpr = f"min(1.0+0.07*on/{frames},1.07)", "(iw-iw/zoom)/2", "(ih-ih/zoom)/2"
                    chain = (
                        f"[{index}:v]scale={width}:{height}:force_original_aspect_ratio=increase,"
                        f"crop={width}:{height},zoompan=z='{zexpr}':x='{xexpr}':y='{yexpr}':"
                        f"d={frames}:s={width}x{height}:fps=30,setsar=1,format=yuv420p,"
                        f"fade=t=in:st=0:d={min(0.25, scene_duration/4):.3f},"
                        f"fade=t=out:st={max(0.0, scene_duration-min(0.25, scene_duration/4)):.3f}:d={min(0.25, scene_duration/4):.3f}[v{index}]"
                    )
                video_filters.append(chain)
            video_filters.append(
                "".join(f"[v{index}]" for index in range(len(asset_media)))
                + f"concat=n={len(asset_media)}:v=1:a=0[basev]"
            )
        else:
            video_filters.append("[0:v]format=yuv420p[basev]")

        if subtitle_filter:
            video_filters.append(f"[basev]{subtitle_filter}[vout]")
            video_map = "[vout]"
        else:
            video_map = "[basev]"

        if video_filters:
            cmd.extend(["-filter_complex", ";".join(video_filters)])

        cmd.extend(["-map", video_map])
        if audio_index is not None:
            cmd.extend(["-map", f"{audio_index}:a"])

        # Encoding options
        cmd.extend([
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "23",
            "-r", "30",
            "-pix_fmt", "yuv420p",
        ])
        if audio_index is not None:
            cmd.extend(["-c:a", "aac", "-af", "apad"])
        cmd.extend(["-t", f"{duration:.3f}"])
        cmd.extend([
            "-y",
            str(output_path_obj),
        ])

        self.logger.debug(f"Running FFmpeg command: {' '.join(cmd)}")

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
            cwd=ffmpeg_cwd,
        )

        if result.returncode != 0:
            self.logger.error(f"FFmpeg stderr: {result.stderr}")
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