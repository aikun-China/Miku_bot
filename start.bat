@echo off
chcp 65001 >nul
cd /d "%~dp0"
title MikuBot

echo =========================================
echo   MikuBot Starting...
echo =========================================

REM 1. Check virtual environment
if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] Virtual environment not found
    echo Please run: python -m venv .venv
    pause
    exit /b 1
)
echo [OK] Virtual environment found

REM 2. Check dependencies
echo.
".venv\Scripts\python.exe" -c "import nonebot" >nul 2>nul
if errorlevel 1 (
    echo [INFO] Dependencies not found, installing...
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [ERROR] Dependency installation failed
        pause
        exit /b 1
    )
    echo [OK] Dependencies installed
) else (
    echo [OK] Dependencies ready
)

REM 3. Start Bot
echo.
echo =========================================
echo   Starting MikuBot...
echo =========================================
echo.

".venv\Scripts\python.exe" bot.py

if errorlevel 1 (
    echo.
    echo =========================================
    echo   [ERROR] Bot exited with code: %errorlevel%
    echo =========================================
) else (
    echo.
    echo [INFO] Bot exited normally
)
echo.
pause
