# scripts/run_bot.py
"""
Local bot runner with health check server.
Usage: python scripts/run_bot.py --admin 123456789
"""

import argparse
import logging
import os
import signal
import sys
from pathlib import Path

# Ensure package import works when running as script in Docker context
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.universal_video_ai.config import TEMP_DIR
from src.universal_video_ai.downloader.service import DownloadService
from src.universal_video_ai.downloader.validator import UrlValidator
from src.universal_video_ai.database import DatabaseManager
from src.universal_video_ai.bot.telegram_bot import TelegramBot, MockAdapter
from src.universal_video_ai.bot.real_telegram_adapter import RealTelegramAdapter
from src.universal_video_ai.bot.server import start_health_check_server
from src.universal_video_ai.logger import setup_logger
from src.universal_video_ai.orchestrator.factory import create_localization_service

# Initialize logger for this runner
logger = setup_logger("bot_runner")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Telegram bot locally")
    parser.add_argument("--admin", type=int, nargs="+", default=[], help="Admin chat IDs")
    parser.add_argument("--db", type=Path, default=TEMP_DIR / "database.sqlite3", help="Database path")
    parser.add_argument("--mock", action="store_true", help="Use mock adapter for testing")
    args = parser.parse_args()

    # Setup database
    db_manager = DatabaseManager(db_path=args.db, logger=logger)
    db_manager.init_schema()
    logger.info("Database initialized at %s", args.db)

    # Setup services
    downloader = DownloadService()
    validator = UrlValidator()

    # Setup localization service with full pipeline enabled using factory
    from universal_video_ai.render.renderer import RenderConfig

    # Cover the original burned-in Chinese subtitles with a precise,
    # per-sentence white box (detected via OCR) instead of blurring the
    # whole frame. blur_text is OFF — it was only ever a coarse fallback,
    # and blurring the entire video (not just the subtitle strip) made it
    # hard to watch. The white box + translated text is turned on below via
    # `enable_text_cover=True`.
    render_config = RenderConfig(
        blur_text=False,
        blur_box=None,
        # DejaVu Sans (installed via Dockerfile) renders Vietnamese
        # diacritics correctly; ffmpeg's fontconfig default often doesn't.
        default_overlay_font_path="/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        # No watermark_box_fractional anymore: the downloader now fetches
        # Douyin/TikTok's own non-watermarked stream directly (see
        # downloader/douyin.py and downloader/ytdlp_downloader.py), so
        # there's no logo left in the pixels to cover. Permanently blurring
        # a corner that's already clean just made the video look worse for
        # no reason. If a specific source still slips through with a
        # watermark, set this back to e.g. (0.80, 0.72, 1.0, 1.0) as a
        # per-deployment fallback.
        watermark_box_fractional=None,
    )

    localization_service = create_localization_service(
        run_transcription=True,
        transcription_language=None,  # Auto-detect language (important for YouTube videos)
        run_translation=True,
        target_language="vi",
        run_tts=True,
        generate_subtitles=True,
        mix_audio=True,
        replace_source_audio=True,
        background_music_dir=Path(os.getenv("LICENSED_MUSIC_DIR", "./local_data/music")),
        replacement_music_volume=float(os.getenv("REPLACEMENT_MUSIC_VOLUME", "0.16")),
        render_video=True,
        render_config=render_config,
        # Detect the burned-in Chinese subtitle region via OCR (easyocr) for
        # each sentence and cover it with a white box + centered translated
        # text, instead of relying on the old whole-frame blur.
        enable_text_cover=True,
        ocr_languages=("ch_sim", "en"),
        logger=logger
    )

    # Setup bot adapter (real or mock)
    if args.mock:
        adapter = MockAdapter()
        logger.info("Using MockAdapter for testing")
    else:
        try:
            adapter = RealTelegramAdapter(logger=logger)
            logger.info("Using RealTelegramAdapter")
        except ValueError as exc:
            logger.warning("Failed to initialize RealTelegramAdapter: %s. Falling back to MockAdapter", exc)
            adapter = MockAdapter()

    admin_ids = set(args.admin) if args.admin else set()
    bot = TelegramBot(
        adapter=adapter,
        download_service=downloader,
        localization_service=localization_service,
        database_manager=db_manager,
        admin_chat_ids=admin_ids,
        output_dir=TEMP_DIR / "output",
        validator=validator,
        logger=logger,
    )

    # Start health check server
    health_server = start_health_check_server(host="0.0.0.0", port=8000)

    # Graceful shutdown
    def signal_handler(sig, frame):
        logger.info("Shutdown signal received (%s)", sig)
        try:
            bot.stop()
        except Exception:
            logger.exception("Error stopping bot")
        health_server.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    logger.info("=" * 60)
    logger.info("TELEGRAM BOT STARTED (LOCAL MODE)")
    logger.info("=" * 60)
    logger.info("Admin IDs: %s", admin_ids or "None")
    logger.info("Database: %s", args.db)
    logger.info("Health check: http://127.0.0.1:8000/health")
    logger.info("=" * 60)

    # Start bot (blocks)
    try:
        bot.start()
        # Keep the main thread alive since MockAdapter doesn't block
        while True:
            import time
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt")
        bot.stop()
        health_server.stop()


if __name__ == "__main__":
    main()
