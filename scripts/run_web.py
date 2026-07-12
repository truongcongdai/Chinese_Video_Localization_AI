#!/usr/bin/env python3
"""
Entry point for the web UI (replaces scripts/run_bot.py's Telegram bot for
people who'd rather use a browser). Run:

    python scripts/run_web.py

Requires WEB_SESSION_SECRET to be set in your environment/.env (see
web/auth.py / README_WEB.md).
"""
import logging
import os
from pathlib import Path

import uvicorn

# Load .env before anything else touches os.environ — belt-and-suspenders
# alongside universal_video_ai.config doing the same on import, so this
# works correctly even if something changes import order later. Points at
# the repo root's .env explicitly (one level up from this scripts/ file)
# rather than relying on the process's current working directory, since
# people commonly launch this script from elsewhere (e.g. an IDE, or a
# different folder) — that mismatch was the actual root cause of
# "WEB_SESSION_SECRET is not set" even when .env had a value in it.
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
except ImportError:
    pass


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    port = int(os.environ.get("WEB_PORT", "8080"))
    uvicorn.run(
        "universal_video_ai.web.app:app",
        host="0.0.0.0",
        port=port,
        log_level=os.environ.get("LOG_LEVEL", "info").lower(),
    )


if __name__ == "__main__":
    main()
