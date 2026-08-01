"""
Adapters for integrating Content OS with existing services.

Provides adapters for TTS, subtitle generation, and timeline building
that bridge Content OS workflows with the existing video pipeline.
"""
from typing import Dict, Any, Optional, List
from dataclasses import dataclass
import time
import logging
import subprocess
from pathlib import Path


@dataclass
class TTSSegment:
    """A TTS audio segment."""
    segment_id: str
    text: str
    start_time: float
    end_time: float
    audio_path: str
    voice_id: str
    duration: float


@dataclass
class SubtitleSegment:
    """A subtitle segment."""
    segment_id: str
    text: str
    start_time: float
    end_time: float
    language: str


@dataclass
class TimelineEvent:
    """An event in the video timeline."""
    event_id: str
    start_time: float
    end_time: float
    event_type: str  # "audio", "video", "subtitle", "transition"
    resource_path: str
    metadata: Dict[str, Any]


@dataclass
class Timeline:
    """Complete video timeline."""
    run_id: int
    user_id: int
    events: List[TimelineEvent]
    total_duration: float
    created_at: float


class TTSAdapter:
    """
    Adapter for Text-to-Speech generation.
    
    Bridges Content OS script segments with the existing TTS service.
    """
    
    def __init__(self):
        self.logger = logging.getLogger(f"{self.__class__.__module__}.{self.__class__.__name__}")
    
    def generate_audio(
        self,
        text: str,
        language: str = "vi",
        voice_id: Optional[str] = None,
        output_dir: Optional[Path] = None,
    ) -> Path:
        """
        Generate audio from text using existing TTS service.
        
        Args:
            text: Text to synthesize
            language: Target language
            voice_id: Voice ID to use
            output_dir: Directory for output audio file
        
        Returns:
            Path to generated audio file
        """
        try:
            from universal_video_ai.tts.backend import EdgeTTSBackend
            
            output_dir = output_dir or Path("local_data/content_os/temp")
            output_dir.mkdir(parents=True, exist_ok=True)
            
            output_path = output_dir / f"tts_{int(time.time())}.wav"
            
            # Try to use real TTS service with EdgeTTSBackend
            try:
                tts_backend = EdgeTTSBackend()
                result = tts_backend.synthesize(
                    text=text,
                    language=language,
                    voice=voice_id,
                    output_path=output_path,  # Pass Path object, not string
                )
                self.logger.info(f"TTS generated audio: {result}")
                return output_path
            except Exception as e:
                self.logger.warning(f"Real TTS service failed: {e}, creating silent audio fallback")
                # Fallback: create a silent audio file using FFmpeg
                try:
                    silent_cmd = [
                        "ffmpeg",
                        "-f", "lavfi",
                        "-i", "anullsrc=r=44100:cl=mono",
                        "-t", "5",
                        "-q:a", "9",
                        "-acodec", "pcm_s16le",
                        "-y",
                        str(output_path),
                    ]
                    subprocess.run(silent_cmd, capture_output=True, timeout=30)
                    self.logger.info(f"Created silent audio fallback: {output_path}")
                    return output_path
                except Exception as ffmpeg_error:
                    self.logger.error(f"Failed to create silent audio: {ffmpeg_error}")
                    # Last resort: create empty file
                    output_path.touch()
                    return output_path
                
        except ImportError:
            self.logger.warning("TTS service not available, using fallback")
            output_dir = output_dir or Path("local_data/content_os/temp")
            output_dir.mkdir(parents=True, exist_ok=True)
            output_path = output_dir / f"tts_{int(time.time())}.wav"
            output_path.touch()
            return output_path


