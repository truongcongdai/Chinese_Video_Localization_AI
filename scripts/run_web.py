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

import uvicorn


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
