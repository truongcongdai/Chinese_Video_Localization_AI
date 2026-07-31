"""
Adapters for integrating Content OS with existing services.

Provides adapters for TTS, subtitle generation, and timeline building
that bridge Content OS workflows with the existing video pipeline.
"""
from typing import Dict, Any, Optional, List
from dataclasses import dataclass
import time


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
    
    def __init__(self, repository):
        self.repository = repository
    
    def generate_audio(
        self,
        run_id: int,
        user_id: int,
        script_segments: List[Dict[str, Any]],
        voice_id: str,
        target_language: str,
    ) -> List[TTSSegment]:
        """
        Generate audio from script segments.
        
        Args:
            run_id: Run ID
            user_id: User ID
            script_segments: Script segments from the generated script
            voice_id: Voice profile ID to use
            target_language: Target language for TTS
        
        Returns:
            List of TTS segments with audio paths
        """
        segments = []
        
        for i, seg in enumerate(script_segments):
            segment = TTSSegment(
                segment_id=f"tts_{i}",
                text=seg.get("narration", ""),
                start_time=seg.get("start_second", 0.0),
                end_time=seg.get("end_second", 0.0),
                audio_path=f"/audio/{run_id}_seg_{i}.wav",
                voice_id=voice_id,
                duration=seg.get("end_second", 0.0) - seg.get("start_second", 0.0),
            )
            segments.append(segment)
        
        # Store as artifact
        self._store_tts_segments(run_id, user_id, segments)
        
        return segments
    
    def _store_tts_segments(
        self, run_id: int, user_id: int, segments: List[TTSSegment]
    ):
        """Store TTS segments as artifact."""
        data = {
            "run_id": run_id,
            "user_id": user_id,
            "segments": [
                {
                    "segment_id": s.segment_id,
                    "text": s.text,
                    "start_time": s.start_time,
                    "end_time": s.end_time,
                    "audio_path": s.audio_path,
                    "voice_id": s.voice_id,
                    "duration": s.duration,
                }
                for s in segments
            ],
            "created_at": time.time(),
        }
        
        self.repository.create_artifact(
            run_id=run_id,
            user_id=user_id,
            artifact_type="tts_segments",
            version=1,
            schema_version="1.0",
            path=f"/tts/{run_id}.json",
            checksum="",
            metadata=data,
            created_by_agent="TTSAdapter",
        )


class SubtitleAdapter:
    """
    Adapter for subtitle generation.
    
    Bridges Content OS script segments with the existing subtitle service.
    """
    
    def __init__(self, repository):
        self.repository = repository
    
    def generate_subtitles(
        self,
        run_id: int,
        user_id: int,
        script_segments: List[Dict[str, Any]],
        target_language: str,
        subtitle_style_id: str,
    ) -> List[SubtitleSegment]:
        """
        Generate subtitles from script segments.
        
        Args:
            run_id: Run ID
            user_id: User ID
            script_segments: Script segments from the generated script
            target_language: Target language for subtitles
            subtitle_style_id: Subtitle style profile ID
        
        Returns:
            List of subtitle segments
        """
        segments = []
        
        for i, seg in enumerate(script_segments):
            segment = SubtitleSegment(
                segment_id=f"sub_{i}",
                text=seg.get("subtitle_text", seg.get("narration", "")),
                start_time=seg.get("start_second", 0.0),
                end_time=seg.get("end_second", 0.0),
                language=target_language,
            )
            segments.append(segment)
        
        # Store as artifact
        self._store_subtitle_segments(run_id, user_id, segments)
        
        return segments
    
    def _store_subtitle_segments(
        self, run_id: int, user_id: int, segments: List[SubtitleSegment]
    ):
        """Store subtitle segments as artifact."""
        data = {
            "run_id": run_id,
            "user_id": user_id,
            "segments": [
                {
                    "segment_id": s.segment_id,
                    "text": s.text,
                    "start_time": s.start_time,
                    "end_time": s.end_time,
                    "language": s.language,
                }
                for s in segments
            ],
            "created_at": time.time(),
        }
        
        self.repository.create_artifact(
            run_id=run_id,
            user_id=user_id,
            artifact_type="subtitle_segments",
            version=1,
            schema_version="1.0",
            path=f"/subtitles/{run_id}.json",
            checksum="",
            metadata=data,
            created_by_agent="SubtitleAdapter",
        )


