# src/universal_video_ai/render/video_modifier.py
"""
Video modification processor for copyright avoidance.
Handles flip, border, and half-insert operations using ffmpeg.
"""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


class VideoModifier:
    """Applies video modifications for copyright avoidance."""
    
    def __init__(self, input_path: Path, output_path: Path):
        self.input_path = input_path
        self.output_path = output_path
    
    def apply_flip(self, flip_type: str) -> bool:
        """Apply video flip: 'horizontal', 'vertical', or 'none'."""
        if flip_type == "none":
            return True
        
        logger.info(f"🔄 Applying {flip_type} flip to video")
        
        # Get video info
        probe_cmd = [
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "csv=p=0",
            str(self.input_path)
        ]
        
        try:
            result = subprocess.run(probe_cmd, capture_output=True, text=True, check=True)
            width, height = map(int, result.stdout.strip().split(','))
        except Exception as e:
            logger.error(f"❌ Failed to get video dimensions: {e}")
            return False
        
        # Build ffmpeg filter
        if flip_type == "horizontal":
            filter_str = "hflip"
        elif flip_type == "vertical":
            filter_str = "vflip"
        else:
            logger.warning(f"⚠️ Unknown flip type: {flip_type}")
            return True
        
        cmd = [
            "ffmpeg", "-y",
            "-i", str(self.input_path),
            "-vf", filter_str,
            "-c:a", "copy",
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "23",
            str(self.output_path)
        ]
        
        try:
            subprocess.run(cmd, check=True, capture_output=True)
            logger.info(f"✅ Applied {flip_type} flip successfully")
            return True
        except subprocess.CalledProcessError as e:
            logger.error(f"❌ Failed to apply flip: {e}")
            return False
    
    def apply_border(
        self,
        border_type: str,
        border_color: str = "#000000",
        border_size: int = 50
    ) -> bool:
        """Apply video border: 'left_right', 'top_bottom', or 'none'."""
        if border_type == "none":
            return True
        
        logger.info(f"🖼️ Applying {border_type} border (size={border_size}, color={border_color})")
        
        # Get video info
        probe_cmd = [
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "csv=p=0",
            str(self.input_path)
        ]
        
        try:
            result = subprocess.run(probe_cmd, capture_output=True, text=True, check=True)
            width, height = map(int, result.stdout.strip().split(','))
        except Exception as e:
            logger.error(f"❌ Failed to get video dimensions: {e}")
            return False
        
        # Build ffmpeg filter
        if border_type == "left_right":
            # Add borders on left and right
            new_width = width + 2 * border_size
            filter_str = f"pad={new_width}:{height}:{border_size}:0:{border_color}"
        elif border_type == "top_bottom":
            # Add borders on top and bottom
            new_height = height + 2 * border_size
            filter_str = f"pad={width}:{new_height}:0:{border_size}:{border_color}"
        else:
            logger.warning(f"⚠️ Unknown border type: {border_type}")
            return True
        
        cmd = [
            "ffmpeg", "-y",
            "-i", str(self.input_path),
            "-vf", filter_str,
            "-c:a", "copy",
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "23",
            str(self.output_path)
        ]
        
        try:
            subprocess.run(cmd, check=True, capture_output=True)
            logger.info(f"✅ Applied {border_type} border successfully")
            return True
        except subprocess.CalledProcessError as e:
            logger.error(f"❌ Failed to apply border: {e}")
            return False
    
    def apply_half_insert(
        self,
        insert_type: str,
        insert_side: str,
        content_path: Optional[Path] = None
    ) -> bool:
        """Insert half image or video: 'image' or 'video' on 'left' or 'right'."""
        if insert_type == "none" or not content_path or not content_path.exists():
            return True
        
        logger.info(f"📎 Applying half insert: {insert_type} on {insert_side} from {content_path}")
        
        # Get main video info
        probe_cmd = [
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height,duration",
            "-of", "csv=p=0",
            str(self.input_path)
        ]
        
        try:
            result = subprocess.run(probe_cmd, capture_output=True, text=True, check=True)
            main_width, main_height, main_duration = result.stdout.strip().split(',')
            main_width = int(main_width)
            main_height = int(main_height)
            main_duration = float(main_duration)
        except Exception as e:
            logger.error(f"❌ Failed to get video info: {e}")
            return False
        
        half_width = main_width // 2
        
        if insert_type == "image":
            # Scale image to half width and full height
            filter_str = f"[1:v]scale={half_width}:{main_height}[insert];[0:v][insert]hstack"
            if insert_side == "left":
                filter_str = f"[1:v]scale={half_width}:{main_height}[insert];[insert][0:v]hstack"
            
            cmd = [
                "ffmpeg", "-y",
                "-loop", "1", "-i", str(content_path),
                "-i", str(self.input_path),
                "-vf", filter_str,
                "-c:v", "libx264",
                "-preset", "fast",
                "-crf", "23",
                "-t", str(main_duration),
                "-pix_fmt", "yuv420p",
                str(self.output_path)
            ]
        elif insert_type == "video":
            # Scale video to half width and full height, loop if needed
            filter_str = f"[1:v]scale={half_width}:{main_height},loop=loop=-1:size=1:start=0[insert];[0:v][insert]hstack"
            if insert_side == "left":
                filter_str = f"[1:v]scale={half_width}:{main_height},loop=loop=-1:size=1:start=0[insert];[insert][0:v]hstack"
            
            cmd = [
                "ffmpeg", "-y",
                "-stream_loop", "-1", "-i", str(content_path),
                "-i", str(self.input_path),
                "-vf", filter_str,
                "-c:v", "libx264",
                "-preset", "fast",
                "-crf", "23",
                "-t", str(main_duration),
                "-pix_fmt", "yuv420p",
                "-shortest",
                str(self.output_path)
            ]
        else:
            logger.warning(f"⚠️ Unknown insert type: {insert_type}")
            return True
        
        try:
            subprocess.run(cmd, check=True, capture_output=True)
            logger.info(f"✅ Applied half insert successfully")
            return True
        except subprocess.CalledProcessError as e:
            logger.error(f"❌ Failed to apply half insert: {e}")
            return False
    
    def apply_all(
        self,
        flip: str = "none",
        border: str = "none",
        border_color: str = "#000000",
        border_size: int = 50,
        half_insert_type: str = "none",
        half_insert_side: str = "right",
        half_insert_content: Optional[Path] = None
    ) -> bool:
        """Apply all modifications in sequence."""
        temp_path = self.input_path.with_suffix('.temp.mp4')
        
        # Apply flip first
        if flip != "none":
            modifier = VideoModifier(self.input_path, temp_path)
            if not modifier.apply_flip(flip):
                return False
            self.input_path = temp_path
        
        # Apply border
        if border != "none":
            next_temp = self.input_path.with_suffix('.temp2.mp4')
            modifier = VideoModifier(self.input_path, next_temp)
            if not modifier.apply_border(border, border_color, border_size):
                return False
            self.input_path = next_temp
        
        # Apply half insert
        if half_insert_type != "none" and half_insert_content:
            modifier = VideoModifier(self.input_path, self.output_path)
            if not modifier.apply_half_insert(half_insert_type, half_insert_side, half_insert_content):
                return False
        else:
            # No half insert, just copy to output
            if self.input_path != self.output_path:
                import shutil
                shutil.copy2(self.input_path, self.output_path)
        
        # Cleanup temp files
        for temp in [self.input_path.with_suffix('.temp.mp4'), self.input_path.with_suffix('.temp2.mp4')]:
            if temp.exists():
                temp.unlink()
        
        logger.info("✅ All video modifications applied successfully")
        return True
