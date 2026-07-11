# src/universal_video_ai/render/__init__.py
"""
Render module.

Exports:
- Renderer, RenderConfig, TextOverlay
- OnScreenTextDetector, TextRegion (optional OCR-based text-cover detection)
"""
from .renderer import Renderer, RenderConfig, TextOverlay
from .text_detector import OnScreenTextDetector, TextRegion, OCR_AVAILABLE

__all__ = [
    "Renderer", "RenderConfig", "TextOverlay",
    "OnScreenTextDetector", "TextRegion", "OCR_AVAILABLE",
]