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


def _env_float(
    name: str,
    default: float,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


YOUTUBE_RESEARCH_ENABLED = _env_bool("YOUTUBE_RESEARCH_ENABLED", False)
YOUTUBE_RESEARCH_MAX_CONCURRENT_JOBS = _env_int(
    "YOUTUBE_RESEARCH_MAX_CONCURRENT_JOBS", 1, minimum=1,
)
YOUTUBE_RESEARCH_MAX_RESULTS = _env_int("YOUTUBE_RESEARCH_MAX_RESULTS", 50, minimum=1)
YOUTUBE_RESEARCH_MAX_COMMENTS = _env_int("YOUTUBE_RESEARCH_MAX_COMMENTS", 100, minimum=0)
YOUTUBE_RESEARCH_HTTP_TIMEOUT = _env_int("YOUTUBE_RESEARCH_HTTP_TIMEOUT", 20, minimum=1)
YOUTUBE_RESEARCH_CACHE_TTL = _env_int("YOUTUBE_RESEARCH_CACHE_TTL", 21600, minimum=0)
YOUTUBE_RESEARCH_ENABLE_AI = _env_bool("YOUTUBE_RESEARCH_ENABLE_AI", False)
YOUTUBE_RESEARCH_ENABLE_LOCAL_EMBEDDINGS = _env_bool(
    "YOUTUBE_RESEARCH_ENABLE_LOCAL_EMBEDDINGS", False,
)
YOUTUBE_RESEARCH_ENABLE_OCR = _env_bool("YOUTUBE_RESEARCH_ENABLE_OCR", False)
YOUTUBE_RESEARCH_ENABLE_THUMBNAIL_FACE_DETECTION = _env_bool(
    "YOUTUBE_RESEARCH_ENABLE_THUMBNAIL_FACE_DETECTION", False,
)


def is_ai_channel_agent_enabled() -> bool:
    """Read the opt-in Channel Agent flag from the centralized configuration."""

    return _env_bool("AI_CHANNEL_AGENT_ENABLED", False)


AI_CHANNEL_AGENT_ENABLED = is_ai_channel_agent_enabled()


def channel_agent_brain_settings() -> dict[str, object]:
    """Read bounded CP4 local-AI settings without contacting Ollama."""

    legacy_num_predict = os.getenv("CHANNEL_AGENT_BRAIN_NUM_PREDICT", "").strip()

    def mode_num_predict(name: str, default: int, hard_cap: int) -> int:
        raw = os.getenv(name, "").strip() or legacy_num_predict
        try:
            value = int(raw) if raw else default
        except (TypeError, ValueError):
            value = default
        return min(hard_cap, max(256, value))

    return {
        "enabled": _env_bool("CHANNEL_AGENT_OLLAMA_ENABLED", True),
        "base_url": os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").strip().rstrip("/"),
        "model": os.getenv("OLLAMA_MODEL", "").strip(),
        "timeout_seconds": _env_int("CHANNEL_AGENT_BRAIN_TIMEOUT_SECONDS", 120, minimum=5),
        "max_evidence_items": min(
            40, _env_int("CHANNEL_AGENT_BRAIN_MAX_EVIDENCE_ITEMS", 18, minimum=5)
        ),
        "max_prompt_chars": min(
            100_000, _env_int("CHANNEL_AGENT_BRAIN_MAX_PROMPT_CHARS", 30_000, minimum=15_000)
        ),
        "temperature_analysis": _env_float(
            "CHANNEL_AGENT_BRAIN_TEMPERATURE_ANALYSIS", 0.15, 0.0, 1.0
        ),
        "temperature_creative": _env_float(
            "CHANNEL_AGENT_BRAIN_TEMPERATURE_CREATIVE", 0.35, 0.0, 1.0
        ),
        "repair_temperature": _env_float(
            "CHANNEL_AGENT_BRAIN_REPAIR_TEMPERATURE", 0.0, 0.0, 0.2
        ),
        "top_p": _env_float("CHANNEL_AGENT_BRAIN_TOP_P", 0.85, 0.1, 1.0),
        "num_predict_by_mode": {
            "opportunity_analysis": mode_num_predict(
                "CHANNEL_AGENT_BRAIN_NUM_PREDICT_OPPORTUNITY_ANALYSIS", 900, 1_200
            ),
            "content_angles": mode_num_predict(
                "CHANNEL_AGENT_BRAIN_NUM_PREDICT_CONTENT_ANGLES", 1_100, 1_400
            ),
            "title_hooks": mode_num_predict(
                "CHANNEL_AGENT_BRAIN_NUM_PREDICT_TITLE_HOOKS", 900, 1_200
            ),
            "longform_outline": mode_num_predict(
                "CHANNEL_AGENT_BRAIN_NUM_PREDICT_LONGFORM_OUTLINE", 1_600, 2_000
            ),
        },
    }


def channel_agent_production_generation_settings() -> dict[str, object]:
    """Bounded local-only CP7A text/structured generation settings."""

    def bounded_predict(name: str, default: int, maximum: int) -> int:
        return min(maximum, _env_int(name, default, minimum=256))

    def bounded_int(name: str, default: int, minimum: int, maximum: int) -> int:
        return min(maximum, _env_int(name, default, minimum=minimum))

    return {
        "words_per_minute": bounded_int(
            "CHANNEL_AGENT_SCRIPT_WORDS_PER_MINUTE", 145, 80, 220
        ),
        "default_section_count": bounded_int(
            "CHANNEL_AGENT_SCRIPT_SECTION_COUNT", 8, 4, 12
        ),
        "max_prompt_chars": bounded_int(
            "CHANNEL_AGENT_PRODUCTION_MAX_PROMPT_CHARS", 24_000, 8_000, 60_000,
        ),
        "temperature": _env_float(
            "CHANNEL_AGENT_PRODUCTION_TEMPERATURE", 0.25, 0.0, 0.8
        ),
        "repair_temperature": _env_float(
            "CHANNEL_AGENT_PRODUCTION_REPAIR_TEMPERATURE", 0.0, 0.0, 0.2
        ),
        "top_p": _env_float(
            "CHANNEL_AGENT_PRODUCTION_TOP_P", 0.85, 0.1, 1.0
        ),
        "num_predict_by_asset": {
            "script_blueprint": bounded_predict(
                "CHANNEL_AGENT_PRODUCTION_NUM_PREDICT_BLUEPRINT", 1_200, 1_600
            ),
            "script_section": bounded_predict(
                "CHANNEL_AGENT_PRODUCTION_NUM_PREDICT_SECTION", 1_800, 2_400
            ),
            "visual_plan": bounded_predict(
                "CHANNEL_AGENT_PRODUCTION_NUM_PREDICT_VISUAL", 1_200, 1_400
            ),
            "voice_plan": bounded_predict(
                "CHANNEL_AGENT_PRODUCTION_NUM_PREDICT_VOICE", 700, 1_000
            ),
            "thumbnail_brief": bounded_predict(
                "CHANNEL_AGENT_PRODUCTION_NUM_PREDICT_THUMBNAIL", 800, 1_100
            ),
            "metadata_package": bounded_predict(
                "CHANNEL_AGENT_PRODUCTION_NUM_PREDICT_METADATA", 1_100, 1_500
            ),
        },
    }

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
