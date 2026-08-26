#!/usr/bin/env python3
"""
Test script for watermark removal.
Usage: python test_watermark_removal.py <video_path> [output_path]

This script will:
1. Detect persistent watermark regions in the video
2. Apply inpainting to remove detected watermarks
3. Save the cleaned video for review
"""

import sys
import logging
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from universal_video_ai.render.text_detector import OnScreenTextDetector
from universal_video_ai.render.watermark_cleaner import SourceWatermarkCleaner

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def main():
    if len(sys.argv) < 2:
        print("Usage: python test_watermark_removal.py <video_path> [output_path]")
        print("Example: python test_watermark_removal.py input.mp4 output_cleaned.mp4")
        sys.exit(1)
    
    video_path = Path(sys.argv[1])
    if not video_path.exists():
        logger.error(f"Video file not found: {video_path}")
        sys.exit(1)
    
    if len(sys.argv) >= 3:
        output_path = Path(sys.argv[2])
    else:
        output_path = video_path.parent / f"{video_path.stem}_watermark_removed.mp4"
    
    logger.info(f"Testing watermark removal on: {video_path}")
    logger.info(f"Output will be saved to: {output_path}")
    
    # Step 1: Detect watermark regions
    logger.info("Step 1: Detecting persistent watermark regions...")
    detector = OnScreenTextDetector()
    
    # Get video duration using ffprobe
    import subprocess
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", 
             "-of", "default=noprint_wrappers=1:nokey=1", str(video_path)],
            capture_output=True, text=True, timeout=30
        )
        duration = float(result.stdout.strip()) if result.returncode == 0 else 0.0
    except Exception as e:
        logger.warning(f"Could not get video duration: {e}")
        duration = 0.0
    
    if duration <= 0:
        logger.error("Invalid video duration, cannot detect watermarks")
        sys.exit(1)
    
    # Detect persistent text/watermark regions
    watermark_boxes = detector.detect_persistent_text_regions(
        video_path=video_path,
        duration=duration,
        sample_count=8,  # Conservative sampling
        min_seen_ratio=0.40,  # Conservative threshold
    )
    
    logger.info(f"Detected {len(watermark_boxes)} watermark region(s)")
    for i, (x0, y0, x1, y1) in enumerate(watermark_boxes, 1):
        logger.info(f"  Region {i}: ({x0:.3f}, {y0:.3f}) -> ({x1:.3f}, {y1:.3f})")
    
    if not watermark_boxes:
        logger.warning("No watermark regions detected. The video may not have persistent watermarks.")
        logger.info("You can manually specify watermark boxes if needed.")
        sys.exit(0)
    
    # Step 2: Apply inpainting to remove watermarks
    logger.info("Step 2: Applying inpainting to remove watermarks...")
    cleaner = SourceWatermarkCleaner(logger=logger)
    
    try:
        cleaned_video = cleaner.clean(
            video_path=video_path,
            output_path=output_path,
            boxes_fractional=watermark_boxes,
            radius=8.0,  # Conservative radius
            inpaint_passes=2,  # Conservative passes
            post_blur=True,
            blur_kernel_size=5,  # Subtle blur
        )
        logger.info(f"Watermark removal completed successfully!")
        logger.info(f"Cleaned video saved to: {cleaned_video}")
        logger.info(f"Please review the output to verify watermark removal quality.")
    except Exception as e:
        logger.error(f"Watermark removal failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
