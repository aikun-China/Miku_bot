"""
MikuBot 依赖检测与自动安装模块

启动时检测所有依赖是否安装，缺失则自动用 .venv 的 pip 补装。
所有依赖都安装在项目目录的 .venv 中，不依赖系统 Python。

浏览器策略：使用系统 Edge（Playwright channel="msedge"），不下载 Chromium。
"""

import sys
import os
import subprocess
from pathlib import Path
from utils.deps_config import (
    BASE_DIR,
    VENV_PYTHON,
    VENV_PIP,
    REQUIREMENTS_FILE,
    get_python_deps,
    ensure_requirements_file,
)


def _check_package(import_name: str) -> bool:
    """检测某个 Python 包是否已安装"""
    try:
        __import__(import_name)
        return True
    except ImportError:
        return False


def _install_deps(missing: list) -> bool:
    """用 .venv 的 pip 安装缺失的依赖"""
    if not VENV_PIP.exists():
        print(f"[ERROR] 找不到 pip: {VENV_PIP}")
        return False

    # 先升级 pip
    subprocess.run(
        [str(VENV_PYTHON), "-m", "pip", "install", "--upgrade", "pip"],
        check=False,
        capture_output=True,
    )

    # 安装缺失的依赖
    cmd = [str(VENV_PYTHON), "-m", "pip", "install"] + missing
    print(f"[INFO] 执行: pip install {' '.join(missing)}")
    result = subprocess.run(cmd, check=False)
    return result.returncode == 0


def _has_system_edge() -> bool:
    """检测系统是否已安装 Microsoft Edge"""
    edge_paths = [
        Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
        Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
    ]
    return any(p.exists() for p in edge_paths)


def _check_edge_available() -> bool:
    """检测 Edge 是否可被 Playwright 调用（channel="msedge"）"""
    if not _has_system_edge():
        return False
    try:
        # 测试启动 Edge
        import subprocess
        result = subprocess.run(
            [str(VENV_PYTHON), "-c",
             "import asyncio; from playwright.async_api import async_playwright; "
             "async def t(): async with async_playwright() as p: "
             "b = await p.chromium.launch(headless=True, channel='msedge'); "
             "await b.close(); asyncio.run(t())"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        return result.returncode == 0
    except Exception:
        return False


def check_all():
    """
    完整的依赖检测与自动安装流程。
    在 bot.py 启动前调用。
    """
    print("[INFO] 正在检测依赖...")

    # 0. 确保 requirements.txt 存在
    if not ensure_requirements_file():
        sys.exit(1)

    # 1. 检查 .venv 是否存在
    if not VENV_PYTHON.exists():
        print("[ERROR] 虚拟环境 .venv 不存在，请先创建:")
        print(f"       python -m venv .venv")
        sys.exit(1)

    # 2. 从 requirements.txt 获取依赖列表
    PYTHON_DEPS = get_python_deps()

    # 3. 检测 Python 依赖
    missing = []
    for pip_name, import_name in PYTHON_DEPS.items():
        if _check_package(import_name):
            print(f"  [OK] {pip_name}")
        else:
            print(f"  [MISS] {pip_name}")
            missing.append(pip_name)

    # 4. 自动安装缺失的依赖
    if missing:
        print(f"[INFO] 检测到 {len(missing)} 个依赖缺失，正在自动安装...")
        if not _install_deps(missing):
            print("[ERROR] 依赖安装失败，请手动执行:")
            print(f"       {VENV_PIP} install {' '.join(missing)}")
            sys.exit(1)
        print("[OK] 依赖安装完成")

    # 5. 检测 Edge 浏览器
    print("")
    print("[INFO] 检测浏览器...")
    if _has_system_edge():
        print("  [OK] 系统 Edge 已安装")
    else:
        print("  [WARN] 未检测到系统 Edge，截图功能可能不可用")
        print("       请安装 Microsoft Edge: https://www.microsoft.com/edge")

    print("[OK] 依赖检测通过\n")


if __name__ == "__main__":
    check_all()
