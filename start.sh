#!/bin/bash
# MikuBot 启动脚本 (Linux/Mac/Windows Git Bash)

cd "$(dirname "$0")" || exit 1

echo "========================================="
echo " MikuBot 启动"
echo "========================================="

# 检测操作系统，选择正确的虚拟环境路径
if [[ -f "./.venv/Scripts/python.exe" ]]; then
    VENV_PYTHON="./.venv/Scripts/python.exe"
elif [[ -f "./.venv/bin/python" ]]; then
    VENV_PYTHON="./.venv/bin/python"
else
    echo "[ERROR] 虚拟环境不存在"
    exit 1
fi

# 1. 检查 .venv
echo "[OK] 虚拟环境已存在"

# 2. 检测依赖
echo ""
if $VENV_PYTHON -c "import nonebot" 2>/dev/null; then
    echo "[OK] 依赖已安装"
else
    echo "[INFO] 依赖缺失，正在安装..."
    $VENV_PYTHON -m pip install -r requirements.txt
    echo "[OK] 依赖安装完成"
fi

# 3. 检测 Edge 浏览器
echo ""
if command -v microsoft-edge &> /dev/null || \
   command -v msedge &> /dev/null || \
   [[ -f "/usr/bin/microsoft-edge" ]] || \
   [[ -f "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge" ]]; then
    echo "[OK] Edge 浏览器已安装"
else
    echo "[WARN] 未检测到 Edge 浏览器"
    echo "[INFO] 截图功能需要 Edge，请安装: https://www.microsoft.com/edge"
fi

# 4. 启动 Bot
echo ""
echo "========================================="
echo " 启动 MikuBot..."
echo "========================================="
echo ""

$VENV_PYTHON bot.py

echo ""
echo "[INFO] Bot 已退出"
read -n 1 -s -r -p "按任意键继续..."
echo ""
