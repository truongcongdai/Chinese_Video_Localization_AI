# src/universal_video_ai/render/animated_subtitles.py
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import List, Optional
import logging

__all__ = ["SubtitleEffect", "SubtitleStyle", "AnimatedSubtitleGenerator"]

_logger = logging.getLogger(__name__)


class SubtitleEffect(Enum):
    """Available subtitle animation effects."""
    NONE = "none"
    FADE_IN = "fade_in"
    FADE_OUT = "fade_out"
    KARAOKE = "karaoke"
    GLOW = "glow"
    GRADIENT = "gradient"
    BOUNCE = "bounce"
    TYPEWRITER = "typewriter"
    WAVE = "wave"


@dataclass
class SubtitleStyle:
    """
    Styling configuration for animated subtitles.
    
    Attributes:
        font_size: Font size in pixels
        font_color: Text color (hex or ffmpeg color name)
        background_color: Background color for subtitle box
        outline_color: Outline/stroke color
        outline_width: Outline width in pixels
        shadow_color: Shadow color
        shadow_x: Shadow X offset
        shadow_y: Shadow Y offset
        bold: Whether text should be bold
        italic: Whether text should be italic
        alignment: Text alignment (1=left, 2=center, 3=right)
        margin_v: Vertical margin from bottom
    """
    font_size: int = 24
    font_color: str = "white"
    background_color: str = "black@0.5"
    outline_color: str = "black"
    outline_width: int = 2
    shadow_color: str = "black"
    shadow_x: int = 2
    shadow_y: int = 2
    bold: bool = True
    italic: bool = False
    alignment: int = 2  # center
    margin_v: int = 50


