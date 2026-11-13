@echo off
chcp 65001 >nul
setlocal EnableExtensions
cd /d "%~dp0"
title MikuBot 首次部署

set "PY=%~dp0runtime\python\python.exe"
set "UV=%~dp0runtime\uv\uv.exe"
set "UV_PYTHON_DOWNLOADS=never"
set "UV_LINK_MODE=copy"

echo =========================================
echo    MikuBot 一键部署
echo    内置便携 Python 与 uv，无需预装环境
echo =========================================
echo.

if not exist "%PY%" goto :missing
if not exist "%UV%" goto :missing

if exist "%~dp0.env" goto :env_ready
if not exist "%~dp0.env.example" goto :env_ready
copy /y "%~dp0.env.example" "%~dp0.env" >nul
echo [OK] 已生成 .env，首次启动会引导设置管理员
:env_ready

if exist "%~dp0.venv\Scripts\python.exe" goto :venv_ready
echo [步骤 1/3] 用包内便携 Python 创建虚拟环境 ...
"%UV%" venv "%~dp0.venv" --python "%PY%" --seed --clear
if errorlevel 1 goto :failed
:venv_ready
echo [OK] 虚拟环境 .venv 就绪

echo.
echo [步骤 2/3] 安装依赖，首次约 3 到 8 分钟 ...
"%UV%" sync --frozen --no-install-project --python "%PY%"
if errorlevel 1 goto :resync
goto :patch

:resync
echo.
echo [警告] 锁定依赖安装失败，改为重新解析依赖再试一次 ...
"%UV%" sync --no-install-project --python "%PY%"
if errorlevel 1 goto :failed

:patch
echo.
echo [步骤 3/3] 安装运行时兼容补丁 ...
if not exist "%~dp0patches\sitecustomize.py" goto :done
if not exist "%~dp0.venv\Lib\site-packages" goto :done
copy /y "%~dp0patches\sitecustomize.py" "%~dp0.venv\Lib\site-packages\sitecustomize.py" >nul
echo [OK] 已补回 Pillow 10 移除的 Image.ANTIALIAS 与 getsize

:done
echo.
echo =========================================
echo    部署完成，双击「一键启动.bat」运行
echo =========================================
echo.
pause
exit /b 0

:missing
echo.
echo [错误] 包内运行时缺失，请重新完整解压后重试：
echo        %PY%
echo        %UV%
echo.
pause
exit /b 1

:failed
echo.
echo [错误] 部署失败，排查建议：
echo        1. 确认网络可用
echo        2. 解压路径尽量不含特殊字符
echo        3. 如镜像源不可用，可先设置 UV_DEFAULT_INDEX 再重试
echo.
pause
exit /b 1