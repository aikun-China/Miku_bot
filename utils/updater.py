"""
MikuBot 自动更新模块

负责：
  1. 从 GitHub 下载最新代码
  2. 智能覆盖：保留用户配置，只更新核心代码
  3. 更新后重启 Bot

更新策略：
  - 保留：.env、config/、data/、logs/、.venv/、.git/
  - 更新：bot.py、utils/、plugins/、start.bat、start.sh、README.md、requirements.txt
"""

import os
import sys
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import List, Optional

import httpx

from utils.version_manager import (
    BASE_DIR,
    GITHUB_REPO,
    GITHUB_RAW,
    update_local_version,
    get_local_version,
)

# ─── 需要保留的目录/文件（用户数据） ───
PROTECTED_ITEMS = {
    ".env",
    ".env.dev",
    ".env.prod",
    "config",
    "data",
    "logs",
    ".venv",
    ".git",
    "version.json",
    "plugins_index",  # 插件索引由用户管理，不覆盖
}

# ─── 需要更新的核心文件 ───
CORE_FILES = [
    "bot.py",
    "requirements.txt",
    "pyproject.toml",
    "start.bat",
    "start.sh",
    "start.ps1",
    "README.md",
]

# ─── 需要更新的目录 ───
CORE_DIRS = [
    "utils",
    "plugins",
]


def _should_update_item(name: str) -> bool:
    """判断某个文件/目录是否需要更新"""
    # 如果是受保护项，不更新
    if name in PROTECTED_ITEMS:
        return False
    # 如果是核心文件或目录，更新
    if name in CORE_FILES or name in CORE_DIRS:
        return True
    # 其他情况默认不更新（避免误删用户文件）
    return False


async def download_file(url: str, dest: Path) -> bool:
    """从 URL 下载文件到本地"""
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(resp.content)
                return True
    except Exception:
        pass
    return False


async def update_core_files() -> List[str]:
    """
    逐个下载核心文件更新
    返回：成功更新的文件列表
    """
    updated = []
    
    for filename in CORE_FILES:
        url = f"{GITHUB_RAW}/{filename}"
        dest = BASE_DIR / filename
        
        if await download_file(url, dest):
            updated.append(filename)
    
    return updated


async def update_core_dirs() -> List[str]:
    """
    下载并更新核心目录（utils/、plugins/）
    策略：下载 zip 包解压后合并，或逐个文件下载
    返回：成功更新的目录列表
    """
    updated = []
    
    # 使用 GitHub archive zip 下载完整目录
    zip_url = f"https://github.com/{GITHUB_REPO}/archive/refs/heads/main.zip"
    
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            zip_path = tmp_path / "update.zip"
            
            # 下载 zip
            async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
                resp = await client.get(zip_url)
                if resp.status_code != 200:
                    return updated
                zip_path.write_bytes(resp.content)
            
            # 解压
            import zipfile
            extract_dir = tmp_path / "extracted"
            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(extract_dir)
            
            # 找到解压后的主目录
            subdirs = [d for d in extract_dir.iterdir() if d.is_dir()]
            if not subdirs:
                return updated
            source_dir = subdirs[0]
            
            # 更新核心目录
            for dirname in CORE_DIRS:
                src = source_dir / dirname
                dst = BASE_DIR / dirname
                
                if src.exists():
                    # 删除旧目录，复制新目录
                    if dst.exists():
                        shutil.rmtree(dst)
                    shutil.copytree(src, dst)
                    updated.append(dirname)
    
    except Exception as e:
        print(f"[WARN] 目录更新失败: {e}")
    
    return updated


async def do_update() -> Dict[str, Any]:
    """
    执行完整更新流程
    
    返回：
    {
        "success": bool,
        "updated_files": list,
        "updated_dirs": list,
        "error": str,
    }
    """
    import json
    
    result = {
        "success": False,
        "updated_files": [],
        "updated_dirs": [],
        "error": "",
    }
    
    try:
        # 1. 更新核心文件
        result["updated_files"] = await update_core_files()
        
        # 2. 更新核心目录
        result["updated_dirs"] = await update_core_dirs()
        
        # 3. 更新 version.json
        if result["updated_files"] or result["updated_dirs"]:
            # 获取远程 version.json 的 hash
            async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
                resp = await client.get(f"{GITHUB_RAW}/version.json")
                if resp.status_code == 200:
                    remote_data = resp.json()
                    update_local_version(
                        commit_hash=remote_data.get("commit_hash", ""),
                        version=remote_data.get("version", ""),
                    )
                else:
                    # 没有远程 version.json，用当前时间作为标记
                    update_local_version()
            
            result["success"] = True
        else:
            result["error"] = "没有文件需要更新"
    
    except Exception as e:
        result["error"] = f"更新失败: {str(e)}"
    
    return result


def restart_bot() -> None:
    """更新完成后重启 Bot"""
    python = sys.executable
    args = sys.argv
    os.execv(python, [python] + args)
