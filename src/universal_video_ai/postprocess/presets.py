"""Preset templates for common video transformation use cases."""
from dataclasses import dataclass
from enum import Enum
from typing import Dict, Any
from pathlib import Path

from .video_transform import TransformConfig, FlipMode, BorderPosition


class PresetType(Enum):
    """Available preset types."""
    ANTI_DETECTION = "anti_detection"
    CINEMATIC = "cinematic"
    SOCIAL_MEDIA = "social_media"
    MINIMAL = "minimal"
    PROFESSIONAL = "professional"


@dataclass
class PresetConfig:
    """Configuration for a preset template."""
    name: str
    description: str
    transform_config: TransformConfig
    video_template_config: Dict[str, Any]
    animated_subtitle_config: Dict[str, Any]


# Preset definitions
PRESETS: Dict[PresetType, PresetConfig] = {
    PresetType.ANTI_DETECTION: PresetConfig(
        name="Anti-Detection",
        description="Tối ưu để tránh phát hiện: lật ngang, thêm khung viền, random hóa nhẹ",
        transform_config=TransformConfig(
            enable_flip=True,
            flip_mode=FlipMode.HORIZONTAL,
            enable_border=True,
            border_position=BorderPosition.TOP_BOTTOM,
            border_px=60,
            border_color="black",
            enable_randomization=True,
            crop_percent=1.0,
            speed_factor=1.0,
            brightness_adjust=0.0,
            contrast_adjust=0.0,
        ),
        video_template_config={
            "enabled": True,
            "template": "minimal",
            "transition": "fade",
            "color_effect": "none",
            "video_quality": "medium",
        },
        animated_subtitle_config={
            "enabled": False,
        },
    ),
    
    PresetType.CINEMATIC: PresetConfig(
        name="Cinematic",
        description="Phong cách điện ảnh: khung viền letterbox, màu ấm",
        transform_config=TransformConfig(
            enable_flip=False,
            flip_mode=FlipMode.NONE,
            enable_border=True,
            border_position=BorderPosition.TOP_BOTTOM,
            border_px=120,
            border_color="black",
            enable_randomization=False,
            crop_percent=0.0,
            speed_factor=1.0,
            brightness_adjust=-2.0,
            contrast_adjust=2.0,
        ),
        video_template_config={
            "enabled": True,
            "template": "cinematic",
            "transition": "fade",
            "color_effect": "warm",
            "video_quality": "high",
        },
        animated_subtitle_config={
            "enabled": True,
            "effect": "fade",
            "style": {
                "font_size": 28,
                "font_color": "white",
                "background_color": "rgba(0,0,0,0.7)",
            },
        },
    ),
    
    PresetType.SOCIAL_MEDIA: PresetConfig(
        name="Social Media",
        description="Tối ưu cho TikTok/Instagram: crop nhẹ, tốc độ nhanh",
        transform_config=TransformConfig(
            enable_flip=False,
            flip_mode=FlipMode.NONE,
            enable_border=False,
            border_position=BorderPosition.NONE,
            border_px=0,
            border_color="black",
            enable_randomization=True,
            crop_percent=0.5,
            speed_factor=1.01,
            brightness_adjust=1.0,
            contrast_adjust=1.0,
        ),
        video_template_config={
            "enabled": True,
            "template": "vibrant",
            "transition": "slide",
            "color_effect": "high_contrast",
            "video_quality": "medium",
        },
        animated_subtitle_config={
            "enabled": True,
            "effect": "typewriter",
            "style": {
                "font_size": 32,
                "font_color": "#FFD700",
                "background_color": "rgba(0,0,0,0.5)",
            },
        },
    ),
    
    PresetType.MINIMAL: PresetConfig(
        name="Minimal",
        description="Phong cách tối giản: không thay đổi nhiều",
        transform_config=TransformConfig(
            enable_flip=False,
            flip_mode=FlipMode.NONE,
            enable_border=False,
            border_position=BorderPosition.NONE,
            border_px=0,
            border_color="black",
            enable_randomization=False,
            crop_percent=0.0,
            speed_factor=1.0,
            brightness_adjust=0.0,
            contrast_adjust=0.0,
        ),
        video_template_config={
            "enabled": False,
        },
        animated_subtitle_config={
            "enabled": False,
        },
    ),
    
    PresetType.PROFESSIONAL: PresetConfig(
        name="Professional",
        description="Phong cách chuyên nghiệp: màu sắc cân bằng, chất lượng cao",
        transform_config=TransformConfig(
            enable_flip=False,
            flip_mode=FlipMode.NONE,
            enable_border=True,
            border_position=BorderPosition.TOP_BOTTOM,
            border_px=40,
            border_color="#1a1a1a",
            enable_randomization=False,
            crop_percent=0.0,
            speed_factor=1.0,
            brightness_adjust=0.0,
            contrast_adjust=0.0,
        ),
        video_template_config={
            "enabled": True,
            "template": "professional",
            "transition": "dissolve",
            "color_effect": "none",
            "video_quality": "high",
        },
        animated_subtitle_config={
            "enabled": True,
            "effect": "fade",
            "style": {
                "font_size": 24,
                "font_color": "white",
                "background_color": "rgba(0,0,0,0.6)",
            },
        },
    ),
}


def get_preset(preset_type: PresetType) -> PresetConfig:
    """Get a preset configuration by type."""
    return PRESETS[preset_type]


def get_preset_by_name(name: str) -> PresetConfig:
    """Get a preset configuration by name."""
    for preset in PRESETS.values():
        if preset.name.lower() == name.lower():
            return preset
    raise ValueError(f"Preset not found: {name}")


def list_presets() -> Dict[str, Dict[str, Any]]:
    """List all available presets with their metadata."""
    return {
        preset_type.value: {
            "name": preset.name,
            "description": preset.description,
        }
        for preset_type, preset in PRESETS.items()
    }


def apply_preset_to_job_config(
    preset_type: PresetType,
    job_config: Dict[str, Any],
) -> Dict[str, Any]:
    """Apply a preset to a job configuration."""
    preset = get_preset(preset_type)
    
    # Convert TransformConfig to dict
    transform_dict = {
        "enabled": True,
        "enable_flip": preset.transform_config.enable_flip,
        "flip_mode": preset.transform_config.flip_mode.value,
        "enable_border": preset.transform_config.enable_border,
        "border_position": preset.transform_config.border_position.value,
        "border_px": preset.transform_config.border_px,
        "border_color": preset.transform_config.border_color,
        "enable_split_screen": preset.transform_config.enable_split_screen,
        "split_mode": preset.transform_config.split_mode,
        "enable_randomization": preset.transform_config.enable_randomization,
        "crop_percent": preset.transform_config.crop_percent,
        "speed_factor": preset.transform_config.speed_factor,
        "brightness_adjust": preset.transform_config.brightness_adjust,
        "contrast_adjust": preset.transform_config.contrast_adjust,
    }
    
    # Update job config
    job_config["transform_config"] = transform_dict
    job_config["video_template_config"] = preset.video_template_config
    job_config["animated_subtitle_config"] = preset.animated_subtitle_config
    
    return job_config
