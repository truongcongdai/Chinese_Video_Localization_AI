"""Nuitka entry point for the Windows standalone distribution."""
from __future__ import annotations

import logging
import os
import secrets
import sys
import threading
import webbrowser
from pathlib import Path


def application_dir() -> Path:
    if getattr(sys, "frozen", False) or "__compiled__" in globals():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


APP_DIR = application_dir()
os.chdir(APP_DIR)

# Make bundled FFmpeg discoverable by every subprocess in the application.
ffmpeg_dir = APP_DIR / "ffmpeg" / "bin"
if ffmpeg_dir.is_dir():
    os.environ["PATH"] = str(ffmpeg_dir) + os.pathsep + os.environ.get("PATH", "")

# Fix Windows console encoding for subprocess calls (demucs, etc.)
os.environ["PYTHONIOENCODING"] = "utf-8"
# yt-dlp's generated lazy registry is enormous and offers only startup-time
# optimization. The normal extractor registry has identical capabilities and
# avoids a single 200k+ line C translation unit that MSVC cannot compile.
os.environ.setdefault("YTDLP_NO_LAZY_EXTRACTORS", "1")

env_path = APP_DIR / ".env"
if not env_path.exists():
    env_path.write_text(
        "# Generated locally on first launch. Do not share this file.\n"
        f"WEB_SESSION_SECRET={secrets.token_urlsafe(48)}\n"
        "WEB_HOST=127.0.0.1\n"
        "WEB_PORT=8080\n",
        encoding="utf-8",
    )

from dotenv import load_dotenv

load_dotenv(env_path, override=False)
os.environ.setdefault("TEMP_DIR", str(APP_DIR / "local_data" / "temp"))
os.environ.setdefault("LOGS_DIR", str(APP_DIR / "local_data" / "logs"))
os.environ.setdefault("COOKIE_DIR", str(APP_DIR / "local_data" / "cookies"))
os.environ.setdefault("WEB_DB_PATH", str(APP_DIR / "local_data" / "database.sqlite3"))
os.environ.setdefault("LICENSED_MUSIC_DIR", str(APP_DIR / "local_data" / "music"))
bundled_browsers = APP_DIR / "playwright-browsers"
if bundled_browsers.is_dir():
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(bundled_browsers))


def main() -> None:
    import uvicorn
    from universal_video_ai.web.app import app

    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    port = int(os.environ.get("WEB_PORT", "8080"))
    host = os.environ.get("WEB_HOST", "127.0.0.1").strip() or "127.0.0.1"
    threading.Timer(1.5, lambda: webbrowser.open(f"http://127.0.0.1:{port}")).start()
    uvicorn.run(app, host=host, port=port, log_level=os.environ.get("LOG_LEVEL", "info").lower())


if __name__ == "__main__":
    main()
