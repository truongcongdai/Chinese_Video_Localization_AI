@echo off
setlocal enabledelayedexpansion

echo ========================================
echo Universal Video AI - Installer
echo ========================================
echo.

REM Check Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python not found. Please install Python 3.9+ from https://python.org
    pause
    exit /b 1
)

echo [OK] Python found
python --version
echo.

REM Check if in project root
if not exist "requirements.txt" (
    echo [ERROR] requirements.txt not found. Please run this script from the project root directory.
    pause
    exit /b 1
)

echo [OK] In project root directory
echo.

REM Create virtual environment if not exists
if not exist ".venv" (
    echo Creating virtual environment...
    python -m venv .venv
    if %errorlevel% neq 0 (
        echo [ERROR] Failed to create virtual environment
        pause
        exit /b 1
    )
    echo [OK] Virtual environment created
) else (
    echo [OK] Virtual environment already exists
)
echo.

REM Activate virtual environment
echo Activating virtual environment...
call .venv\Scripts\activate.bat
if %errorlevel% neq 0 (
    echo [ERROR] Failed to activate virtual environment
    pause
    exit /b 1
)
echo [OK] Virtual environment activated
echo.

REM Upgrade pip
echo Upgrading pip...
python -m pip install --upgrade pip
echo.

REM Install dependencies
echo Installing dependencies...
pip install -r requirements.txt
if %errorlevel% neq 0 (
    echo [ERROR] Failed to install dependencies
    pause
    exit /b 1
)
echo [OK] Dependencies installed
echo.

REM Install optional dependencies
echo Installing optional dependencies (this may take a while)...
pip install edge-tts
if %errorlevel% neq 0 (
    echo [WARNING] Failed to install edge-tts (optional)
) else (
    echo [OK] edge-tts installed
)

pip install openai
if %errorlevel% neq 0 (
    echo [WARNING] Failed to install openai (optional)
) else (
    echo [OK] openai installed
)
echo.

REM Check FFmpeg
ffmpeg -version >nul 2>&1
if %errorlevel% neq 0 (
    echo [WARNING] FFmpeg not found in PATH
    echo.
    echo Please install FFmpeg:
    echo 1. Download from https://ffmpeg.org/download.html#build-windows
    echo 2. Extract to a folder (e.g., C:\ffmpeg)
    echo 3. Add C:\ffmpeg\bin to your PATH environment variable
    echo.
    echo Or use winget: winget install ffmpeg
    echo.
    set /p FFmpegContinue="Press Enter after installing FFmpeg, or type 'skip' to continue anyway: "
    if /i "!FFmpegContinue!"=="skip" (
        echo [INFO] Skipping FFmpeg check (video processing may not work)
    ) else (
        ffmpeg -version >nul 2>&1
        if %errorlevel% neq 0 (
            echo [ERROR] FFmpeg still not found. Video processing will not work.
            pause
            exit /b 1
        )
        echo [OK] FFmpeg installed
    )
) else (
    echo [OK] FFmpeg found
    ffmpeg -version | findstr "ffmpeg version"
)
echo.

REM Create .env file if not exists
if not exist ".env" (
    echo Creating .env file...
    (
        echo # Universal Video AI Configuration
        echo.
        echo # AI Providers (optional - choose one or more)
        echo # GEMINI_API_KEY=your_gemini_api_key
        echo # OPENAI_API_KEY=your_openai_api_key
        echo # OLLAMA_BASE_URL=http://127.0.0.1:11434
        echo # OPENROUTER_API_KEY=your_openrouter_api_key
        echo.
        echo # Default AI Provider for script generation
        echo CREATOR_AI_PROVIDER=gemini
        echo.
        echo # Video/Audio Settings
        echo WEB_RENDER_PRESET=medium
        echo WEB_RENDER_TIMEOUT_SECONDS=1800
        echo.
        echo # Copyright-safe audio (optional)
        echo # Set to true to replace downloaded audio with licensed local music
        echo COPYRIGHT_SAFE_AUDIO=false
        echo LICENSED_MUSIC_DIR=local_data/music
        echo REPLACEMENT_MUSIC_VOLUME=0.3
        echo.
        echo # Server Settings
        echo WEB_HOST=0.0.0.0
        echo WEB_PORT=8000
    ) > .env
    echo [OK] .env file created
    echo [INFO] Please edit .env to add your API keys if needed
) else (
    echo [OK] .env file already exists
)
echo.

REM Create necessary directories
if not exist "local_data" mkdir local_data
if not exist "local_data\music" mkdir local_data\music
if not exist "local_data\uploads" mkdir local_data\uploads
if not exist "local_data\temp" mkdir local_data\temp
echo [OK] Created necessary directories
echo.

REM Download Whisper model if needed (optional)
echo.
echo Download Whisper model for speech-to-text?
echo.
choice /C YN /M "Press Y for Yes, N for No"
if %errorlevel% equ 1 (
    echo Downloading Whisper model (this may take a while)...
    pip install openai-whisper
    if %errorlevel% neq 0 (
        echo [WARNING] Failed to install Whisper
    ) else (
        echo [OK] Whisper installed
        echo.
        echo Whisper model will be downloaded automatically on first use
    )
)
echo.

echo ========================================
echo Installation Complete!
echo ========================================
echo.
echo To start the web UI:
echo   1. Activate virtual environment: .venv\Scripts\activate.bat
echo   2. Run: python scripts\run_web.py
echo.
echo Or simply run: start.bat
echo.
echo The web UI will be available at http://localhost:8000
echo.
pause
