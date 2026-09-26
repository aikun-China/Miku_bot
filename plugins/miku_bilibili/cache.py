"""
Miku B站插件 - 缓存服务模块
提供会话级URL缓存和图片缓存
"""

import time
from pathlib import Path
from typing import Dict, Any, Optional

from nonebot.adapters.onebot.v11 import MessageEvent, GroupMessageEvent
from nonebot.log import logger

from .config import get_cache_ttl_minutes, AVATAR_CACHE_DIR, BANGUMI_COVER_CACHE_DIR, IMAGE_CACHE_DIR
from .credential import get_headers_with_cookie

from curl_cffi import requests as curl_requests


class SessionCache:
    """会话级URL缓存，避免短时间内重复解析同一URL"""

    def __init__(self, ttl_minutes: int = 5):
        self.ttl_seconds = ttl_minutes * 60
        self._cache: Dict[str, Dict[str, float]] = {}

    def _get_session_key(self, event: MessageEvent) -> str:
        """获取会话标识"""
        if isinstance(event, GroupMessageEvent):
            return f"group_{event.group_id}"
        return f"private_{event.user_id}"

    def _clean_expired(self, session_key: str) -> None:
        """清理过期缓存"""
        if session_key not in self._cache:
            return
        cached_urls = self._cache[session_key]
        current_time = time.time()
        expired = [u for u, t in cached_urls.items()
                   if current_time - t > self.ttl_seconds]
        for u in expired:
            del cached_urls[u]

    def should_parse(self, event: MessageEvent, url: str) -> bool:
        """检查是否需要解析（缓存未过期返回False）"""
        session_key = self._get_session_key(event)
        self._clean_expired(session_key)
        if session_key in self._cache:
            if url in self._cache[session_key]:
                return False
        return True

    def add(self, event: MessageEvent, url: str) -> None:
        """添加到缓存"""
        session_key = self._get_session_key(event)
        if session_key not in self._cache:
            self._cache[session_key] = {}
        self._cache[session_key][url] = time.time()


_session_cache: Optional[SessionCache] = None


def get_session_cache() -> SessionCache:
    """获取全局会话缓存实例"""
    global _session_cache
    if _session_cache is None:
        _session_cache = SessionCache(ttl_minutes=get_cache_ttl_minutes())
    return _session_cache


# ============================================================
# 图片缓存
# ============================================================

async def download_image(url: str, save_path: Path) -> bool:
    """下载图片到指定路径"""
    try:
        headers = get_headers_with_cookie()
        headers["Referer"] = "https://www.bilibili.com/"
        async with curl_requests.AsyncSession(impersonate="chrome131", timeout=30, headers=headers) as client:
            response = await client.get(url)
            response.raise_for_status()
            save_path.parent.mkdir(parents=True, exist_ok=True)
            save_path.write_bytes(response.content)
            return True
    except Exception as e:
        logger.debug(f"[miku_bilibili] 下载图片失败 {url}: {e}")
        return False


async def get_cached_avatar(uid: str, avatar_url: str) -> Optional[Path]:
    """获取缓存的用户头像路径，如果不存在则下载"""
    if not avatar_url or not uid:
        return None
    cached_path = AVATAR_CACHE_DIR / f"{uid}.png"
    if cached_path.exists() and cached_path.stat().st_size > 0:
        return cached_path
    if await download_image(avatar_url, cached_path):
        return cached_path
    return None


async def get_cached_bangumi_cover(season_or_ep_id: int, cover_url: str) -> Optional[Path]:
    """获取缓存的番剧或剧集封面路径，如果不存在则下载"""
    if not cover_url or not season_or_ep_id:
        return None
    cached_path = BANGUMI_COVER_CACHE_DIR / f"{season_or_ep_id}.png"
    if cached_path.exists() and cached_path.stat().st_size > 0:
        return cached_path
    if await download_image(cover_url, cached_path):
        return cached_path
    return None


async def get_cached_image(cache_key: str, image_url: str) -> Optional[Path]:
    """通用图片缓存获取"""
    if not image_url or not cache_key:
        return None
    import hashlib
    safe_key = hashlib.md5(cache_key.encode()).hexdigest()
    cached_path = IMAGE_CACHE_DIR / f"{safe_key}.jpg"
    if cached_path.exists() and cached_path.stat().st_size > 0:
        return cached_path
    if await download_image(image_url, cached_path):
        return cached_path
    return None
