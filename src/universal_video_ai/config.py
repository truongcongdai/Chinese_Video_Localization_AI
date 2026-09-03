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

from universal_video_ai.cache import RedisCache, SQLiteCache

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
TRANSLATION_CACHE_PATH = _local_path_from_env(
    "TRANSLATION_CACHE_PATH", "local_data/temp/translation_cache.sqlite3"
)
translation_cache = SQLiteCache(TRANSLATION_CACHE_PATH)
SPEECH_CACHE_PATH = _local_path_from_env(
    "SPEECH_CACHE_PATH", "local_data/temp/speech_cache.sqlite3"
)
speech_cache = SQLiteCache(SPEECH_CACHE_PATH)
DEMUCS_CACHE_DIR = _local_path_from_env(
    "DEMUCS_CACHE_DIR", "local_data/temp/demucs_cache"
)
DEMUCS_CACHE_DIR.mkdir(parents=True, exist_ok=True)



def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, minimum: int | None = None) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    if minimum is not None:
        value = max(minimum, value)
    return value


YOUTUBE_RESEARCH_ENABLED = _env_bool("YOUTUBE_RESEARCH_ENABLED", False)
YOUTUBE_RESEARCH_MAX_CONCURRENT_JOBS = min(4, _env_int(
    "YOUTUBE_RESEARCH_MAX_CONCURRENT_JOBS", 1, minimum=1,
))
YOUTUBE_RESEARCH_MAX_RESULTS = min(
    100, _env_int("YOUTUBE_RESEARCH_MAX_RESULTS", 50, minimum=1)
)
YOUTUBE_RESEARCH_MAX_COMMENTS = _env_int("YOUTUBE_RESEARCH_MAX_COMMENTS", 100, minimum=0)
YOUTUBE_RESEARCH_HTTP_TIMEOUT = min(
    120, _env_int("YOUTUBE_RESEARCH_HTTP_TIMEOUT", 20, minimum=1)
)
YOUTUBE_RESEARCH_CACHE_TTL = _env_int("YOUTUBE_RESEARCH_CACHE_TTL", 21600, minimum=0)
YOUTUBE_RESEARCH_ENABLE_AI = _env_bool("YOUTUBE_RESEARCH_ENABLE_AI", False)
YOUTUBE_RESEARCH_ENABLE_LOCAL_EMBEDDINGS = _env_bool(
    "YOUTUBE_RESEARCH_ENABLE_LOCAL_EMBEDDINGS", False,
)
YOUTUBE_RESEARCH_ENABLE_OCR = _env_bool("YOUTUBE_RESEARCH_ENABLE_OCR", False)
YOUTUBE_RESEARCH_ENABLE_THUMBNAIL_FACE_DETECTION = _env_bool(
    "YOUTUBE_RESEARCH_ENABLE_THUMBNAIL_FACE_DETECTION", False,
)

# AI Content OS Feature Flag
CONTENT_OS_ENABLED = _env_bool("CONTENT_OS_ENABLED", False)
CONTENT_OS_MAX_AUTO_REVISIONS = _env_int("CONTENT_OS_MAX_AUTO_REVISIONS", 1, minimum=0)
CONTENT_OS_MAX_SOURCE_ITEMS = _env_int("CONTENT_OS_MAX_SOURCE_ITEMS", 20, minimum=1)
CONTENT_OS_ARTIFACT_DIR = _local_path_from_env("CONTENT_OS_ARTIFACT_DIR", "local_data/content_os")
CONTENT_OS_PROVIDER_TIMEOUT_SECONDS = _env_int("CONTENT_OS_PROVIDER_TIMEOUT_SECONDS", 30, minimum=5)
_CONTENT_OS_DEFAULT_LLM_PROVIDER = "gemini" if (
    os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_AI_API_KEY")
) else "ollama"
CONTENT_OS_LLM_PROVIDER = os.getenv("CONTENT_OS_LLM_PROVIDER", _CONTENT_OS_DEFAULT_LLM_PROVIDER)
CONTENT_OS_LLM_MODEL = os.getenv("CONTENT_OS_LLM_MODEL", "")
CONTENT_OS_LLM_BASE_URL = os.getenv("CONTENT_OS_LLM_BASE_URL", "http://localhost:11434/v1")
CONTENT_OS_LLM_API_KEY = os.getenv("CONTENT_OS_LLM_API_KEY", "")
CONTENT_OS_REQUIRE_RENDER_APPROVAL = _env_bool("CONTENT_OS_REQUIRE_RENDER_APPROVAL", True)
CONTENT_OS_REQUIRE_PUBLISH_APPROVAL = _env_bool("CONTENT_OS_REQUIRE_PUBLISH_APPROVAL", True)
