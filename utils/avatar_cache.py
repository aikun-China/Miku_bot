"""
头像缓存工具
=============
- 下载 QQ 用户头像到本地缓存，供所有插件共用
- 缓存目录：data/avatars/{user_id}.jpg
- 每次调用会检查文件是否存在，不存在则下载
- 即使被定时任务清理，下次调用也会自动重新下载

用法：
    from utils.avatar_cache import get_avatar_data_uri, get_avatar_path

    # 获取头像的 data URI（直接在 HTML img src 使用）
    avatar_uri = await get_avatar_data_uri(user_id, default_char="M")

    # 获取头像本地路径（供其他插件使用）
    avatar_path = get_avatar_path(user_id)
    if avatar_path and avatar_path.exists():
        ...
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Optional

try:
    import httpx
except ImportError:
    httpx = None

PROJECT_ROOT = Path(__file__).resolve().parent.parent
# 放在 config/avatars 下，和配置在一起，不会被任何定时任务清理
AVATAR_DIR = PROJECT_ROOT / "config" / "avatars"
AVATAR_DIR.mkdir(parents=True, exist_ok=True)

# QQ 头像 URL：s=640 是高清
_AVATAR_URL = "https://q1.qlogo.cn/g?b=qq&nk={user_id}&s=640"


def get_avatar_path(user_id: int | str) -> Path:
    """获取头像缓存文件路径。文件可能不存在（还未下载 / 已被清理）。"""
    return AVATAR_DIR / f"{user_id}.jpg"


async def download_avatar(user_id: int | str, timeout: int = 10) -> Optional[Path]:
    """下载 QQ 用户头像到本地缓存。成功返回文件路径，失败返回 None。"""
    if httpx is None:
        return None

    target = get_avatar_path(user_id)
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            r = await client.get(_AVATAR_URL.format(user_id=user_id))
            if r.status_code != 200:
                return None
            data = r.content
            if not data or len(data) < 200:
                return None
            target.write_bytes(data)
            return target
    except Exception:
        return None


def _file_to_data_uri(path: Path) -> str:
    """把本地图片文件转为 data:image URI。"""
    try:
        data = path.read_bytes()
        if not data:
            return ""
        b64 = base64.b64encode(data).decode("ascii")
        return f"data:image/jpeg;base64,{b64}"
    except Exception:
        return ""


def _generate_default_avatar(char: str) -> str:
    """生成一个带有默认字符的圆形头像 SVG。"""
    char = str(char or "U")[:1].upper()
    colors = [
        "#2BA89E", "#3A6E8E", "#D64545", "#7CB342", 
        "#FB8C00", "#E53935", "#8E24AA", "#1E88E5"
    ]
    # 使用字符的 Unicode 值来选择颜色，保持一致性
    color_idx = ord(char) % len(colors)
    bg_color = colors[color_idx]
    
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100" viewBox="0 0 100 100">
        <circle cx="50" cy="50" r="50" fill="{bg_color}"/>
        <text x="50" y="58" font-family="Arial, sans-serif" font-size="48" font-weight="bold" fill="white" text-anchor="middle">{char}</text>
    </svg>'''
    b64 = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    return f"data:image/svg+xml;base64,{b64}"


async def get_avatar_data_uri(
    user_id: int | str,
    default_char: str = "U",
) -> str:
    """获取用户头像的 data URI。
    - 若本地缓存存在 → 直接使用
    - 若本地缓存不存在 → 下载后使用
    - 下载失败 → 返回一个带有默认字符的圆形头像
    """
    path = get_avatar_path(user_id)
    if path.exists():
        uri = _file_to_data_uri(path)
        if uri:
            return uri
    # 缓存不存在或读取失败 → 尝试下载
    new_path = await download_avatar(user_id)
    if new_path and new_path.exists():
        uri = _file_to_data_uri(new_path)
        if uri:
            return uri
    # 下载失败，返回一个带有默认字符的圆形头像
    return _generate_default_avatar(default_char)
