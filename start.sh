#!/bin/bash
# MikuBot 启动脚本 (Linux/Mac/Windows Git Bash) — uv 版
# 依赖管理与运行环境统一由 uv 负责

cd "$(dirname "$0")" || exit 1

echo "========================================="
echo " MikuBot 启动 (uv)"
echo "========================================="

# 1. 检查 uv
if ! command -v uv &> /dev/null; then
    echo "[ERROR] 未找到 uv，请先安装: https://docs.astral.sh/uv/"
    echo "       Windows: powershell -ExecutionPolicy ByPass -c \"irm https://astral.sh/uv/install.ps1 | iex\""
    echo "       Linux/Mac: curl -LsSf https://astral.sh/uv/install.sh | sh"
    exit 1
fi
echo "[OK] uv 已就绪"

# 2. 同步依赖（uv sync 会自动创建/复用 .venv 并安装依赖）
echo ""
echo "[INFO] 正在同步依赖 (uv sync)..."
uv sync
if [ $? -ne 0 ]; then
    echo "[ERROR] uv sync 失败"
    exit 1
fi
echo "[OK] 依赖同步完成"

# 3. 检查 Edge 浏览器（截图引擎）
echo ""
if command -v microsoft-edge &> /dev/null || \
   command -v msedge &> /dev/null || \
   [[ -f "/usr/bin/microsoft-edge" ]] || \
   [[ -f "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge" ]]; then
    echo "[OK] Edge 浏览器已安装"
else
    echo "[WARN] 未检测到 Edge，截图功能需要 Edge"
    echo "[INFO] 截图引擎依赖: uv run playwright install msedge"
fi

# 4. 启动 Bot
echo ""
echo "========================================="
echo " 启动 MikuBot..."
echo "========================================="
echo ""

uv run python bot.py

echo ""
echo "[INFO] Bot 已退出"
read -n 1 -s -r -p "按任意键继续..."
echo ""
