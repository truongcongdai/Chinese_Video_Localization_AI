# src/universal_video_ai/logger.py
import logging
from pathlib import Path

from universal_video_ai.config import LOG_DIR, LOG_LEVEL

LOG_FILE = LOG_DIR / "application.log"

# Ensure parent dir exists (config.py should have created LOG_DIR, but be defensive)
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

# Basic configuration for root logger
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
    ],
)

# httpx logs complete Telegram API URLs at INFO level. Telegram embeds the
# bot token in that URL, so routine getUpdates/getMe lines would leak a live
# credential into Docker logs. Keep HTTP client details to warnings/errors.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

def setup_logger(name: str, level: str | None = None) -> logging.Logger:
    """
    Convenience function to create/get a named logger configured with the
    global handlers. Returns the logger instance.
    """
    logger = logging.getLogger(name)
    if level:
        logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    return logger

# Default module logger
logger = logging.getLogger("UniversalVideoAI")
