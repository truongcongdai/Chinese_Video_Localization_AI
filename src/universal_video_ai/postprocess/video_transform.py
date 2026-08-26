"""Video transformation module for flip, border, and split-screen effects."""
import logging
import subprocess
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional, Tuple

_logger = logging.getLogger(__name__)


class FlipMode(Enum):
    """Video flip modes."""
    NONE = "none"
    HORIZONTAL = "hflip"
    VERTICAL = "vflip"
    BOTH = "hflip,vflip"


class BorderPosition(Enum):
    """Border position for letterbox/pillarbox."""
    NONE = "none"
    TOP_BOTTOM = "top_bottom"
    LEFT_RIGHT = "left_right"


@dataclass
class TransformConfig:
    """Configuration for video transformations."""
    # Optional delivery canvas. Content is fitted without stretching and
    # padded into this frame, preserving subtitles and logos.
    target_width: Optional[int] = None
    target_height: Optional[int] = None

    # Flip settings
    enable_flip: bool = False
    flip_mode: FlipMode = FlipMode.HORIZONTAL
    
    # Border settings
    enable_border: bool = False
    border_position: BorderPosition = BorderPosition.TOP_BOTTOM
    border_px: int = 60
    border_color: str = "black"
    
    # Split-screen settings
    enable_split_screen: bool = False
    overlay_image_path: Optional[Path] = None
    split_mode: str = "vertical"  # vertical (top/bottom) or horizontal (left/right)
    
    # Randomization for anti-detection
    enable_randomization: bool = False
    crop_percent: float = 0.0  # 0-2% random crop
    speed_factor: float = 1.0  # 0.98-1.02 random speed
    brightness_adjust: float = 0.0  # -3 to +3%
    contrast_adjust: float = 0.0  # -3 to +3%


