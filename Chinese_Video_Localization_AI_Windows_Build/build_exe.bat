@echo off
REM Build script for packaging Chinese Video Localization AI into Windows executable
REM Usage: Run this script on Windows with Python 3.10+ installed

echo ========================================
echo Building Chinese Video Localization AI
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

echo [1/5] Installing PyInstaller...
pip install pyinstaller
if %errorlevel% neq 0 (
    echo ERROR: Failed to install PyInstaller
    pause
    exit /b 1
)

echo.
echo [2/5] Installing project dependencies...
pip install -r requirements.txt
if %errorlevel% neq 0 (
    echo ERROR: Failed to install dependencies
    echo Note: Some dependencies like PyTorch may require manual installation
    pause
    exit /b 1
)

echo.
echo [3/5] Building executable with PyInstaller...
pyinstaller build_exe.spec --clean
if %errorlevel% neq 0 (
    echo ERROR: PyInstaller build failed
    pause
    exit /b 1
)

echo.
echo [4/5] Copying additional files...
if exist ".env" (
    copy ".env" "dist\ChineseVideoLocalizationAI\.env"
    echo Copied .env file
) else (
    echo WARNING: .env file not found, using .env.example
    copy ".env.example" "dist\ChineseVideoLocalizationAI\.env"
)

if not exist "dist\ChineseVideoLocalizationAI\temp" mkdir "dist\ChineseVideoLocalizationAI\temp"
if not exist "dist\ChineseVideoLocalizationAI\local_data" mkdir "dist\ChineseVideoLocalizationAI\local_data"

echo.
echo [5/5] Build completed successfully!
echo.
echo Output directory: dist\ChineseVideoLocalizationAI\
echo Executable: dist\ChineseVideoLocalizationAI\ChineseVideoLocalizationAI.exe
echo.
echo IMPORTANT NOTES:
echo 1. The executable will be large (2-5 GB) due to PyTorch and ML dependencies
echo 2. First run may take longer as models are downloaded
echo 3. Edit .env file in the dist folder to configure your settings
echo 4. Make sure to set WEB_SESSION_SECRET in .env before running
echo.
pause
