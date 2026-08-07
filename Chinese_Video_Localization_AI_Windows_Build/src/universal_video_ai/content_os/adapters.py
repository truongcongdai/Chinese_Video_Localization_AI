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
import re
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
                chunks = self._split_tts_chunks(text)
                if len(chunks) == 1:
                    result = tts_backend.synthesize(
                        text=chunks[0],
                        language=language,
                        voice=voice_id,
                        output_path=output_path,
                    )
                else:
                    chunk_paths = []
                    for index, chunk in enumerate(chunks, start=1):
                        chunk_path = output_dir / f"{output_path.stem}_part{index:02d}.wav"
                        tts_backend.synthesize(
                            text=chunk,
                            language=language,
                            voice=voice_id,
                            output_path=chunk_path,
                        )
                        chunk_paths.append(chunk_path)
                    self._concat_audio_chunks(chunk_paths, output_path)
                    result = output_path
                self.logger.info(f"TTS generated audio: {result}")
                return output_path
            except Exception as e:
                self.logger.warning(f"Real TTS service failed: {e}, creating silent audio fallback")
                # Fallback: create a silent audio file using FFmpeg
                try:
                    estimated_duration = self._estimate_speech_duration(text)
                    silent_cmd = [
                        "ffmpeg",
                        "-f", "lavfi",
                        "-i", "anullsrc=r=44100:cl=mono",
                        "-t", f"{estimated_duration:.3f}",
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

    def _split_tts_chunks(self, text: str, max_chars: int = 220) -> List[str]:
        """Split long narration into smaller TTS requests to avoid provider timeouts."""
        normalized = re.sub(r"\s+", " ", text or "").strip()
        if not normalized:
            return [""]
        sentences = [part.strip() for part in re.split(r"(?<=[.!?。！？])\s+", normalized) if part.strip()]
        if not sentences:
            sentences = [normalized]
        chunks: List[str] = []
        current = ""
        for sentence in sentences:
            if len(sentence) > max_chars:
                for word in sentence.split():
                    candidate = f"{current} {word}".strip()
                    if current and len(candidate) > max_chars:
                        chunks.append(current)
                        current = word
                    else:
                        current = candidate
                continue
            candidate = f"{current} {sentence}".strip()
            if current and len(candidate) > max_chars:
                chunks.append(current)
                current = sentence
            else:
                current = candidate
        if current:
            chunks.append(current)
        return chunks or [normalized]

    def _concat_audio_chunks(self, chunk_paths: List[Path], output_path: Path) -> None:
        """Concatenate synthesized TTS chunks into one WAV file."""
        if len(chunk_paths) == 1:
            chunk_paths[0].replace(output_path)
            return
        list_path = output_path.with_suffix(".concat.txt")
        lines = []
        for path in chunk_paths:
            escaped = str(path.resolve()).replace("'", "'\\''")
            lines.append(f"file '{escaped}'")
        list_path.write_text("\n".join(lines), encoding="utf-8")
        cmd = [
            "ffmpeg",
            "-f", "concat",
            "-safe", "0",
            "-i", str(list_path),
            "-c", "copy",
            "-y",
            str(output_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0 or not output_path.exists() or output_path.stat().st_size == 0:
            cmd = [
                "ffmpeg",
                "-f", "concat",
                "-safe", "0",
                "-i", str(list_path),
                "-acodec", "pcm_s16le",
                "-ar", "24000",
                "-ac", "1",
                "-y",
                str(output_path),
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            if result.returncode != 0:
                raise RuntimeError(f"Failed to concat TTS chunks: {result.stderr}")

    def _estimate_speech_duration(self, text: str) -> float:
        # Vietnamese TTS is commonly around 11-14 chars/sec for short-form narration.
        char_count = len(re.sub(r"\s+", "", text or ""))
        return max(5.0, min(90.0, char_count / 12.0))


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
            from universal_video_ai.timeline.service import (
                TimelineService,
                TimelineSegment,
                _balanced_caption_chunks,
            )
            
            output_dir = output_dir or Path("local_data/content_os/temp")
            output_dir.mkdir(parents=True, exist_ok=True)
            
            output_path = output_dir / f"subtitles_{int(time.time())}.ass"
            
            timeline_segments = []
            per_segment = duration / len(segments) if segments else 0
            planned_end = max(
                [
                    float(seg.get("end_second") or 0.0)
                    for seg in segments
                    if isinstance(seg, dict)
                ]
                or [duration]
            )
            time_scale = (duration / planned_end) if planned_end > 0 and duration > 0 else 1.0
            
            for i, seg in enumerate(segments):
                # Burn in the same narration text that TTS reads. Short caption fields
                # are useful for UI previews, but using them here makes voice/subtitle
                # appear out of sync.
                text = seg.get("narration") or seg.get("text") or seg.get("subtitle_text") or ""
                chunks = _balanced_caption_chunks(text, max_chars=104, line_chars=42) or [text]
                start = float(seg.get("start_second", i * per_segment) or i * per_segment) * time_scale
                end = float(seg.get("end_second", (i + 1) * per_segment) or (i + 1) * per_segment) * time_scale
                if end <= start:
                    end = start + per_segment
                weights = [max(1, len("".join(chunk.split()))) for chunk in chunks]
                total_weight = sum(weights) or 1
                elapsed = 0
                for chunk, weight in zip(chunks, weights):
                    chunk_start = start + (end - start) * elapsed / total_weight
                    elapsed += weight
                    chunk_end = start + (end - start) * elapsed / total_weight
                    timeline_segments.append(TimelineSegment(chunk_start, chunk_end, chunk))
            
            timeline_service = TimelineService()
            ass_content = timeline_service.generate_ass_karaoke(
                timeline_segments,
                frame_width=1080,
                frame_height=1920,
                font_size=34,
            )
            output_path.write_text(ass_content, encoding='utf-8')
            
            self.logger.info(f"Subtitles generated: {output_path}")
            return output_path
            
        except ImportError:
            self.logger.warning("Timeline service not available, using fallback")
            output_dir = output_dir or Path("local_data/content_os/temp")
            output_dir.mkdir(parents=True, exist_ok=True)
            output_path = output_dir / f"subtitles_{int(time.time())}.ass"
            output_path.touch()
            return output_path

    def _split_tts_chunks(self, text: str, max_chars: int = 220) -> List[str]:
        """Split long narration into smaller TTS requests to avoid provider timeouts."""
        normalized = re.sub(r"\s+", " ", text or "").strip()
        if not normalized:
            return [""]
        sentences = [part.strip() for part in re.split(r"(?<=[.!?。！？])\s+", normalized) if part.strip()]
        if not sentences:
            sentences = [normalized]
        chunks: List[str] = []
        current = ""
        for sentence in sentences:
            if len(sentence) > max_chars:
                words = sentence.split()
                for word in words:
                    candidate = f"{current} {word}".strip()
                    if current and len(candidate) > max_chars:
                        chunks.append(current)
                        current = word
                    else:
                        current = candidate
                continue
            candidate = f"{current} {sentence}".strip()
            if current and len(candidate) > max_chars:
                chunks.append(current)
                current = sentence
            else:
                current = candidate
        if current:
            chunks.append(current)
        return chunks or [normalized]

    def _concat_audio_chunks(self, chunk_paths: List[Path], output_path: Path) -> None:
        if len(chunk_paths) == 1:
            chunk_paths[0].replace(output_path)
            return
        list_path = output_path.with_suffix(".concat.txt")
        lines = []
        for path in chunk_paths:
            escaped = str(path.resolve()).replace("'", "'\\''")
            lines.append(f"file '{escaped}'")
        list_path.write_text("\n".join(lines), encoding="utf-8")
        cmd = [
            "ffmpeg",
            "-f", "concat",
            "-safe", "0",
            "-i", str(list_path),
            "-c", "copy",
            "-y",
            str(output_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0 or not output_path.exists() or output_path.stat().st_size == 0:
            cmd = [
                "ffmpeg",
                "-f", "concat",
                "-safe", "0",
                "-i", str(list_path),
                "-acodec", "pcm_s16le",
                "-ar", "24000",
                "-ac", "1",
                "-y",
                str(output_path),
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            if result.returncode != 0:
                raise RuntimeError(f"Failed to concat TTS chunks: {result.stderr}")

    def _estimate_speech_duration(self, text: str) -> float:
        # Vietnamese TTS is commonly around 11-14 chars/sec for short-form narration.
        char_count = len(re.sub(r"\s+", "", text or ""))
        return max(5.0, min(90.0, char_count / 12.0))


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
            
            audio_duration = float(voice_manifest.get("duration_seconds") or 0.0) if isinstance(voice_manifest, dict) else 0.0
            effective_duration = audio_duration if audio_duration > 0 else target_duration
            planned_end = max(
                [
                    float(seg.get("end_second") or 0.0)
                    for seg in segments
                    if isinstance(seg, dict)
                ]
                or [target_duration]
            )
            time_scale = (effective_duration / planned_end) if planned_end > 0 and effective_duration > 0 else 1.0
            per_segment = effective_duration / len(segments) if segments else 0
            
            for i, seg in enumerate(segments):
                text = seg.get("narration") or seg.get("text") or seg.get("subtitle_text") or ""
                start = float(seg.get("start_second", i * per_segment) or i * per_segment) * time_scale
                end = float(seg.get("end_second", (i + 1) * per_segment) or (i + 1) * per_segment) * time_scale
                if end <= start:
                    end = start + per_segment
                timeline_segments.append(TimelineSegment(start, end, text))
            
            timeline_service = TimelineService()
            
            timeline = {
                "duration_seconds": effective_duration,
                "total_duration": effective_duration,
                "planned_duration_seconds": target_duration,
                "time_scale": time_scale,
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
            
            self.logger.info(f"Timeline built for {target_platform}: {effective_duration:.2f}s (planned {target_duration}s, scale {time_scale:.3f})")
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
