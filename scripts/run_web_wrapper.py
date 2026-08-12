#!/usr/bin/env python3
"""
Wrapper script for PyInstaller - ensures sys.path is set correctly
"""
import sys
import os
from pathlib import Path

# Add src directory to sys.path
script_dir = Path(__file__).parent.resolve()
src_dir = script_dir.parent / "src"
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

# Add parent directory to sys.path (for PyInstaller)
if str(script_dir.parent) not in sys.path:
    sys.path.insert(0, str(script_dir.parent))

# Now import and run the app
import uvicorn
from dotenv import load_dotenv

# Load .env from multiple possible locations
# When running from .exe, the current directory is dist/
# We need to check dist/.env and dist/ChineseVideoLocalizationAI/.env
env_locations = [
    Path.cwd() / ".env",  # Current directory (dist/)
    Path.cwd() / "ChineseVideoLocalizationAI" / ".env",  # Subdirectory
    Path(__file__).parent.parent / ".env",  # Parent of scripts (repo root)
    Path(__file__).parent / ".env",  # Same directory as script
    Path(__file__).parent.parent / "dist" / ".env",  # dist/ folder
    Path(__file__).parent.parent / "dist" / "ChineseVideoLocalizationAI" / ".env",  # dist/ChineseVideoLocalizationAI/
]

env_loaded = False
for env_file in env_locations:
    if env_file.exists():
        print(f"Loading .env from: {env_file}")
        load_dotenv(env_file)
        env_loaded = True
        break

if not env_loaded:
    print("WARNING: .env file not found in any of these locations:")
    for loc in env_locations:
        print(f"  - {loc}")
    print("WEB_SESSION_SECRET may not be set!")

def main():
    # Import the app directly instead of using string
    try:
        from universal_video_ai.web.app import app
    except ImportError as e:
        print(f"ERROR: Failed to import app: {e}")
        print(f"sys.path: {sys.path}")
        print(f"Current directory: {os.getcwd()}")
        print(f"Script directory: {script_dir}")
        print(f"Src directory: {src_dir}")
        print(f"Repo root: {script_dir.parent}")
        raise

    port = int(os.environ.get("WEB_PORT", "8080"))
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=port,
        log_level=os.environ.get("LOG_LEVEL", "info").lower(),
    )

if __name__ == "__main__":
    main()
