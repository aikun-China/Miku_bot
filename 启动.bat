@echo off
chcp 936 >nul
cd /d "%~dp0"

echo =========================================
echo MikuBot Starting
echo =========================================

REM 1. Check virtual environment
if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] Virtual environment not found!
    echo [INFO] Please create it first:
    echo     python -m venv .venv
    pause
    exit /b 1
)
echo [OK] Virtual environment exists

REM 2. Check dependencies (bot.py will handle browser installation)
".venv\Scripts\python.exe" -c "import nonebot" >nul 2>nul
if errorlevel 1 (
    echo [INFO] Dependencies missing, installing...
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [ERROR] Dependency installation failed
        pause
        exit /b 1
    )
    echo [OK] Dependencies installed
) else (
    echo [OK] Dependencies already installed
)

REM 3. Start Bot (check_deps.py in bot.py will auto-install browser if needed)
echo.
echo [INFO] Starting MikuBot...
echo [INFO] If browser is not installed, it will be downloaded automatically
echo =========================================
".venv\Scripts\python.exe" bot.py

if errorlevel 1 (
    echo.
    echo =========================================
    echo [ERROR] Bot startup failed! Error code: %errorlevel%
    echo =========================================
) else (
    echo.
    echo [INFO] Bot exited normally
)
pause