class SubtitleAdapter:
    """
    Adapter for subtitle generation.
    
    Bridges Content OS script segments with the existing subtitle service.
    """
    
    def __init__(self):
        self.logger = logging.getLogger(f"{self.__class__.__module__}.{self.__class__.__name__}")
    
    def generate_subtitles(
        self,
        segments: List[Dict[str, Any]],
        duration: float,
        output_dir: Optional[Path] = None,
    ) -> Path:
        """
        Generate subtitles from script segments using existing timeline service.
        
        Args:
            segments: Script segments
            duration: Total duration in seconds
            output_dir: Directory for output subtitle file
        
        Returns:
            Path to generated subtitle file
        """
        try:
            from universal_video_ai.timeline.service import TimelineService, TimelineSegment
            
            output_dir = output_dir or Path("local_data/content_os/temp")
            output_dir.mkdir(parents=True, exist_ok=True)
            
            output_path = output_dir / f"subtitles_{int(time.time())}.srt"
            
            # Convert script segments to TimelineSegment format
            timeline_segments = []
            per_segment = duration / len(segments) if segments else 0
            
            for i, seg in enumerate(segments):
                text = seg.get("text", seg.get("narration", ""))
                # Wrap text to prevent long lines (max 40 chars per line)
                wrapped_lines = []
                words = text.split()
                current_line = ""
                for word in words:
                    if len(current_line + " " + word) <= 40:
                        current_line += " " + word if current_line else word
                    else:
                        if current_line:
                            wrapped_lines.append(current_line)
                        current_line = word
                if current_line:
                    wrapped_lines.append(current_line)
                
                # Limit to 2 lines max
                wrapped_text = "\n".join(wrapped_lines[:2])
                
                start = i * per_segment
                end = (i + 1) * per_segment
                timeline_segments.append(TimelineSegment(start, end, wrapped_text))
            
            # Use TimelineService to generate SRT
            timeline_service = TimelineService()
            srt_content = timeline_service.generate_srt(timeline_segments)
            
            # Write to file
            output_path.write_text(srt_content, encoding='utf-8')
            
            self.logger.info(f"Subtitles generated: {output_path}")
            return output_path
            
        except ImportError:
            self.logger.warning("Timeline service not available, using fallback")
            output_dir = output_dir or Path("local_data/content_os/temp")
            output_dir.mkdir(parents=True, exist_ok=True)
            output_path = output_dir / f"subtitles_{int(time.time())}.srt"
            output_path.touch()
            return output_path


class TimelineAdapter:
    """
    Adapter for timeline building.
    
    Combines audio, video, and subtitle elements into a complete timeline.
    """
    
    def __init__(self):
        self.logger = logging.getLogger(f"{self.__class__.__module__}.{self.__class__.__name__}")
    
    def build_timeline(
        self,
        script: Dict[str, Any],
        voice_manifest: Dict[str, Any],
        subtitle_manifest: Dict[str, Any],
        assets: Dict[str, Any],
        target_platform: str,
        target_duration: float,
    ) -> Dict[str, Any]:
        """
        Build a complete video timeline.
        
        Args:
            script: Script with segments
            voice_manifest: Voice generation manifest
            subtitle_manifest: Subtitle generation manifest
            assets: Resolved assets
            target_platform: Target platform
            target_duration: Target duration in seconds
        
        Returns:
            Timeline dictionary
        """
        try:
            from universal_video_ai.timeline.service import TimelineService, TimelineSegment
            
            segments = script.get("segments", [])
            timeline_segments = []
            
            per_segment = target_duration / len(segments) if segments else 0
            
            for i, seg in enumerate(segments):
                text = seg.get("text", seg.get("narration", ""))
                start = i * per_segment
                end = (i + 1) * per_segment
                timeline_segments.append(TimelineSegment(start, end, text))
            
            timeline_service = TimelineService()
            
            timeline = {
                "duration_seconds": target_duration,
                "resolution": self._get_resolution_for_platform(target_platform),
                "video_tracks": [],
                "audio_tracks": [
                    {
                        "track_id": "voice",
                        "source": voice_manifest.get("audio_path"),
                        "duration": voice_manifest.get("duration_seconds", target_duration),
                    }
                ],
                "subtitle_tracks": [
                    {
                        "track_id": "subtitles",
                        "source": subtitle_manifest.get("subtitle_path"),
                        "language": script.get("language", "en"),
                    }
                ],
                "segments": [
                    {
                        "start": seg.start_time,
                        "end": seg.end_time,
                        "text": seg.text,
                    }
                    for seg in timeline_segments
                ],
                "assets": assets.get("assets", []),
            }
            
            self.logger.info(f"Timeline built for {target_platform}: {target_duration}s")
            return timeline
            
        except ImportError:
            self.logger.warning("Timeline service not available, using fallback")
            return {
                "duration_seconds": target_duration,
                "resolution": self._get_resolution_for_platform(target_platform),
                "video_tracks": [],
                "audio_tracks": [],
                "subtitle_tracks": [],
                "segments": [],
                "assets": assets.get("assets", []),
            }
    
    def _get_resolution_for_platform(self, platform: str) -> str:
        """Get resolution for target platform."""
        resolutions = {
            "youtube_shorts": "1080x1920",
            "facebook_reels": "1080x1920",
            "tiktok": "1080x1920",
            "youtube_landscape": "1920x1080",
            "instagram_reels": "1080x1920",
        }
        return resolutions.get(platform, "1080x1920")
