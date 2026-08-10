@echo off
REM Script cài đặt dependencies cho build Windows EXE
REM Chạy script này trước khi build_nuitka.bat

echo ========================================
echo Cai dat dependencies cho build EXE
echo ========================================
echo.

REM Check Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo ERROR: Python khong duoc cai dat hoac khong co trong PATH
    echo Vui long cai Python 3.10 hoac 3.11 tu: https://www.python.org/downloads/
    echo QUAN TRONG: Check "Add Python to PATH" khi cai
    pause
    exit /b 1
)

echo [1/5] Python da duoc cai dat:
python --version
echo.

REM Check pip
pip --version >nul 2>&1
if %errorlevel% neq 0 (
    echo ERROR: pip khong duoc cai dat
    pause
    exit /b 1
)

echo [2/5] Upgrade pip...
python -m pip install --upgrade pip
if %errorlevel% neq 0 (
    echo ERROR: Khong the upgrade pip
    pause
    exit /b 1
)
echo.

echo [3/5] Cai dat dependencies tu requirements.txt...
pip install -r requirements.txt
if %errorlevel% neq 0 (
    echo ERROR: Khong the cai dependencies
    pause
    exit /b 1
)
echo.

echo [4/5] Cai dat Nuitka cho build EXE...
pip install nuitka
if %errorlevel% neq 0 (
    echo ERROR: Khong the cai Nuitka
    pause
    exit /b 1
)
echo.

echo [5/5] Kiem tra Visual C++ Build Tools...
where cl.exe >nul 2>&1
if %errorlevel% neq 0 (
    echo WARNING: Visual C++ Build Tools khong duoc tim thay
    echo Nuitka se can Visual C++ Build Tools de build
    echo Download tai: https://visualstudio.microsoft.com/visual-cpp-build-tools/
    echo Chon "Desktop development with C++" khi cai
    echo.
    echo Ban co muon tiep tuc khong? (y/n)
    set /p continue=
    if /i not "%continue%"=="y" (
        echo Huy cai dat.
        pause
        exit /b 1
    )
) else (
    echo Visual C++ Build Tools da duoc cai dat.
)
echo.

echo ========================================
echo Cai dat hoan tat!
echo ========================================
echo.
echo Tiep theo:
echo 1. Cau hinh .env file (copy tu .env.example)
echo 2. Chay build_nuitka.bat de build EXE
echo.
pause
