# scripts/run_bot.py
"""
Local bot runner with health check server.
Usage: python scripts/run_bot.py --admin 123456789
"""

import argparse
import logging
import signal
import sys
from pathlib import Path
from typing import Optional

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.universal_video_ai.config import setup_logger, TEMP_DIR
from src.universal_video_ai.downloader.service import DownloadService
from src.universal_video_ai.downloader.validator import UrlValidator
from src.universal_video_ai.database import DatabaseManager
from src.universal_video_ai.bot.telegram_bot import TelegramBot, MockAdapter
from src.universal_video_ai.bot.server import start_health_check_server

logger = setup_logger("bot_runner")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Telegram bot locally")
    parser.add_argument("--admin", type=int, nargs="+", default=[], help="Admin chat IDs")
    parser.add_argument("--db", type=Path, default=TEMP_DIR / "database.sqlite3", help="Database path")
    args = parser.parse_args()

    # Setup database
    db_manager = DatabaseManager(db_path=args.db, logger=logger)
    db_manager.init_schema()
    logger.info("Database initialized at %s", args.db)

    # Setup services
    downloader = DownloadService(logger=logger)
    validator = UrlValidator()

    # Setup bot with mock adapter (for testing)
    adapter = MockAdapter()

    admin_ids = set(args.admin) if args.admin else set()
    bot = TelegramBot(
        adapter=adapter,
        download_service=downloader,
        database_manager=db_manager,
        admin_chat_ids=admin_ids,
        output_dir=TEMP_DIR / "output",
        validator=validator,
        logger=logger,
    )

    # Start health check server
    health_server = start_health_check_server(host="127.0.0.1", port=8000)

    # Graceful shutdown
    def signal_handler(sig, frame):
        logger.info("Shutdown signal received (%s)", sig)
        bot.stop()
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
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt")
        bot.stop()
        health_server.stop()


if __name__ == "__main__":
    main()