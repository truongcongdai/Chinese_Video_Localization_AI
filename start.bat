@echo off
setlocal enabledelayedexpansion

echo ========================================
echo Universal Video AI - Starting Web UI
echo ========================================
echo.

REM Check if virtual environment exists
if not exist ".venv" (
    echo [ERROR] Virtual environment not found.
    echo Please run install.bat first to set up the environment.
    pause
    exit /b 1
)

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

REM Check FFmpeg
ffmpeg -version >nul 2>&1
if %errorlevel% neq 0 (
    echo [WARNING] FFmpeg not found in PATH
    echo Video processing may not work correctly.
    echo Please install FFmpeg or run install.bat
    echo.
)

REM Check if .env exists
if not exist ".env" (
    echo [WARNING] .env file not found
    echo Using default configuration
    echo.
)

REM Start the web server
echo Starting web server...
echo.
echo The web UI will be available at http://localhost:8000
echo Press Ctrl+C to stop the server
echo.
echo ========================================
echo.

python scripts\run_web.py

if %errorlevel% neq 0 (
    echo.
    echo [ERROR] Server stopped with error code %errorlevel%
    pause
)
