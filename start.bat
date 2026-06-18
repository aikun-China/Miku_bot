@echo off
chcp 936 >nul
cd /d "%~dp0"

echo =========================================
echo MikuBot 启动
echo =========================================

REM 1. 检查 .venv
if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] 虚拟环境不存在！
    echo [INFO] 请先用系统 Python 创建虚拟环境:
    echo     python -m venv .venv
    pause
    exit /b 1
)
echo [OK] 虚拟环境已存在

REM 2. 检测依赖
echo.
".venv\Scripts\python.exe" -c "import nonebot" >nul 2>nul
if errorlevel 1 (
    echo [INFO] 依赖缺失，正在安装...
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [ERROR] 依赖安装失败
        pause
        exit /b 1
    )
    echo [OK] 依赖安装完成
) else (
    echo [OK] 依赖已安装
)

REM 3. 检测 Edge 浏览器
echo.
if exist "C:\Program Files\Microsoft\Edge\Application\msedge.exe" (
    echo [OK] Edge 浏览器已安装
) else if exist "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe" (
    echo [OK] Edge 浏览器已安装
) else (
    echo [WARN] 未检测到 Edge 浏览器
    echo [INFO] 截图功能需要 Edge，请安装: https://www.microsoft.com/edge
)

REM 4. 启动 Bot
echo.
echo =========================================
echo 启动 MikuBot...
echo =========================================
echo.

".venv\Scripts\python.exe" bot.py

echo.
echo [INFO] Bot 已退出
pause
