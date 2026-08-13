@echo off
REM Set License Server URL and User Management Server URL in Windows Registry
REM Run as Administrator

if "%1"=="" (
    echo Usage: set_license_server.bat ^<LICENSE_SERVER_URL^>
    echo Example: set_license_server.bat http://192.168.6.10:8000
    echo Example: set_license_server.bat http://113.160.14.1:8000
    echo.
    echo This will automatically set USER_MANAGEMENT_SERVER_URL to the same host with port 8001
    exit /b 1
)

set "LICENSE_SERVER_URL=%1"

REM Derive USER_MANAGEMENT_SERVER_URL by replacing port 8000 with 8001
set "USER_MANAGEMENT_SERVER_URL=%LICENSE_SERVER_URL:8000=8001%"

REM Check if running as Administrator
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo ERROR: This script must be run as Administrator
    echo Right-click Command Prompt and select "Run as Administrator"
    exit /b 1
)

REM Create registry key if it doesn't exist
reg add "HKLM\SOFTWARE\ChineseVideoLocalizationAI" /f >nul 2>&1

REM Set LICENSE_SERVER_URL
reg add "HKLM\SOFTWARE\ChineseVideoLocalizationAI" /v LICENSE_SERVER_URL /t REG_SZ /d "%LICENSE_SERVER_URL%" /f

REM Set USER_MANAGEMENT_SERVER_URL
reg add "HKLM\SOFTWARE\ChineseVideoLocalizationAI" /v USER_MANAGEMENT_SERVER_URL /t REG_SZ /d "%USER_MANAGEMENT_SERVER_URL%" /f

if %errorLevel% equ 0 (
    echo SUCCESS: LICENSE_SERVER_URL set to %LICENSE_SERVER_URL%
    echo SUCCESS: USER_MANAGEMENT_SERVER_URL set to %USER_MANAGEMENT_SERVER_URL%
    echo Registry Key: HKLM\SOFTWARE\ChineseVideoLocalizationAI
    echo.
    echo Restart the application to apply changes
) else (
    echo ERROR: Failed to set URLs in registry
    exit /b 1
)
