from pathlib import Path
import os

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