class TimelineAdapter:
    """
    Adapter for timeline building.
    
    Combines audio, video, and subtitle elements into a complete timeline.
    """
    
    def __init__(self, repository):
        self.repository = repository
    
    def build_timeline(
        self,
        run_id: int,
        user_id: int,
        storyboard_scenes: List[Dict[str, Any]],
        tts_segments: List[TTSSegment],
        subtitle_segments: List[SubtitleSegment],
        asset_manifest: Dict[str, Any],
    ) -> Timeline:
        """
        Build a complete video timeline.
        
        Args:
            run_id: Run ID
            user_id: User ID
            storyboard_scenes: Storyboard scenes
            tts_segments: TTS audio segments
            subtitle_segments: Subtitle segments
            asset_manifest: Asset manifest with video/image assets
        
        Returns:
            Complete timeline
        """
        events = []
        
        # Add audio events from TTS
        for tts in tts_segments:
            event = TimelineEvent(
                event_id=f"audio_{tts.segment_id}",
                start_time=tts.start_time,
                end_time=tts.end_time,
                event_type="audio",
                resource_path=tts.audio_path,
                metadata={"voice_id": tts.voice_id},
            )
            events.append(event)
        
        # Add video/image events from storyboard and assets
        for i, scene in enumerate(storyboard_scenes):
            # Try to get asset from manifest
            asset_path = f"/assets/{run_id}_scene_{i}.mp4"
            
            event = TimelineEvent(
                event_id=f"video_scene_{i}",
                start_time=scene.get("start_second", 0.0),
                end_time=scene.get("end_second", 0.0),
                event_type="video",
                resource_path=asset_path,
                metadata={
                    "visual_instruction": scene.get("visual_instruction", ""),
                    "camera_angle": scene.get("camera_angle", "front"),
                    "transition": scene.get("transition", "cut"),
                },
            )
            events.append(event)
        
        # Add subtitle events
        for sub in subtitle_segments:
            event = TimelineEvent(
                event_id=f"subtitle_{sub.segment_id}",
                start_time=sub.start_time,
                end_time=sub.end_time,
                event_type="subtitle",
                resource_path="",  # Subtitles are embedded
                metadata={"text": sub.text, "language": sub.language},
            )
            events.append(event)
        
        # Sort events by start time
        events.sort(key=lambda e: e.start_time)
        
        # Calculate total duration
        total_duration = max((e.end_time for e in events), default=0.0)
        
        timeline = Timeline(
            run_id=run_id,
            user_id=user_id,
            events=events,
            total_duration=total_duration,
            created_at=time.time(),
        )
        
        # Store as artifact
        self._store_timeline(timeline)
        
        return timeline
    
    def _store_timeline(self, timeline: Timeline):
        """Store timeline as artifact."""
        data = {
            "run_id": timeline.run_id,
            "user_id": timeline.user_id,
            "events": [
                {
                    "event_id": e.event_id,
                    "start_time": e.start_time,
                    "end_time": e.end_time,
                    "event_type": e.event_type,
                    "resource_path": e.resource_path,
                    "metadata": e.metadata,
                }
                for e in timeline.events
            ],
            "total_duration": timeline.total_duration,
            "created_at": timeline.created_at,
        }
        
        self.repository.create_artifact(
            run_id=timeline.run_id,
            user_id=timeline.user_id,
            artifact_type="timeline",
            version=1,
            schema_version="1.0",
            path=f"/timelines/{timeline.run_id}.json",
            checksum="",
            metadata=data,
            created_by_agent="TimelineAdapter",
        )