class VideoTransformer:
    """Applies video transformations using FFmpeg."""
    
    def __init__(self, config: TransformConfig, logger: Optional[logging.Logger] = None):
        self.config = config
        self.logger = logger or _logger
    
    def transform(self, input_path: Path, output_path: Path) -> bool:
        """Apply all configured transformations to the video."""
        if not input_path.exists():
            self.logger.error(f"Input video not found: {input_path}")
            return False
        
        # Build FFmpeg filter chain
        filters = self._build_filter_chain()
        
        split_ready = bool(self.config.enable_split_screen and self.config.overlay_image_path)
        if not filters and not split_ready:
            # No transformations needed, just copy
            self.logger.info("No transformations configured, copying video directly")
            self._copy_video(input_path, output_path)
            return True
        
        # Apply transformations
        return self._apply_ffmpeg_filters(input_path, output_path, filters)
    
    def _build_filter_chain(self) -> str:
        """Build FFmpeg filter chain from config."""
        filters = []

        if self.config.target_width and self.config.target_height:
            width = max(2, int(self.config.target_width) // 2 * 2)
            height = max(2, int(self.config.target_height) // 2 * 2)
            filters.extend([
                f"scale={width}:{height}:force_original_aspect_ratio=decrease",
                f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black",
                "setsar=1",
            ])
        
        # Flip
        if self.config.enable_flip and self.config.flip_mode != FlipMode.NONE:
            filters.append(self.config.flip_mode.value)
        
        # Border (letterbox/pillarbox)
        if self.config.enable_border and self.config.border_position != BorderPosition.NONE:
            border_filter = self._build_border_filter()
            if border_filter:
                filters.append(border_filter)
        
        # Randomization
        if self.config.enable_randomization:
            random_filters = self._build_randomization_filters()
            filters.extend(random_filters)
        
        return ",".join(filters) if filters else ""
    
    def _build_border_filter(self) -> str:
        """Build FFmpeg pad filter for borders."""
        if self.config.border_position == BorderPosition.TOP_BOTTOM:
            # Add border on top and bottom
            return f"pad=width=iw:height=ih+{self.config.border_px * 2}:x=0:y={self.config.border_px}:color={self.config.border_color}"
        elif self.config.border_position == BorderPosition.LEFT_RIGHT:
            # Add border on left and right
            return f"pad=width=iw+{self.config.border_px * 2}:height=ih:x={self.config.border_px}:y=0:color={self.config.border_color}"
        return ""
    
    def _build_randomization_filters(self) -> list:
        """Build filters for randomization effects."""
        filters = []
        
        # Crop
        if self.config.crop_percent > 0:
            crop_w = f"iw*{(100 - self.config.crop_percent) / 100}"
            crop_h = f"ih*{(100 - self.config.crop_percent) / 100}"
            crop_x = f"(iw-iw*{(100 - self.config.crop_percent) / 100})/2"
            crop_y = f"(ih-ih*{(100 - self.config.crop_percent) / 100})/2"
            filters.append(f"crop={crop_w}:{crop_h}:{crop_x}:{crop_y}")
        
        # Speed (setpts)
        if self.config.speed_factor != 1.0:
            filters.append(f"setpts={1.0/self.config.speed_factor}*PTS")
        
        # Brightness
        if self.config.brightness_adjust != 0:
            filters.append(f"eq=brightness={self.config.brightness_adjust/100}")
        
        # Contrast
        if self.config.contrast_adjust != 0:
            filters.append(f"eq=contrast={1.0 + self.config.contrast_adjust/100}")
        
        return filters
    
    def _apply_ffmpeg_filters(self, input_path: Path, output_path: Path, filters: str) -> bool:
        """Apply FFmpeg filters to video."""
        if self.config.enable_split_screen and self.config.overlay_image_path:
            image_path = self.config.overlay_image_path
            if not image_path.exists():
                self.logger.error("Split-screen image not found: %s", image_path)
                return False
            width = self.config.target_width
            height = self.config.target_height
            if not width or not height:
                width, height = self._probe_dimensions(input_path)
            width = max(4, int(width) // 2 * 2)
            height = max(4, int(height) // 2 * 2)
            base_prefix = f"{filters}," if filters else ""
            if self.config.split_mode == "vertical":
                pane_w, pane_h, stack = width // 2, height, "hstack=inputs=2"
            else:
                pane_w, pane_h, stack = width, height // 2, "vstack=inputs=2"
            fit = (
                f"scale={pane_w}:{pane_h}:force_original_aspect_ratio=decrease,"
                f"pad={pane_w}:{pane_h}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1"
            )
            complex_filter = f"[0:v]{base_prefix}{fit}[main];[1:v]{fit}[still];[main][still]{stack}[out]"
            cmd = [
                "ffmpeg", "-y", "-i", str(input_path), "-loop", "1", "-i", str(image_path),
                "-filter_complex", complex_filter,
                "-map", "[out]", "-map", "0:a?", "-shortest",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "copy",
                str(output_path),
            ]
        else:
            cmd = [
                "ffmpeg", "-i", str(input_path), "-vf", filters,
                "-c:a", "copy", "-y", str(output_path),
            ]
        
        self.logger.info(f"Applying FFmpeg filters: {filters}")
        self.logger.debug(f"FFmpeg command: {' '.join(cmd)}")
        
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300  # 5 minute timeout
            )
            
            if result.returncode != 0:
                self.logger.error(f"FFmpeg failed: {result.stderr}")
                return False
            
            if output_path.exists():
                self.logger.info(f"Video transformed successfully: {output_path}")
                return True
            else:
                self.logger.error(f"Output file not created: {output_path}")
                return False
                
        except subprocess.TimeoutExpired:
            self.logger.error("FFmpeg transformation timed out")
            return False
        except Exception as e:
            self.logger.error(f"FFmpeg transformation failed: {e}")
            return False

    @staticmethod
    def _probe_dimensions(input_path: Path) -> Tuple[int, int]:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error", "-select_streams", "v:0",
                "-show_entries", "stream=width,height", "-of", "csv=s=x:p=0",
                str(input_path),
            ],
            capture_output=True, text=True, timeout=30, check=True,
        )
        width, height = result.stdout.strip().split("x", 1)
        return int(width), int(height)
    
    def _copy_video(self, input_path: Path, output_path: Path) -> bool:
        """Copy video without transformations."""
        cmd = [
            "ffmpeg",
            "-i", str(input_path),
            "-c", "copy",
            "-y",
            str(output_path),
        ]
        
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300
            )
            
            if result.returncode != 0:
                self.logger.error(f"Video copy failed: {result.stderr}")
                return False
            
            return output_path.exists()
            
        except(Exception) as e:
            self.logger.error(f"Video copy failed: {e}")
            return False
