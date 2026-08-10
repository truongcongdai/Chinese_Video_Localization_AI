@echo off
REM Build script for packaging with Nuitka (better code protection than PyInstaller)
REM Nuitka compiles Python to C then to native machine code
REM Usage: Run this script on Windows with Python 3.10+ and Visual C++ Build Tools installed

echo ========================================
echo Building with Nuitka (Code Protection)
echo ========================================
echo.

REM Check if Python is installed
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo ERROR: Python is not installed or not in PATH
    echo Please install Python 3.10 or higher from https://www.python.org/
    pause
    exit /b 1
)

echo [1/6] Installing Nuitka...
pip install nuitka
if %errorlevel% neq 0 (
    echo ERROR: Failed to install Nuitka
    pause
    exit /b 1
)

echo.
echo [2/6] Installing project dependencies...
pip install -r requirements.txt
if %errorlevel% neq 0 (
    echo ERROR: Failed to install dependencies
    pause
    exit /b 1
)

echo.
echo [3/6] Installing Visual C++ Build Tools if needed...
echo If build fails, install from: https://visualstudio.microsoft.com/visual-cpp-build-tools/
echo Select "Desktop development with C++" during installation
echo.

echo [4/6] Building with Nuitka...
echo This will take 20-60 minutes depending on your CPU...
echo.

python -m nuitka ^
    --standalone ^
    --onefile ^
    --enable-plugin=anti-bloat ^
    --enable-plugin=numpy ^
    --enable-plugin=pylint-warnings ^
    --windows-console-mode=force ^
    --windows-icon-from-ico=none ^
    --output-filename=ChineseVideoLocalizationAI.exe ^
    --include-data-dir=src/universal_video_ai/web/static=universal_video_ai/web/static ^
    --include-data-file=.env.example=.env.example ^
    --include-module=universal_video_ai.web.app ^
    --include-module=universal_video_ai.orchestrator ^
    --include-module=universal_video_ai.config ^
    --include-module=universal_video_ai.license ^
    --include-package=universal_video_ai ^
    --assume-yes-for-downloads ^
    --show-progress ^
    --show-memory ^
    --show-release-memory ^
    scripts/run_web.py

if %errorlevel% neq 0 (
    echo ERROR: Nuitka build failed
    echo.
    echo Common issues:
    echo 1. Visual C++ Build Tools not installed
    echo    Download: https://visualstudio.microsoft.com/visual-cpp-build-tools/
    echo 2. Missing C compiler
    echo 3. Insufficient disk space
    pause
    exit /b 1
)

echo.
echo [5/6] Copying additional files...
if exist ".env" (
    copy ".env" ".\ChineseVideoLocalizationAI.env"
    echo Copied .env file
) else (
    echo WARNING: .env file not found, using .env.example
    copy ".env.example" ".\ChineseVideoLocalizationAI.env"
)

if not exist "temp" mkdir "temp"
if not exist "local_data" mkdir "local_data"

echo.
echo [6/6] Build completed successfully!
echo.
echo Output: ChineseVideoLocalizationAI.exe
echo.
echo IMPORTANT NOTES:
echo 1. Nuitka compiles to native machine code - much harder to decompile
echo 2. File size will be 2-5 GB due to PyTorch and ML dependencies
echo 3. First run may take longer as models are downloaded
echo 4. Edit ChineseVideoLocalizationAI.env to configure settings
echo 5. Make sure to set WEB_SESSION_SECRET in .env before running
echo.
echo CODE PROTECTION:
echo - Nuitka compiles Python to C then to machine code
echo - Much harder to reverse engineer than PyInstaller
echo - Not 100% unbreakable, but significantly better
echo - For absolute security, use server deployment instead
echo.
pause
