"""
MikuBot 依赖检测与自动安装模块

启动时检测所有依赖是否安装，缺失则自动用 .venv 的 pip 补装。
所有依赖都安装在项目目录的 .venv 中，不依赖系统 Python。
"""

import sys
import os
import subprocess
import zipfile
import re
from pathlib import Path
from utils.deps_config import (
    BASE_DIR,
    VENV_PYTHON,
    VENV_PIP,
    REQUIREMENTS_FILE,
    PLAYWRIGHT_BROWSERS,
    PLAYWRIGHT_CACHE_DIR,
    get_python_deps,
    ensure_requirements_file,
)

# ─── 本地浏览器 zip 包查找路径 ───
LOCAL_BROWSER_ZIP_SEARCH = [
    BASE_DIR / "chrome-win64.zip",
    BASE_DIR / "data" / "browsers" / "chrome-win64.zip",
]


def _get_playwright_chromium_dir() -> Path:
    # 用 playwright CLI 的 --dry-run 精确获取 chromium 的真实安装位置。
    # 目标目录最终形如:  C:/Users/xxx/AppData/Local/ms-playwright/chromium-1223/chrome-win64
    try:
        result = subprocess.run(
            [str(VENV_PYTHON), "-m", "playwright", "install", "--dry-run", "chromium"],
            capture_output=True, text=True, check=False,
        )
        output = (result.stdout or "") + (result.stderr or "")
        # 优先解析 Install location:  <完整路径>
        m = re.search(r"Install location:\s+(\S+)", output)
        if m:
            base = Path(m.group(1))
            return base / "chrome-win64"
        # 回退：解析 revision 号 chromium v1223
        m = re.search(r"chromium[-\s]v?(\d+)", output)
        if m:
            return PLAYWRIGHT_CACHE_DIR / f"chromium-{m.group(1)}" / "chrome-win64"
    except Exception:
        pass
    # 最终回退：扫描现有目录 / 使用默认兜底
    existing = list(PLAYWRIGHT_CACHE_DIR.glob("chromium-*"))
    if existing:
        return existing[0] / "chrome-win64"
    return PLAYWRIGHT_CACHE_DIR / "chromium-0000" / "chrome-win64"


def _find_local_zip() -> Path | None:
    """在约定位置查找本地 chrome-win64.zip"""
    for p in LOCAL_BROWSER_ZIP_SEARCH:
        if p.exists() and p.stat().st_size > 10 * 1024 * 1024:  # >10MB 视为有效
            return p
    return None


def _extract_browser_zip(zip_path: Path, target_dir: Path) -> bool:
    """把 chrome-win64.zip 解压到目标目录（保留 zip 内的相对路径）"""
    try:
        target_dir.parent.mkdir(parents=True, exist_ok=True)
        print(f"[INFO] 解压本地浏览器包: {zip_path.name}")
        with zipfile.ZipFile(zip_path, "r") as zf:
            # 解压到 chromium-xxxx/  层级（zip 内通常已有 chrome-win64/ 前缀）
            zf.extractall(target_dir.parent)
        # 标记安装完成（Playwright 自身机制）
        marker = target_dir.parent / "INSTALLATION_COMPLETE"
        marker.write_text("", encoding="utf-8")
        # 校验关键可执行文件是否就位
        chrome_exe = target_dir / "chrome.exe"
        return chrome_exe.exists()
    except Exception as e:
        print(f"[WARN] 解压失败: {e}")
        return False


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
    # 检测系统是否已安装 Microsoft Edge（channel="msedge"）
    edge_paths = [
        Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
        Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
    ]
    return any(p.exists() for p in edge_paths)


def _check_playwright_browser() -> bool:
    # 检测 Playwright Chromium 浏览器是否可用（优先系统 Edge）
    # 系统 Edge 已安装 → 直接认为可用，跳过约 180MB 下载
    if _has_system_edge():
        return True
    try:
        if not PLAYWRIGHT_CACHE_DIR.exists():
            return False
        target = _get_playwright_chromium_dir()
        return (target / "chrome.exe").exists()
    except Exception:
        return False


def _install_browser() -> bool:
    """
    安装 Playwright Chromium 浏览器：
      1. 优先检测本地 chrome-win64.zip 并自动解压
      2. 没有本地包时，设置国内镜像再走 playwright install
    """
    target = _get_playwright_chromium_dir()

    # 方案 1：本地 zip 包优先
    zip_path = _find_local_zip()
    if zip_path:
        print(f"[INFO] 找到本地浏览器包: {zip_path}")
        if _extract_browser_zip(zip_path, target):
            print(f"[OK] 浏览器已解压到: {target}")
            return True
        print("[WARN] 本地解压失败，尝试云端安装...")

    # 方案 2：云端安装（使用国内镜像）
    os.environ.setdefault("PLAYWRIGHT_DOWNLOAD_HOST", "https://npmmirror.com/mirrors/playwright/")
    print("[INFO] 正在下载 Chromium 浏览器（约180MB，首次需要）...")
    result = subprocess.run(
        [str(VENV_PYTHON), "-m", "playwright", "install"] + PLAYWRIGHT_BROWSERS,
        check=False,
    )
    return result.returncode == 0


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

    # 5. 检测 Playwright 浏览器
    if _check_package("playwright") and not _check_playwright_browser():
        print("[INFO] 浏览器未安装，正在下载...")
        if not _install_browser():
            print("[WARN] 浏览器下载失败，截图功能将不可用")
            print(f"       稍后手动执行: {VENV_PYTHON} -m playwright install {' '.join(PLAYWRIGHT_BROWSERS)}")
        else:
            print("[OK] 浏览器下载完成")

    print("[OK] 依赖检测通过\n")


if __name__ == "__main__":
    check_all()