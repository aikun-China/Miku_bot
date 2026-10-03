@echo off
chcp 65001 >nul
cd /d "%~dp0"
title MikuBot

echo =========================================
echo   MikuBot Starting (uv)...
echo =========================================

REM 1. Check uv
where uv >nul 2>nul
if errorlevel 1 (
    echo [ERROR] uv not found. Please install:
    echo   powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
    pause
    exit /b 1
)
echo [OK] uv found

REM 2. Sync dependencies (uv sync creates/reuses .venv)
echo.
echo [INFO] Syncing dependencies (uv sync)...
uv sync
if errorlevel 1 (
    echo [ERROR] uv sync failed
    pause
    exit /b 1
)
echo [OK] Dependencies ready

REM 3. Start Bot
echo.
echo =========================================
echo   Starting MikuBot...
echo =========================================
echo.

uv run python bot.py

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
