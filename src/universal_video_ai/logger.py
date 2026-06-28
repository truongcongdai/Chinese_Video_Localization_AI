import logging

from universal_video_ai.config import LOG_DIR, LOG_LEVEL

LOG_FILE = LOG_DIR / "application.log"

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
    ],
)

logger = logging.getLogger("UniversalVideoAI")