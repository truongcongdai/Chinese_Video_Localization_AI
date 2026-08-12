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

# Load .env from parent directory (dist folder when running from exe)
repo_root = script_dir.parent
env_file = repo_root / ".env"
if env_file.exists():
    load_dotenv(env_file)

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
        print(f"Repo root: {repo_root}")
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