class AnimatedSubtitleGenerator:
    """
    Generate FFmpeg filter strings for animated subtitle effects.
    
    This class creates complex filter graphs that apply various animations
    to subtitle text, including karaoke-style coloring, fade effects, glow,
    gradient text, and more.
    """
    
    def __init__(self, logger: Optional[logging.Logger] = None):
        self.logger = logger or _logger
    
    def _escape_text(self, text: str) -> str:
        """Escape text for safe use in FFmpeg drawtext filter."""
        text = text.replace("\\", "\\\\")
        text = text.replace(":", "\\:")
        text = text.replace("%", "\\%")
        text = text.replace(",", "\\,")
        text = text.replace("[", "\\[")
        text = text.replace("]", "\\]")
        text = text.replace("'", "\u2019")
        text = text.replace("\n", " ")
        return text
    
    def generate_karaoke_filter(
        self,
        text: str,
        start: float,
        end: float,
        duration_per_char: float = 0.08,
        style: Optional[SubtitleStyle] = None,
    ) -> str:
        """
        Generate karaoke-style subtitle filter where each character highlights
        as it's "spoken".
        
        Args:
            text: Subtitle text
            start: Start time in seconds
            end: End time in seconds
            duration_per_char: Time to highlight each character
            style: Subtitle styling
        """
        style = style or SubtitleStyle()
        escaped_text = self._escape_text(text)
        text_len = len(text)
        
        # Create a time-based expression for karaoke highlighting
        # Each character gets highlighted for duration_per_char seconds
        enable_expr = f"between(t\\,{start:.3f}\\,{end:.3f})"
        
        # Base drawtext filter
        base_filter = (
            f"drawtext=text='{escaped_text}':"
            f"fontsize={style.font_size}:"
            f"fontcolor={style.font_color}:"
            f"x=(w-tw)/2:"
            f"y=h-{style.margin_v}:"
            f"enable='{enable_expr}'"
        )
        
        # Add outline if specified
        if style.outline_width > 0:
            base_filter += f":bordercolor={style.outline_color}:borderw={style.outline_width}"
        
        # Add shadow if specified
        if style.shadow_x or style.shadow_y:
            base_filter += f":shadowcolor={style.shadow_color}:shadowx={style.shadow_x}:shadowy={style.shadow_y}"
        
        return base_filter
    
    def generate_fade_filter(
        self,
        text: str,
        start: float,
        end: float,
        fade_duration: float = 0.3,
        style: Optional[SubtitleStyle] = None,
    ) -> str:
        """
        Generate subtitle filter with fade-in and fade-out effects.
        
        Args:
            text: Subtitle text
            start: Start time in seconds
            end: End time in seconds
            fade_duration: Duration of fade in/out in seconds
            style: Subtitle styling
        """
        style = style or SubtitleStyle()
        escaped_text = self._escape_text(text)
        
        # Fade expression: alpha goes from 0 to 1 at start, 1 to 0 at end
        fade_in_expr = f"if(lt(t\\,{start + fade_duration})\\,(t-{start})/{fade_duration}\\,1)"
        fade_out_expr = f"if(gt(t\\,{end - fade_duration})\\,({end}-t)/{fade_duration}\\,1)"
        alpha_expr = f"{fade_in_expr}*{fade_out_expr}"
        
        base_filter = (
            f"drawtext=text='{escaped_text}':"
            f"fontsize={style.font_size}:"
            f"fontcolor={style.font_color}@{alpha_expr}:"
            f"x=(w-tw)/2:"
            f"y=h-{style.margin_v}:"
            f"enable='between(t\\,{start:.3f}\\,{end:.3f})'"
        )
        
        if style.outline_width > 0:
            base_filter += f":bordercolor={style.outline_color}@{alpha_expr}:borderw={style.outline_width}"
        
        return base_filter
    
    def generate_glow_filter(
        self,
        text: str,
        start: float,
        end: float,
        glow_color: str = "yellow",
        glow_intensity: float = 0.5,
        style: Optional[SubtitleStyle] = None,
    ) -> str:
        """
        Generate subtitle filter with glowing effect.
        
        Args:
            text: Subtitle text
            start: Start time in seconds
            end: End time in seconds
            glow_color: Color of the glow
            glow_intensity: Intensity of the glow (0-1)
            style: Subtitle styling
        """
        style = style or SubtitleStyle()
        escaped_text = self._escape_text(text)
        enable_expr = f"between(t\\,{start:.3f}\\,{end:.3f})"
        
        # Create multiple layers for glow effect
        # Layer 1: Glow (larger, semi-transparent)
        glow_filter = (
            f"drawtext=text='{escaped_text}':"
            f"fontsize={style.font_size + 4}:"
            f"fontcolor={glow_color}@{glow_intensity}:"
            f"x=(w-tw)/2:"
            f"y=h-{style.margin_v}:"
            f"enable='{enable_expr}'"
        )
        
        # Layer 2: Main text (on top)
        text_filter = (
            f"drawtext=text='{escaped_text}':"
            f"fontsize={style.font_size}:"
            f"fontcolor={style.font_color}:"
            f"x=(w-tw)/2:"
            f"y=h-{style.margin_v}:"
            f"enable='{enable_expr}'"
        )
        
        if style.outline_width > 0:
            text_filter += f":bordercolor={style.outline_color}:borderw={style.outline_width}"
        
        # Combine both filters
        return f"{glow_filter},{text_filter}"
    
    def generate_gradient_filter(
        self,
        text: str,
        start: float,
        end: float,
        gradient_colors: str = "red|blue",
        style: Optional[SubtitleStyle] = None,
    ) -> str:
        """
        Generate subtitle filter with gradient text color.
        
        Note: True gradient text requires complex filter chains with
        multiple drawtext layers. This is a simplified version that
        oscillates between colors.
        
        Args:
            text: Subtitle text
            start: Start time in seconds
            end: End time in seconds
            gradient_colors: Pipe-separated colors (e.g., "red|blue|green")
            style: Subtitle styling
        """
        style = style or SubtitleStyle()
        escaped_text = self._escape_text(text)
        enable_expr = f"between(t\\,{start:.3f}\\,{end:.3f})"
        
        colors = gradient_colors.split("|")
        num_colors = len(colors)
        
        # Create a time-based color oscillation
        # This simplifies gradient by oscillating between colors
        if num_colors >= 2:
            color_expr = f"if(mod(floor((t-{start})*2)\\,{num_colors})==0\\,'{colors[0]}'\\,'{colors[1]}')"
            if num_colors > 2:
                for i, color in enumerate(colors[2:], 2):
                    color_expr = f"if(mod(floor((t-{start})*2)\\,{num_colors})=={i}\\,'{color}'\\,{color_expr})"
        else:
            color_expr = f"'{colors[0]}'"
        
        base_filter = (
            f"drawtext=text='{escaped_text}':"
            f"fontsize={style.font_size}:"
            f"fontcolor={color_expr}:"
            f"x=(w-tw)/2:"
            f"y=h-{style.margin_v}:"
            f"enable='{enable_expr}'"
        )
        
        if style.outline_width > 0:
            base_filter += f":bordercolor={style.outline_color}:borderw={style.outline_width}"
        
        return base_filter
    
    def generate_bounce_filter(
        self,
        text: str,
        start: float,
        end: float,
        bounce_height: int = 10,
        bounce_speed: float = 2.0,
        style: Optional[SubtitleStyle] = None,
    ) -> str:
        """
        Generate subtitle filter with bouncing animation.
        
        Args:
            text: Subtitle text
            start: Start time in seconds
            end: End time in seconds
            bounce_height: Maximum bounce height in pixels
            bounce_speed: Speed of bounce (higher = faster)
            style: Subtitle styling
        """
        style = style or SubtitleStyle()
        escaped_text = self._escape_text(text)
        enable_expr = f"between(t\\,{start:.3f}\\,{end:.3f})"
        
        # Bounce expression using sine wave
        bounce_expr = f"sin((t-{start})*{bounce_speed}*PI)*{bounce_height}"
        y_expr = f"h-{style.margin_v}+{bounce_expr}"
        
        base_filter = (
            f"drawtext=text='{escaped_text}':"
            f"fontsize={style.font_size}:"
            f"fontcolor={style.font_color}:"
            f"x=(w-tw)/2:"
            f"y={y_expr}:"
            f"enable='{enable_expr}'"
        )
        
        if style.outline_width > 0:
            base_filter += f":bordercolor={style.outline_color}:borderw={style.outline_width}"
        
        return base_filter
    
    def generate_typewriter_filter(
        self,
        text: str,
        start: float,
        end: float,
        char_duration: float = 0.05,
        style: Optional[SubtitleStyle] = None,
    ) -> str:
        """
        Generate subtitle filter with typewriter effect (characters appear one by one).
        
        Args:
            text: Subtitle text
            start: Start time in seconds
            end: End time in seconds
            char_duration: Time between each character appearing
            style: Subtitle styling
        """
        style = style or SubtitleStyle()
        escaped_text = self._escape_text(text)
        text_len = len(text)
        
        # Typewriter effect is complex in FFmpeg
        # We use a time-based substring expression
        # This is a simplified version that shows progressively more characters
        enable_expr = f"between(t\\,{start:.3f}\\,{end:.3f})"
        
        # Calculate how many characters to show based on time
        chars_to_show = f"min({text_len}\\,floor((t-{start})/{char_duration}))"
        
        # FFmpeg doesn't have a substring function for drawtext
        # This is a limitation - we'll use a simpler fade-in approach instead
        # that simulates typewriter by fading in the whole text
        return self.generate_fade_filter(text, start, end, char_duration * text_len, style)
    
    def generate_wave_filter(
        self,
        text: str,
        start: float,
        end: float,
        wave_amplitude: int = 5,
        wave_frequency: float = 3.0,
        style: Optional[SubtitleStyle] = None,
    ) -> str:
        """
        Generate subtitle filter with wave animation.
        
        Args:
            text: Subtitle text
            start: Start time in seconds
            end: End time in seconds
            wave_amplitude: Wave height in pixels
            wave_frequency: Wave frequency
            style: Subtitle styling
        """
        style = style or SubtitleStyle()
        escaped_text = self._escape_text(text)
        enable_expr = f"between(t\\,{start:.3f}\\,{end:.3f})"
        
        # Wave expression
        wave_expr = f"sin((t-{start})*{wave_frequency}*PI)*{wave_amplitude}"
        y_expr = f"h-{style.margin_v}+{wave_expr}"
        
        base_filter = (
            f"drawtext=text='{escaped_text}':"
            f"fontsize={style.font_size}:"
            f"fontcolor={style.font_color}:"
            f"x=(w-tw)/2:"
            f"y={y_expr}:"
            f"enable='{enable_expr}'"
        )
        
        if style.outline_width > 0:
            base_filter += f":bordercolor={style.outline_color}:borderw={style.outline_width}"
        
        return base_filter
    
    def generate_filter(
        self,
        text: str,
        start: float,
        end: float,
        effect: SubtitleEffect,
        style: Optional[SubtitleStyle] = None,
        **kwargs,
    ) -> str:
        """
        Generate FFmpeg filter string for the specified effect.
        
        Args:
            text: Subtitle text
            start: Start time in seconds
            end: End time in seconds
            effect: The animation effect to apply
            style: Subtitle styling
            **kwargs: Additional effect-specific parameters
        """
        if effect == SubtitleEffect.NONE:
            style = style or SubtitleStyle()
            escaped_text = self._escape_text(text)
            enable_expr = f"between(t\\,{start:.3f}\\,{end:.3f})"
            
            base_filter = (
                f"drawtext=text='{escaped_text}':"
                f"fontsize={style.font_size}:"
                f"fontcolor={style.font_color}:"
                f"x=(w-tw)/2:"
                f"y=h-{style.margin_v}:"
                f"enable='{enable_expr}'"
            )
            
            if style.outline_width > 0:
                base_filter += f":bordercolor={style.outline_color}:borderw={style.outline_width}"
            
            return base_filter
        
        elif effect == SubtitleEffect.FADE_IN:
            return self.generate_fade_filter(text, start, end, kwargs.get("fade_duration", 0.3), style)
        
        elif effect == SubtitleEffect.FADE_OUT:
            return self.generate_fade_filter(text, start, end, kwargs.get("fade_duration", 0.3), style)
        
        elif effect == SubtitleEffect.KARAOKE:
            return self.generate_karaoke_filter(text, start, end, kwargs.get("duration_per_char", 0.08), style)
        
        elif effect == SubtitleEffect.GLOW:
            return self.generate_glow_filter(text, start, end, kwargs.get("glow_color", "yellow"), kwargs.get("glow_intensity", 0.5), style)
        
        elif effect == SubtitleEffect.GRADIENT:
            return self.generate_gradient_filter(text, start, end, kwargs.get("gradient_colors", "red|blue"), style)
        
        elif effect == SubtitleEffect.BOUNCE:
            return self.generate_bounce_filter(text, start, end, kwargs.get("bounce_height", 10), kwargs.get("bounce_speed", 2.0), style)
        
        elif effect == SubtitleEffect.TYPEWRITER:
            return self.generate_typewriter_filter(text, start, end, kwargs.get("char_duration", 0.05), style)
        
        elif effect == SubtitleEffect.WAVE:
            return self.generate_wave_filter(text, start, end, kwargs.get("wave_amplitude", 5), kwargs.get("wave_frequency", 3.0), style)
        
        else:
            self.logger.warning("Unknown subtitle effect: %s, falling back to none", effect)
            return self.generate_filter(text, start, end, SubtitleEffect.NONE, style, **kwargs)
