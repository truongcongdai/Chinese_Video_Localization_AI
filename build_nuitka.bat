@echo off
setlocal
cd /d "%~dp0"

REM Modes:
REM   release (default) - build, validate and create the release ZIP
REM   fast              - build and validate a runnable directory, no ZIP
REM   package           - validate and ZIP the existing runnable directory
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0packaging\build_windows.ps1" %*
if errorlevel 1 (
  echo.
  echo Nuitka build failed. Review the error above; no release ZIP was produced.
  exit /b 1
)

echo.
echo Build pipeline completed successfully.
exit /b 0
