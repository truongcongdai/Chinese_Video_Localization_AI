@echo off
setlocal
cd /d "%~dp0"
"ChineseVideoAI.exe"
if errorlevel 1 (
  echo.
  echo ChineseVideoAI da dung voi loi. Nhan phim bat ky de dong.
  pause >nul
)
