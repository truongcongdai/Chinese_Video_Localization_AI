# src/universal_video_ai/__main__.py
"""
CLI entry point for Universal Video AI.

Supports end-to-end video localization: download → transcribe → translate → TTS → subtitles → mix.
"""

from __future__ import annotations

import argparse
import sys
import logging
from pathlib import Path

from universal_video_ai.logger import logger
from universal_video_ai.downloader.platform_detector import PlatformDetector
from universal_video_ai.orchestrator.factory import create_localization_service


def main() -> None:
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Universal Video AI - end-to-end video localization",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Download and extract audio
  python -m universal_video_ai https://example.com/video.mp4 --output /tmp/output

  # Download, transcribe, and generate subtitles
  python -m universal_video_ai https://example.com/video.mp4 --output /tmp/output --transcribe --subtitles

  # Full pipeline: transcribe + translate + TTS + mix
  python -m universal_video_ai https://example.com/video.mp4 --output /tmp/output \\
    --transcribe --translate --target-lang vi --tts --mix-audio
        """,
    )

    parser.add_argument("url", help="Video URL to process")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path.cwd() / "output",
        help="Output directory (default: ./output)",
    )
    parser.add_argument(
        "--transcribe",
        action="store_true",
        help="Enable speech-to-text transcription",
    )
    parser.add_argument(
        "--transcription-lang",
        default="en",
        help="Transcription language code (default: en)",
    )
    parser.add_argument(
        "--translate",
        action="store_true",
        help="Enable text translation",
    )
    parser.add_argument(
        "--target-lang",
        default="vi",
        help="Target language for translation (default: vi)",
    )
    parser.add_argument(
        "--tts",
        action="store_true",
        help="Enable text-to-speech synthesis",
    )
    parser.add_argument(
        "--subtitles",
        action="store_true",
        help="Generate subtitle files",
    )
    parser.add_argument(
        "--demucs",
        action="store_true",
        help="Enable audio stem separation (Demucs)",
    )
    parser.add_argument(
        "--mix-audio",
        action="store_true",
        help="Mix original audio with TTS audio",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )

    args = parser.parse_args()

    # Configure logging
    if args.verbose:
        logging.getLogger("universal_video_ai").setLevel(logging.DEBUG)

    logger.info("Universal Video AI CLI started")
    logger.info("Processing URL: %s", args.url)

    # Detect platform
    try:
        detector = PlatformDetector()
        platform = detector.detect(args.url)
        logger.info("Detected platform: %s", platform.value)
    except ValueError as exc:
        logger.error("Failed to detect platform: %s", exc)
        sys.exit(1)

    # Create localization service with requested features
    logger.info(
        "Creating localization service: transcribe=%s translate=%s tts=%s",
        args.transcribe,
        args.translate,
        args.tts,
    )
    try:
        service = create_localization_service(
            run_demucs=args.demucs,
            run_transcription=args.transcribe,
            transcription_language=args.transcription_lang,
            run_translation=args.translate,
            target_language=args.target_lang,
            run_tts=args.tts,
            generate_subtitles=args.subtitles,
            mix_audio=args.mix_audio,
            logger=logger,
        )
    except Exception as exc:
        logger.error("Failed to create localization service: %s", exc)
        sys.exit(1)

    # Execute localization
    try:
        logger.info("Starting localization workflow...")
        result = service.localize(args.url, output_dir=args.output)

        logger.info("=" * 60)
        logger.info("LOCALIZATION COMPLETE")
        logger.info("=" * 60)
        logger.info("Download result: %s", "SUCCESS" if result.download_result.success else "FAILED")

        if result.download_result.success:
            logger.info("Video: %s", result.download_result.video_path)
            logger.info("Title: %s", result.download_result.title)
            logger.info("Duration: %.1f sec", result.download_result.duration)

        if result.audio_pipeline_result.audio_result:
            logger.info("Audio extracted: %s", result.audio_pipeline_result.audio_result.audio_path)

        if result.audio_pipeline_result.transcript:
            logger.info("Transcript: %s...", result.audio_pipeline_result.transcript[:100])

        if result.translated_text:
            logger.info("Translation: %s...", result.translated_text[:100])

        if result.tts_audio_path:
            logger.info("TTS audio: %s", result.tts_audio_path)

        if result.mixed_audio_path:
            logger.info("Mixed audio: %s", result.mixed_audio_path)

        if result.subtitle_segments:
            logger.info("Subtitles: %d segments", len(result.subtitle_segments))

        logger.info("All artifacts saved to: %s", args.output)
        logger.info("=" * 60)

    except ValueError as exc:
        logger.error("Localization failed: %s", exc)
        sys.exit(1)
    except Exception as exc:
        logger.exception("Unexpected error during localization: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()