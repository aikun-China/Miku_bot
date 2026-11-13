@echo off
chcp 65001 >nul
setlocal EnableExtensions
cd /d "%~dp0"
title MikuBot

set "VPY=%~dp0.venv\Scripts\python.exe"
set "PYCFG=%~dp0.venv\pyvenv.cfg"
set "EXPECT=%~dp0runtime\python"

if not exist "%VPY%" goto :need_deploy
if not exist "%PYCFG%" goto :need_deploy
findstr /I /C:"home = %EXPECT%" "%PYCFG%" >nul 2>&1
if errorlevel 1 goto :moved
goto :run

:moved
echo [提示] 检测到所在目录变化，正在重建虚拟环境 ...
call "%~dp0首次部署.bat"
if errorlevel 1 goto :abort
goto :run

:need_deploy
echo [提示] 尚未部署，正在执行首次部署 ...
call "%~dp0首次部署.bat"
if errorlevel 1 goto :abort

:run
if not exist "%~dp0.env" if exist "%~dp0.env.example" copy /y "%~dp0.env.example" "%~dp0.env" >nul
if exist "%~dp0patches\sitecustomize.py" if not exist "%~dp0.venv\Lib\site-packages\sitecustomize.py" copy /y "%~dp0patches\sitecustomize.py" "%~dp0.venv\Lib\site-packages\sitecustomize.py" >nul
echo =========================================
echo    MikuBot 启动中
echo    运行时 %VPY%
echo =========================================
echo.
"%VPY%" bot.py
echo.
echo [信息] Bot 已退出
pause
exit /b 0

:abort
echo [错误] 部署未完成，无法启动。
pause
exit /b 1