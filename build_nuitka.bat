@echo off
setlocal
cd /d "%~dp0"

REM Canonical Nuitka release pipeline. It builds a standalone directory,
REM bundles FFmpeg and Chromium, rejects secrets/user data, then creates ZIP.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0packaging\build_windows.ps1"
if errorlevel 1 (
  echo.
  echo Nuitka build failed. Review the error above; no release ZIP was produced.
  exit /b 1
)

echo.
echo Release ready: build\windows\ChineseVideoAI-Windows-x64.zip
exit /b 0
