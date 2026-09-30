"""
Miku GitHub 自动备份插件
========================
- 每日 0:00 自动将 Bot 关键数据备份到 GitHub 私有仓库
- 每次备份新建以前一天日期命名的文件夹（年-月-日），同一天多次备份加 -x 后缀
- 每个备份默认保留 30 天，过期自动清理
- 支持手动指令：git备份 / git恢复 / git恢复 日期 / git备份列表

依赖：httpx（项目已安装）
GitHub Token 获取：https://github.com/settings/tokens （需 repo 权限）
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import ssl
import tempfile
import time
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import httpx
from nonebot import on_command, get_driver
from nonebot.exception import FinishedException
from nonebot.adapters.onebot.v11 import Bot, MessageEvent
from nonebot.params import CommandArg
from nonebot.log import logger
from nonebot.plugin import PluginMetadata
from nonebot.adapters.onebot.v11.message import Message

from utils.config_manager import config_manager

__plugin_meta__ = PluginMetadata(
    name="Miku GitHub备份",
    description="每日自动备份Bot数据到GitHub私有仓库，支持手动备份/恢复",
    usage="git备份 / git恢复 / git恢复 年.月.日(-x) / git备份列表",
    type="application",
    supported_adapters={"~onebot.v11"},
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# ============================================================
# 插件配置
# ============================================================
_TEMPLATE = (
    "\n"
    "miku_github:\n"
    "  # 是否启用 GitHub 自动备份\n"
    "  enabled: true\n"
    "  # GitHub Personal Access Token（需 repo 权限）\n"
    "  # 获取地址：https://github.com/settings/tokens\n"
    "  token: \"\"\n"
    "  # GitHub 用户名（仓库所有者）\n"
    "  repo_owner: \"\"\n"
    "  # 备份仓库名称（不存在时自动创建为私有仓库）\n"
    "  repo_name: \"Mikubot-Backup\"\n"
    "  # 备份保留天数（超过此天数的备份自动清理）\n"
    "  retention_days: 30\n"
    "  # 是否验证SSL证书（开启网络加速且代理证书不受信任时出现SSL错误，可设为 false）\n"
    "  verify_ssl: true\n"
    "  # 每日自动备份时间（HH:MM 格式）\n"
    "  backup_time: \"00:00\"\n"
    "  # 是否在启动时检查仓库是否存在（不存在则创建）\n"
    "  auto_create_repo: true\n"
)

_cfg = config_manager.register_plugin(
    "miku_github",
    defaults={
        "enabled": True,
        "token": "",
        "repo_owner": "",
        "repo_name": "Mikubot-Backup",
        "retention_days": 30,
        "verify_ssl": True,
        "backup_time": "00:00",
        "auto_create_repo": True,
    },
    template_str=_TEMPLATE,
    description="GitHub自动备份配置",
)


def _is_enabled() -> bool:
    raw = config_manager.get("miku_github", "enabled", True)
    return str(raw).strip().lower() not in ("false", "0", "no", "")


def _conf(key: str, default=None):
    return config_manager.get("miku_github", key, default)


# ============================================================
# 备份清单（待办中指定的文件/目录）
# ============================================================
# 每个条目：相对项目根目录的文件路径（散文件，并发上传）
BACKUP_ITEMS: List[str] = [
    ".env",
    "data/blacklist.json",
    "data/command_stats.json",
    "data/parser_groups.json",
    "data/shop.json",
    "data/webui_devices.json",
    "data/global_daily_stats.json",
    "data/message_stats.json",
    "data/plugin_stats.json",
    "config/group_welcome.json",
    "config/bot.yaml",
]

# 打包上传的目录（文件数多，逐个上传极慢，各自打包成一个 zip）
BACKUP_ZIP_DIRS: List[str] = [
    "data/users",
    "data/chat_history",
    "data/ai_chat_history",
    "config/welcome_imgs",
]

# logs 目录特殊处理：仅备份前一天的日志
def _get_backup_log_files() -> List[Path]:
    """获取前一天的日志文件列表"""
    log_dir = PROJECT_ROOT / "logs"
    if not log_dir.exists():
        return []
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    files = []
    for f in log_dir.iterdir():
        if f.is_file() and f.suffix == ".log" and yesterday in f.name:
            files.append(f)
    return files


# ============================================================
# GitHub API 操作封装
# ============================================================
GITHUB_API = "https://api.github.com"


def _get_headers() -> dict:
    token = str(_conf("token", "") or "").strip()
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "MikuBot-Backup",
    }
    if token:
        headers["Authorization"] = f"token {token}"
    return headers


def _sanitize_repo_name(name: str) -> str:
    """GitHub 仓库名只允许字母数字、-、_、.，中文等非法字符替换为 -"""
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "-", name)
    cleaned = re.sub(r"-{2,}", "-", cleaned).strip("-.")
    return cleaned or "mikubot-backup"


def _get_repo_name() -> str:
    return _sanitize_repo_name(str(_conf("repo_name", "Mikubot-Backup") or "Mikubot-Backup").strip())


def _get_repo_full_name() -> str:
    owner = str(_conf("repo_owner", "") or "").strip()
    return f"{owner}/{_get_repo_name()}"


def _check_token_configured() -> Tuple[bool, str]:
    """检查 token 和 repo_owner 是否已配置"""
    token = str(_conf("token", "") or "").strip()
    owner = str(_conf("repo_owner", "") or "").strip()
    if not token:
        return False, "未配置 GitHub Token，请在 bot.yaml 的 miku_github.token 中填写"
    if not owner:
        return False, "未配置 GitHub 用户名，请在 bot.yaml 的 miku_github.repo_owner 中填写"
    return True, ""


_SSL_DEGRADED = False


def _ssl_verify_param():
    """构建 httpx verify 参数：合并 certifi 与系统证书库（兼容加速代理根证书）；已降级或配置关闭时返回 False"""
    if _SSL_DEGRADED or not _conf("verify_ssl", True):
        return False
    try:
        ctx = ssl.create_default_context()
        try:
            import certifi
            ctx.load_verify_locations(certifi.where())
        except Exception:
            pass
        try:
            ctx.load_default_certs()
        except Exception:
            pass
        return ctx
    except Exception:
        return True


def _is_ssl_error(e: Exception) -> bool:
    msg = str(e).lower()
    return "certificate" in msg or "ssl" in msg


async def _do_http(method: str, url: str, headers: dict, verify, **kwargs) -> Tuple[int, dict]:
    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True, verify=verify) as client:
        resp = await client.request(method, url, headers=headers, **kwargs)
        try:
            data = resp.json()
        except Exception:
            data = {"message": resp.text[:500]}
        return resp.status_code, data


async def _api_request(method: str, url: str, **kwargs) -> Tuple[int, dict]:
    """统一的 GitHub API 请求，返回 (status_code, json_data)；SSL证书验证失败时自动降级为不验证并重试"""
    global _SSL_DEGRADED
    headers = _get_headers()
    if "headers" in kwargs:
        headers.update(kwargs.pop("headers"))
    verify = _ssl_verify_param()
    if verify is not False:
        try:
            return await _do_http(method, url, headers, verify, **kwargs)
        except httpx.ConnectError as e:
            if not _is_ssl_error(e):
                logger.error(f"[miku_github] API请求异常 {method} {url}: {e}")
                return 0, {"message": str(e)}
            _SSL_DEGRADED = True
            logger.warning(
                "[miku_github] SSL证书验证失败（加速代理证书不在信任列表），已自动降级为不验证证书并重试，"
                "后续请求将直接使用不验证模式"
            )
    try:
        return await _do_http(method, url, headers, False, **kwargs)
    except Exception as e:
        logger.error(f"[miku_github] API请求异常 {method} {url}: {e}")
        return 0, {"message": str(e)}


async def check_repo_exists() -> Tuple[bool, str]:
    """检查备份仓库是否存在"""
    ok, msg = _check_token_configured()
    if not ok:
        return False, msg
    full = _get_repo_full_name()
    status, data = await _api_request("GET", f"{GITHUB_API}/repos/{full}")
    if status == 200:
        return True, f"仓库 {full} 已存在"
    if status == 404:
        return False, f"仓库 {full} 不存在"
    return False, f"检查仓库失败: {data.get('message', '未知错误')}"


async def create_repo() -> Tuple[bool, str]:
    """创建私有备份仓库"""
    ok, msg = _check_token_configured()
    if not ok:
        return False, msg
    repo_name = _get_repo_name()
    payload = {
        "name": repo_name,
        "description": "MikuBot 自动备份仓库",
        "private": True,
        "auto_init": True,
    }
    status, data = await _api_request("POST", f"{GITHUB_API}/user/repos", json=payload)
    if status in (200, 201):
        return True, f"私有仓库 {repo_name} 创建成功"
    # 422 可能是仓库已存在
    if status == 422:
        return True, f"仓库 {repo_name} 已存在"
    return False, f"创建仓库失败: {data.get('message', '未知错误')}"


async def ensure_repo() -> Tuple[bool, str]:
    """确保仓库存在，不存在则创建"""
    exists, msg = await check_repo_exists()
    if exists:
        return True, msg
    auto_create = str(_conf("auto_create_repo", True)).strip().lower() not in ("false", "0", "no", "")
    if not auto_create:
        return False, f"{msg}（且未开启自动创建）"
    return await create_repo()


async def _get_file_sha(remote_path: str) -> Optional[str]:
    """获取远程文件的 SHA（用于更新/删除）"""
    full = _get_repo_full_name()
    # 路径需要 URL 编码
    encoded_path = remote_path
    status, data = await _api_request("GET", f"{GITHUB_API}/repos/{full}/contents/{encoded_path}")
    if status == 200 and isinstance(data, dict) and "sha" in data:
        return data["sha"]
    return None


async def upload_file(local_path: Path, remote_path: str, message: str = "backup") -> bool:
    """上传单个文件到 GitHub 仓库"""
    if not local_path.exists() or not local_path.is_file():
        return False
    try:
        content = local_path.read_bytes()
    except Exception as e:
        logger.warning(f"[miku_github] 读取文件失败 {local_path}: {e}")
        return False

    # GitHub API 单次上传限制 100MB
    if len(content) > 100 * 1024 * 1024:
        logger.warning(f"[miku_github] 文件过大跳过（>100MB）: {local_path.name}")
        return False

    b64 = base64.b64encode(content).decode("utf-8")
    sha = await _get_file_sha(remote_path)

    payload: Dict = {
        "message": message,
        "content": b64,
    }
    if sha:
        payload["sha"] = sha

    full = _get_repo_full_name()
    status, data = await _api_request(
        "PUT",
        f"{GITHUB_API}/repos/{full}/contents/{remote_path}",
        json=payload,
    )
    if status in (200, 201):
        return True
    logger.warning(f"[miku_github] 上传失败 {remote_path}: {data.get('message', '未知')}")
    return False


async def download_file(remote_path: str, local_path: Path) -> bool:
    """从 GitHub 下载单个文件到本地"""
    full = _get_repo_full_name()
    status, data = await _api_request("GET", f"{GITHUB_API}/repos/{full}/contents/{remote_path}")
    if status != 200 or not isinstance(data, dict):
        return False
    content_b64 = data.get("content")
    encoding = data.get("encoding", "base64")
    if not content_b64:
        return False
    try:
        if encoding == "base64":
            content = base64.b64decode(content_b64)
        else:
            content = content_b64.encode("utf-8")
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_bytes(content)
        return True
    except Exception as e:
        logger.warning(f"[miku_github] 下载失败 {remote_path}: {e}")
        return False


async def list_remote_dir(remote_path: str = "") -> List[dict]:
    """列出远程目录下的条目"""
    full = _get_repo_full_name()
    url = f"{GITHUB_API}/repos/{full}/contents/{remote_path}" if remote_path else f"{GITHUB_API}/repos/{full}/contents/"
    status, data = await _api_request("GET", url)
    if status == 200 and isinstance(data, list):
        return data
    return []


async def delete_remote_file(remote_path: str) -> bool:
    """删除远程单个文件"""
    sha = await _get_file_sha(remote_path)
    if not sha:
        return True  # 不存在视为删除成功
    full = _get_repo_full_name()
    payload = {"message": "cleanup expired backup", "sha": sha}
    status, data = await _api_request(
        "DELETE",
        f"{GITHUB_API}/repos/{full}/contents/{remote_path}",
        json=payload,
    )
    if status in (200, 204):
        return True
    logger.warning(f"[miku_github] 删除失败 {remote_path}: {data.get('message', '未知')}")
    return False


async def delete_remote_dir(remote_path: str) -> bool:
    """递归删除远程目录"""
    entries = await list_remote_dir(remote_path)
    ok = True
    for entry in entries:
        entry_path = entry.get("path", "")
        entry_type = entry.get("type", "")
        if entry_type == "dir":
            if not await delete_remote_dir(entry_path):
                ok = False
        else:
            if not await delete_remote_file(entry_path):
                ok = False
    return ok


# ============================================================
# 备份文件夹命名与查询
# ============================================================
_DATE_DIR_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})(?:-(\d+))?$")


def parse_backup_dir(name: str) -> Optional[Tuple[datetime, int]]:
    """解析备份文件夹名，返回 (日期, 序号x)，无法解析返回 None"""
    m = _DATE_DIR_RE.match(name.strip())
    if not m:
        return None
    date_str = m.group(1)
    x = int(m.group(2)) if m.group(2) else 0
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        return dt, x
    except ValueError:
        return None


async def list_backup_dirs() -> List[str]:
    """列出仓库中所有备份文件夹名（按日期排序，最新在前）"""
    entries = await list_remote_dir("")
    dirs = []
    for e in entries:
        if e.get("type") == "dir":
            name = e.get("name", "")
            if parse_backup_dir(name):
                dirs.append(name)
    # 按日期降序
    def _sort_key(n):
        p = parse_backup_dir(n)
        return (p[0], p[1]) if p else (datetime.min, 0)
    dirs.sort(key=_sort_key, reverse=True)
    return dirs


async def get_newest_backup_dir() -> Optional[str]:
    """获取最新的备份文件夹名"""
    dirs = await list_backup_dirs()
    return dirs[0] if dirs else None


async def _find_available_dir_name(target_date: datetime) -> str:
    """为指定日期找一个可用的文件夹名（已存在则递增 -x 后缀）"""
    date_str = target_date.strftime("%Y-%m-%d")
    existing = await list_backup_dirs()
    used_x = set()
    for name in existing:
        p = parse_backup_dir(name)
        if p and p[0] == target_date:
            used_x.add(p[1])
    # 找最小的可用 x
    x = 0
    while x in used_x:
        x += 1
    return f"{date_str}-{x}" if x > 0 else date_str


# ============================================================
# 备份核心逻辑
# ============================================================
def _collect_backup_files() -> List[Tuple[Path, str]]:
    """
    收集需要备份的本地散文件列表。
    返回 [(本地绝对路径, 仓库内相对路径), ...]（不含 zip 打包目录）
    仓库内相对路径以备份文件夹名为前缀（由调用方拼接）。
    """
    results: List[Tuple[Path, str]] = []

    # 前一天的日志
    for log_file in _get_backup_log_files():
        results.append((log_file, f"logs/{log_file.name}"))

    for rel_path in BACKUP_ITEMS:
        local = PROJECT_ROOT / rel_path
        if local.is_file():
            results.append((local, rel_path))
        else:
            logger.debug(f"[miku_github] 备份项不存在，跳过: {rel_path}")

    return results


def _zip_directory(local_dir: Path, zip_path: Path) -> Tuple[int, int]:
    """
    打包目录为 zip，arcname 使用相对项目根的 posix 路径。
    返回 (文件数, zip 字节数)。
    """
    count = 0
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _, files in os.walk(local_dir):
            for fname in files:
                fp = Path(root) / fname
                zf.write(fp, fp.relative_to(PROJECT_ROOT).as_posix())
                count += 1
    return count, zip_path.stat().st_size


def _extract_zip(zip_path: Path, dest: Path) -> bool:
    """安全解压 zip 到目标目录（防路径穿越）"""
    try:
        dest_resolved = str(dest.resolve())
        with zipfile.ZipFile(zip_path) as zf:
            for member in zf.namelist():
                target = (dest / member).resolve()
                if not str(target).startswith(dest_resolved):
                    logger.warning(f"[miku_github] zip成员路径异常，跳过: {member}")
                    continue
            zf.extractall(dest)
        return True
    except Exception as e:
        logger.warning(f"[miku_github] zip解压失败 {zip_path.name}: {e}")
        return False


def _zip_name_for(rel_dir: str) -> str:
    """目录对应的 zip 包名：data/chat_history -> data_chat_history.zip"""
    return rel_dir.replace("/", "_") + ".zip"


async def do_backup() -> Tuple[bool, str]:
    """
    执行一次备份。
    备份文件夹名 = 前一天日期（同一天多次备份加 -x 后缀）
    散文件并发上传，目录打包为 zip 上传（避免逐文件上传耗时数小时）。
    """
    ok, msg = _check_token_configured()
    if not ok:
        return False, msg

    # 确保仓库存在
    repo_ok, repo_msg = await ensure_repo()
    if not repo_ok:
        return False, repo_msg

    # 备份目标日期 = 前一天
    backup_date = datetime.now() - timedelta(days=1)
    dir_name = await _find_available_dir_name(backup_date)
    logger.info(f"[miku_github] 开始备份到文件夹: {dir_name}")

    files = _collect_backup_files()
    if not files:
        return False, "没有可备份的文件"

    success_count = 0
    fail_count = 0
    skipped: List[str] = []
    sem = asyncio.Semaphore(8)

    async def _up(local_path: Path, rel_path: str) -> bool:
        async with sem:
            return await upload_file(local_path, f"{dir_name}/{rel_path}", message=f"backup {dir_name}")

    # 散文件并发上传
    results = await asyncio.gather(*[_up(p, rel) for p, rel in files])
    success_count += sum(1 for r in results if r)
    fail_count += sum(1 for r in results if not r)

    # 目录打包为 zip 上传
    for rel_dir in BACKUP_ZIP_DIRS:
        local_dir = PROJECT_ROOT / rel_dir
        if not local_dir.is_dir() or not any(local_dir.iterdir()):
            logger.debug(f"[miku_github] 打包目录为空，跳过: {rel_dir}")
            continue
        zip_name = _zip_name_for(rel_dir)
        tmp_zip = Path(tempfile.gettempdir()) / f"mikubot_{zip_name}"
        try:
            n_files, zsize = _zip_directory(local_dir, tmp_zip)
            if zsize > 100 * 1024 * 1024:
                skipped.append(f"{zip_name}({n_files}个文件,zip超过100MB)")
                logger.warning(f"[miku_github] {zip_name} 超过GitHub单文件100MB限制，跳过")
                continue
            if await upload_file(tmp_zip, f"{dir_name}/{zip_name}", message=f"backup {dir_name}"):
                success_count += 1
                logger.info(
                    f"[miku_github] 目录打包上传完成: {zip_name}"
                    f"（{n_files}个文件, {zsize / 1024 / 1024:.1f}MB）"
                )
            else:
                fail_count += 1
        except Exception as e:
            logger.warning(f"[miku_github] 打包上传失败 {rel_dir}: {e}")
            fail_count += 1
        finally:
            tmp_zip.unlink(missing_ok=True)

    if success_count == 0:
        return False, f"备份失败，所有文件上传失败（共{len(files) + len(BACKUP_ZIP_DIRS)}项）"

    result_msg = f"备份完成: {dir_name}，成功 {success_count} 项，失败 {fail_count} 项"
    if skipped:
        result_msg += f"，跳过 {len(skipped)} 项（{'、'.join(skipped)}）"
    logger.info(f"[miku_github] {result_msg}")
    return True, result_msg


def asyncio_sleep(seconds: float):
    import asyncio
    return asyncio.sleep(seconds)


# ============================================================
# 恢复核心逻辑
# ============================================================
async def do_restore(backup_dir: Optional[str] = None) -> Tuple[bool, str]:
    """
    恢复指定备份。若 backup_dir 为 None 则恢复最新备份。
    """
    ok, msg = _check_token_configured()
    if not ok:
        return False, msg

    if backup_dir is None:
        backup_dir = await get_newest_backup_dir()
        if not backup_dir:
            return False, "仓库中没有任何备份"

    # 校验文件夹名格式
    if not parse_backup_dir(backup_dir):
        return False, f"无效的备份文件夹名: {backup_dir}"

    logger.info(f"[miku_github] 开始恢复备份: {backup_dir}")

    # 列出备份文件夹下所有文件（递归）
    entries = await _list_remote_dir_recursive(backup_dir)
    if not entries:
        return False, f"备份 {backup_dir} 中没有文件"

    success_count = 0
    fail_count = 0
    zip_names = {_zip_name_for(d) for d in BACKUP_ZIP_DIRS}
    sem = asyncio.Semaphore(8)

    async def _down(entry: dict) -> Tuple[bool, bool]:
        """下载单个条目，返回 (成功, 是否zip包)"""
        remote_path = entry.get("path", "")
        if not remote_path:
            return False, False
        rel = remote_path[len(backup_dir) + 1:] if remote_path.startswith(backup_dir + "/") else remote_path
        async with sem:
            if rel in zip_names:
                tmp = Path(tempfile.gettempdir()) / f"mikubot_restore_{rel}"
                try:
                    if await download_file(remote_path, tmp) and _extract_zip(tmp, PROJECT_ROOT):
                        return True, True
                    return False, True
                finally:
                    tmp.unlink(missing_ok=True)
            local_path = PROJECT_ROOT / rel
            return await download_file(remote_path, local_path), False

    results = await asyncio.gather(*[_down(e) for e in entries])
    success_count = sum(1 for ok, _ in results if ok)
    fail_count = len(results) - success_count
    zip_count = sum(1 for ok, is_zip in results if ok and is_zip)

    if success_count == 0:
        return False, f"恢复失败，所有文件下载失败（共{len(entries)}个）"

    result_msg = f"恢复完成: {backup_dir}，成功 {success_count} 项，失败 {fail_count} 项"
    if zip_count:
        result_msg += f"（含 {zip_count} 个zip包已解压）"
    logger.info(f"[miku_github] {result_msg}")
    return True, result_msg


async def _list_remote_dir_recursive(remote_path: str) -> List[dict]:
    """递归列出远程目录下所有文件"""
    results: List[dict] = []
    entries = await list_remote_dir(remote_path)
    for e in entries:
        if e.get("type") == "dir":
            results.extend(await _list_remote_dir_recursive(e.get("path", "")))
        else:
            results.append(e)
    return results


# ============================================================
# 过期备份清理
# ============================================================
async def cleanup_expired_backups() -> Tuple[bool, str]:
    """清理超过 retention_days 的备份文件夹"""
    ok, msg = _check_token_configured()
    if not ok:
        return False, msg

    retention_days = int(_conf("retention_days", 30) or 30)
    cutoff = datetime.now() - timedelta(days=retention_days)

    dirs = await list_backup_dirs()
    removed = 0
    for name in dirs:
        p = parse_backup_dir(name)
        if not p:
            continue
        if p[0] < cutoff:
            logger.info(f"[miku_github] 清理过期备份: {name}")
            if await delete_remote_dir(name):
                removed += 1
            await asyncio_sleep(0.2)

    result = f"备份清理完成，保留{retention_days}天，删除 {removed} 个过期备份"
    logger.info(f"[miku_github] {result}")
    return True, result


# ============================================================
# 日期解析（用于 git恢复 指令）
# ============================================================
def parse_restore_date(text: str) -> Optional[str]:
    """
    解析用户输入的恢复日期，匹配仓库中的备份文件夹名。
    支持格式：
      - 月.日           （自动补全年为今年）
      - 年.月.日
      - 上述格式加 -x 后缀
    返回匹配到的备份文件夹名，未匹配返回 None
    """
    text = text.strip()
    if not text:
        return None
    # 提取可选的 -x 后缀
    x = 0
    x_match = re.search(r"-(\d+)$", text)
    if x_match:
        x = int(x_match.group(1))
        text = text[:x_match.start()].strip()

    # 匹配 年.月.日 或 月.日
    m = re.match(r"^(?:(\d{4})\.(\d{1,2})\.(\d{1,2})|(\d{1,2})\.(\d{1,2}))$", text)
    if not m:
        return None
    if m.group(1):
        year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
    else:
        year = datetime.now().year
        month, day = int(m.group(4)), int(m.group(5))
    try:
        dt = datetime(year, month, day)
    except ValueError:
        return None
    date_str = dt.strftime("%Y-%m-%d")
    return f"{date_str}-{x}" if x > 0 else date_str


# ============================================================
# 指令定义
# ============================================================
git_backup_cmd = on_command("git备份", priority=5, block=True)
git_restore_cmd = on_command("git恢复", priority=5, block=True)
git_list_cmd = on_command("git备份列表", priority=5, block=True)


def _is_superuser(event: MessageEvent) -> bool:
    """判断是否超级用户"""
    try:
        su = str(config_manager.get("bot", "superusers", "")).strip()
        user_id = str(event.user_id)
        return user_id in [s.strip() for s in su.split(",") if s.strip()]
    except Exception:
        return False


@git_backup_cmd.handle()
async def _handle_git_backup(bot: Bot, event: MessageEvent):
    if not _is_enabled():
        await git_backup_cmd.finish("GitHub备份功能未启用")
    if not _is_superuser(event):
        await git_backup_cmd.finish("只有超级用户可以执行备份操作")
    try:
        await git_backup_cmd.send("正在备份到GitHub，请稍候...")
        ok, msg = await do_backup()
        # 备份后清理过期
        try:
            await cleanup_expired_backups()
        except Exception as e:
            logger.warning(f"[miku_github] 备份后清理异常: {e}")
        await git_backup_cmd.finish(msg if ok else f"备份失败: {msg}")
    except FinishedException:
        raise
    except Exception as e:
        logger.exception("[miku_github] git备份异常")
        await git_backup_cmd.finish(f"备份异常: {e}")


@git_restore_cmd.handle()
async def _handle_git_restore(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    if not _is_enabled():
        await git_restore_cmd.finish("GitHub备份功能未启用")
    if not _is_superuser(event):
        await git_restore_cmd.finish("只有超级用户可以执行恢复操作")
    text = args.extract_plain_text().strip()
    try:
        if text:
            target = parse_restore_date(text)
            if not target:
                await git_restore_cmd.finish(
                    f"日期格式无效: {text}\n支持格式：月.日 或 年.月.日，可加 -x 后缀\n例如：9.29 或 2026.9.29-1"
                )
            await git_restore_cmd.send(f"正在恢复备份 {target}，请稍候...")
            ok, msg = await do_restore(target)
        else:
            await git_restore_cmd.send("正在恢复最近一次备份，请稍候...")
            ok, msg = await do_restore(None)
        await git_restore_cmd.finish(msg if ok else f"恢复失败: {msg}")
    except FinishedException:
        raise
    except Exception as e:
        logger.exception("[miku_github] git恢复异常")
        await git_restore_cmd.finish(f"恢复异常: {e}")


@git_list_cmd.handle()
async def _handle_git_list(bot: Bot, event: MessageEvent):
    if not _is_enabled():
        await git_list_cmd.finish("GitHub备份功能未启用")
    if not _is_superuser(event):
        await git_list_cmd.finish("只有超级用户可以查看备份列表")
    try:
        dirs = await list_backup_dirs()
        if not dirs:
            await git_list_cmd.finish("当前仓库中没有任何备份")
        lines = [f"GitHub备份列表（共 {len(dirs)} 个）:"]
        for d in dirs:
            p = parse_backup_dir(d)
            if p:
                age = (datetime.now() - p[0]).days
                lines.append(f"  {d}  ({age}天前)")
            else:
                lines.append(f"  {d}")
        await git_list_cmd.finish("\n".join(lines))
    except FinishedException:
        raise
    except Exception as e:
        logger.exception("[miku_github] git备份列表异常")
        await git_list_cmd.finish(f"查询异常: {e}")


# ============================================================
# 定时备份调度
# ============================================================
_github_scheduler = None
_github_started = False


def start_github_backup(driver=None) -> None:
    """启动每日自动备份调度"""
    global _github_scheduler, _github_started

    if not _is_enabled():
        logger.info("[miku_github] 自动备份已禁用")
        return

    ok, msg = _check_token_configured()
    if not ok:
        logger.warning(f"[miku_github] {msg}，自动备份未启动")
        return

    if _github_started:
        return
    _github_started = True

    backup_time_str = str(_conf("backup_time", "00:00") or "00:00").strip()
    m = re.match(r"^(\d{1,2}):(\d{2})$", backup_time_str)
    if not m:
        logger.warning(f"[miku_github] backup_time 配置非法: {backup_time_str!r}，使用默认 00:00")
        hour, minute = 0, 0
    else:
        hour, minute = int(m.group(1)), int(m.group(2))
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            hour, minute = 0, 0

    def _do_start():
        global _github_scheduler
        try:
            from apscheduler.schedulers.asyncio import AsyncIOScheduler
            from apscheduler.triggers.cron import CronTrigger
        except ImportError:
            logger.warning(
                "[miku_github] APScheduler 未安装，每日自动备份未启动。"
                "请执行: .venv\\Scripts\\python.exe -m pip install APScheduler"
            )
            return

        async def _daily_job():
            try:
                ok, msg = await do_backup()
                logger.info(f"[miku_github] 每日自动备份: {msg}")
                # 备份后清理过期
                await cleanup_expired_backups()
            except Exception as e:
                logger.exception(f"[miku_github] 每日自动备份异常: {e}")

        scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")
        scheduler.add_job(
            _daily_job,
            trigger=CronTrigger(hour=hour, minute=minute),
            id="miku_github_daily_backup",
            name=f"GitHub每日备份 {hour:02d}:{minute:02d}",
            max_instances=1,
            coalesce=True,
            misfire_grace_time=3600,
        )
        scheduler.start()
        _github_scheduler = scheduler
        logger.info(f"[miku_github] 每日自动备份已启动，{hour:02d}:{minute:02d} 执行，保留 {_conf('retention_days', 30)} 天")

    if driver is not None:
        @driver.on_bot_connect
        async def _on_bot_connect(bot):
            # 启动时确保仓库存在
            try:
                await ensure_repo()
            except Exception as e:
                logger.warning(f"[miku_github] 启动时仓库检查异常: {e}")
            _do_start()

        @driver.on_shutdown
        async def _on_shutdown():
            global _github_scheduler
            if _github_scheduler and _github_scheduler.running:
                try:
                    _github_scheduler.shutdown(wait=False)
                except Exception:
                    pass
                _github_scheduler = None
    else:
        _do_start()
