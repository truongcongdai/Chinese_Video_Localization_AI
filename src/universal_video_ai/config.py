from pathlib import Path
import os

BASE_DIR = Path(__file__).resolve().parents[2]

# Load .env into os.environ as early as possible — config.py is imported
# by nearly every other module in this package, so this guarantees .env
# values (WEB_SESSION_SECRET, GOOGLE_CLIENT_ID, JOB_COST_CREDITS, etc.) are
# actually visible via os.getenv()/os.environ everywhere else, no matter
# which script started the process or what its working directory is.
# `override=False` (the default) means a real OS environment variable
# already set always wins over .env, matching normal expectations.
#
# This was previously missing entirely: .env existed only as a template
# nobody ever parsed, so every value in it was silently ignored unless the
# person also exported it as a real shell/OS environment variable — which
# is why WEB_SESSION_SECRET (and anything else in .env) appeared "not set"
# even with a value sitting right there in the file.
try:
    from dotenv import load_dotenv
    load_dotenv(BASE_DIR / ".env")
except ImportError:
    # python-dotenv isn't installed for some reason — fall back to
    # requiring real OS environment variables, same as before, rather than
    # crashing the whole import.
    pass

from universal_video_ai.cache import RedisCache

def _local_path_from_env(name: str, default: str) -> Path:
    """Resolve local storage settings consistently for Docker and local runs.

    Relative values are anchored at the repository root, not at whatever
    directory happened to be current when the launcher was invoked.
    """
    configured = Path(os.getenv(name, default)).expanduser()
    return configured if configured.is_absolute() else BASE_DIR / configured


TEMP_DIR = _local_path_from_env("TEMP_DIR", "local_data/temp")
LOG_DIR = _local_path_from_env("LOGS_DIR", "local_data/logs")
COOKIE_DIR = _local_path_from_env("COOKIE_DIR", "local_data/cookies")

for directory in (
    TEMP_DIR,
    LOG_DIR,
    COOKIE_DIR,
):
    directory.mkdir(parents=True, exist_ok=True)

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
REDIS_URL = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")
cache = RedisCache(url=REDIS_URL, fallback=True)
