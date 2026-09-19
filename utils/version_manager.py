"""
MikuBot 版本管理器

负责：
  1. 读取/保存本地版本信息（version.json）
  2. 从 GitHub 获取最新版本信息
  3. 比对版本，判断是否需要更新

版本比对策略：
  - 优先通过 GitHub commit hash 比对
  - 回退到文件内容 hash 比对（下载 bot.py 计算 hash）
"""

import json
import hashlib
import time
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, Dict, Any

import httpx

# ─── 路径 ───
BASE_DIR = Path(__file__).resolve().parent.parent
VERSION_FILE = BASE_DIR / "version.json"
GITHUB_REPO = "aikun-China/Miku_bot"
GITHUB_RAW = f"https://raw.githubusercontent.com/{GITHUB_REPO}/main"


def _load_version_file() -> Dict[str, Any]:
    """读取 version.json"""
    if VERSION_FILE.exists():
        try:
            return json.loads(VERSION_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {
        "version": "0.1.0",
        "commit_hash": "",
        "update_time": "",
        "github_repo": GITHUB_REPO,
        "check_interval_hours": 24,
        "last_check_time": "",
    }


def _save_version_file(data: Dict[str, Any]) -> None:
    """保存 version.json"""
    VERSION_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _file_hash(filepath: Path) -> str:
    """计算文件 MD5 hash"""
    try:
        return hashlib.md5(filepath.read_bytes()).hexdigest()[:16]
    except Exception:
        return ""


def _should_check() -> bool:
    """判断是否需要检查更新（根据 check_interval_hours）"""
    data = _load_version_file()
    last_check = data.get("last_check_time", "")
    interval = data.get("check_interval_hours", 24)
    
    if not last_check:
        return True
    
    try:
        last = datetime.fromisoformat(last_check)
        now = datetime.now(timezone.utc)
        hours_passed = (now - last).total_seconds() / 3600
        return hours_passed >= interval
    except Exception:
        return True


# ═══════════════════════════════════════════
# 公开 API
# ═══════════════════════════════════════════

def get_local_version() -> Dict[str, Any]:
    """获取本地版本信息"""
    return _load_version_file()


async def check_update() -> Dict[str, Any]:
    """
    检查 GitHub 是否有新版本
    
    返回：
    {
        "has_update": bool,       # 是否有更新
        "local_version": str,     # 本地版本号
        "remote_version": str,    # 远程版本号（commit hash 或文件 hash）
        "update_time": str,       # 远程更新时间
        "changelog": str,         # 更新说明（README 前 500 字）
        "error": str,            # 错误信息（如果有）
    }
    """
    result = {
        "has_update": False,
        "local_version": "",
        "remote_version": "",
        "update_time": "",
        "changelog": "",
        "error": "",
    }
    
    data = _load_version_file()
    result["local_version"] = data.get("version", "0.1.0")
    
    # 记录检查时间
    data["last_check_time"] = datetime.now(timezone.utc).isoformat()
    _save_version_file(data)
    
    try:
        # 方案 1：尝试获取远程 version.json
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.get(f"{GITHUB_RAW}/version.json")
            if resp.status_code == 200:
                remote_data = resp.json()
                remote_version = remote_data.get("version", "")
                remote_hash = remote_data.get("commit_hash", "")
                
                local_hash = data.get("commit_hash", "")
                
                if remote_hash and local_hash and remote_hash != local_hash:
                    result["has_update"] = True
                    result["remote_version"] = f"{remote_version} ({remote_hash[:8]})"
                elif remote_version != data.get("version", ""):
                    result["has_update"] = True
                    result["remote_version"] = remote_version
                
                result["update_time"] = remote_data.get("update_time", "")
                
        # 方案 2：如果 version.json 拿不到，比对 bot.py hash
        if not result["has_update"] and not result["remote_version"]:
            local_bot_hash = _file_hash(BASE_DIR / "bot.py")
            
            async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
                resp = await client.get(f"{GITHUB_RAW}/bot.py")
                if resp.status_code == 200:
                    remote_bot_hash = hashlib.md5(resp.text.encode("utf-8")).hexdigest()[:16]
                    
                    if local_bot_hash and remote_bot_hash != local_bot_hash:
                        result["has_update"] = True
                        result["remote_version"] = f"bot.py:{remote_bot_hash[:8]}"
                else:
                    result["error"] = f"无法获取远程版本 (HTTP {resp.status_code})"
                    return result
        
        # 获取更新说明（README 前 500 字）
        if result["has_update"]:
            try:
                async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
                    resp = await client.get(f"{GITHUB_RAW}/README.md")
                    if resp.status_code == 200:
                        text = resp.text[:500]
                        result["changelog"] = text.strip()
            except Exception:
                pass
    
    except httpx.TimeoutException:
        result["error"] = "连接 GitHub 超时，请检查网络"
    except Exception as e:
        result["error"] = f"检查更新失败: {str(e)}"
    
    return result


async def get_latest_info() -> Dict[str, Any]:
    """获取最新版本信息（不比对，只获取）"""
    result = {"version": "", "update_time": "", "error": ""}
    
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            # 先尝试 version.json
            resp = await client.get(f"{GITHUB_RAW}/version.json")
            if resp.status_code == 200:
                data = resp.json()
                result["version"] = data.get("version", "")
                result["update_time"] = data.get("update_time", "")
                return result
            
            # 回退：获取 README 判断活跃性
            resp = await client.get(f"{GITHUB_RAW}/README.md")
            if resp.status_code == 200:
                result["version"] = "最新版"
    except Exception as e:
        result["error"] = str(e)
    
    return result


def update_local_version(commit_hash: str = "", version: str = "") -> None:
    """更新本地版本信息（更新成功后调用）"""
    data = _load_version_file()
    if version:
        data["version"] = version
    if commit_hash:
        data["commit_hash"] = commit_hash
    data["update_time"] = datetime.now(timezone.utc).isoformat()
    _save_version_file(data)


def format_version_info(data: Dict[str, Any]) -> str:
    """格式化版本信息为字符串"""
    lines = [
        "MikuBot 版本信息",
        "━━━━━━━━━━━━",
        f"当前版本: {data.get('version', '未知')}",
    ]
    
    commit = data.get('commit_hash', '')
    if commit:
        lines.append(f"Commit: {commit[:8]}")
    
    update_time = data.get('update_time', '')
    if update_time:
        lines.append(f"更新时间: {update_time}")
    
    last_check = data.get('last_check_time', '')
    if last_check:
        lines.append(f"上次检查: {last_check}")
    
    lines.append("━━━━━━━━━━━━")
    lines.append(f"仓库: https://github.com/{data.get('github_repo', GITHUB_REPO)}")
    
    return "\n".join(lines)
