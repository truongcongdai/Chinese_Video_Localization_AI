from pathlib import Path
import os
from universal_video_ai.cache import RedisCache

BASE_DIR = Path(__file__).resolve().parents[2]

TEMP_DIR = BASE_DIR / "temp"
LOG_DIR = BASE_DIR / "logs"
COOKIE_DIR = BASE_DIR / "cookies"

for directory in (
    TEMP_DIR,
    LOG_DIR,
    COOKIE_DIR,
):
    directory.mkdir(parents=True, exist_ok=True)

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
REDIS_URL = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")
cache = RedisCache(url=REDIS_URL, fallback=True)