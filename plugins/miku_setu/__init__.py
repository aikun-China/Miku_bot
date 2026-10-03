"""
Miku 涩图 (send_setu 适配版)
============================
基于 zhenxun_bot 的 send_setu 插件重构，适配 MikuBot 架构：

- 网络：用 httpx 替代 Zhenxun 的 AsyncHttpx（异步优先）
- 数据源：source=lolicon（默认，api.lolicon.app 国内直连，无需账号/token）
          source=pixiv（走代理的 Pixiv App API 搜索，需 refresh_token，
          注意 pixiv 对 Cloudflare Workers 出口 IP 有 WAF 封锁，目前不可用）
- 图片：两种源的图片 URL 均替换为自定义代理域名（pixiv_proxy_host，
        自研 Worker pixiv.aikun-bili.top，仅图片链路可用）
- 配置：source / r18 / num / pixiv_proxy_host / pixiv_refresh_token 写入 config/bot.yaml
- 图片发送：NoneBot2 原生 MessageSegment.image
- 安全：refresh_token 不输出到日志/群聊
- 触发：群聊+私聊均可用；priority=5 高于 miku_ai 的全消息监听(10)，
        命令命中后 block=True 阻断传播，避免 AI 插件对同一消息重复回复
- 撤回：withdraw_seconds（默认 60，0=关闭）秒后自动撤回提示与图片（仅群聊，
        私聊消息协议端通常不支持撤回）；计时起点为各消息自身发送成功时
"""

import asyncio
import sys
from pathlib import Path
from typing import List, Optional
from urllib.parse import urlparse

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from nonebot import on_command
from nonebot.adapters.onebot.v11 import (
    Bot,
    MessageEvent,
    Message,
    MessageSegment,
    GroupMessageEvent,
)
from nonebot.plugin import PluginMetadata
from nonebot.log import logger
from nonebot.params import CommandArg
import httpx

try:
    from utils.plugin_registry import register_plugin_info
except ImportError:
    register_plugin_info = lambda *args, **kwargs: None

try:
    from plugins.miku_stats import record_plugin_usage
except ImportError:
    record_plugin_usage = lambda *args, **kwargs: None

try:
    from utils.config_manager import config_manager
except ImportError:
    config_manager = None


__plugin_meta__ = PluginMetadata(
    name="Miku涩图",
    description="Pixiv 涩图（send_setu 适配版，经自定义代理）",
    usage="涩图 <tag> / 来点涩图",
    type="application",
    supported_adapters={"~onebot.v11"},
)


# Pixiv 官方公开 OAuth 客户端（用于 refresh_token 换取 access_token）
_PIXIV_CLIENT_ID = "MOBrBDS8blbauoSck0ZfDbtuzpyT"
_PIXIV_CLIENT_SECRET = "lsACyCD94FhDUtGTt3rICgW1RhEwlw7pmJPZt2Y"
_PIXIV_OAUTH = "https://oauth.secure.pixiv.net/auth/token"


def _get_cfg(key: str, default=None):
    if config_manager is None:
        return default
    try:
        return config_manager.get("miku_setu", key, default)
    except Exception:
        return default


def _get_proxy_host() -> str:
    host = str(_get_cfg("pixiv_proxy_host", "") or "").strip().rstrip("/")
    return host


def _extract_msg_id(ret) -> Optional[int]:
    """从 OneBot send 响应提取 message_id（兼容 dict 与裸值）。"""
    mid = ret.get("message_id") if isinstance(ret, dict) else ret
    try:
        return int(mid)
    except (TypeError, ValueError):
        return None


async def _withdraw_later(bot: Bot, message_id: int, seconds: int):
    """延时撤回单条消息（失败静默：消息可能已被手动删除或超时）。"""
    await asyncio.sleep(seconds)
    try:
        await bot.delete_msg(message_id=message_id)
    except Exception:
        pass


def _schedule_withdraw(bot: Bot, event: MessageEvent, message_id: Optional[int], seconds: int):
    """安排撤回任务；仅群聊生效，seconds<=0 或无 message_id 时跳过。"""
    if seconds <= 0 or not isinstance(event, GroupMessageEvent) or not message_id:
        return
    asyncio.create_task(_withdraw_later(bot, message_id, seconds))


def _get_refresh_token() -> str:
    return str(_get_cfg("pixiv_refresh_token", "") or "").strip()


async def _get_access_token() -> Optional[str]:
    """用 refresh_token 换取 access_token。"""
    token = _get_refresh_token()
    if not token:
        return None
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(
                _PIXIV_OAUTH,
                data={
                    "client_id": _PIXIV_CLIENT_ID,
                    "client_secret": _PIXIV_CLIENT_SECRET,
                    "grant_type": "refresh_token",
                    "refresh_token": token,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            if r.status_code == 200:
                return r.json().get("access_token")
    except Exception as e:
        logger.warning(f"[miku_setu] 获取 Pixiv access_token 失败: {e}")
    return None


async def _search_illust(access_token: str, word: str, limit: int = 6) -> List[dict]:
    """通过代理搜索 Pixiv 插画（source=pixiv 时使用）。"""
    host = _get_proxy_host()
    if not host:
        return []
    url = f"{host}/app-api.pixiv.net/v2/illust/search"
    params = {"word": word, "search_target": "partial_match_for_tags", "limit": limit}
    headers = {"Authorization": f"Bearer {access_token}", "Referer": "https://app-api.pixiv.net/"}
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            r = await client.get(url, params=params, headers=headers)
            if r.status_code == 200:
                return r.json().get("illusts") or []
    except Exception as e:
        logger.warning(f"[miku_setu] 搜索 Pixiv 失败: {e}")
    return []


async def _search_lolicon(word: str) -> List[dict]:
    """通过 Lolicon API 获取涩图（source=lolicon 时使用，tag 为 OR 匹配）。"""
    params: List[tuple] = [
        ("r18", str(_get_cfg("r18", 1))),
        ("num", str(_get_cfg("num", 3))),
    ]
    for t in word.split():
        params.append(("tag", t))
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get("https://api.lolicon.app/setu/v2", params=params)
            if r.status_code == 200:
                body = r.json()
                if not body.get("error"):
                    return body.get("data") or []
                logger.warning(f"[miku_setu] Lolicon 返回错误: {body.get('error')}")
    except Exception as e:
        logger.warning(f"[miku_setu] Lolicon 搜索失败: {e}")
    return []


def _build_image_url(item: dict) -> Optional[str]:
    """构造经代理的图片 URL（兼容 Pixiv illust 与 Lolicon item 两种结构）。"""
    proxy = _get_proxy_host()
    if "urls" in item:
        img_url = (item.get("urls") or {}).get("original")
    else:
        img_url = item.get("meta_single_page", {}).get("original_image_url")
        if not img_url:
            pages = item.get("meta_pages") or []
            if pages:
                img_url = pages[0].get("image_urls", {}).get("original")
    if not img_url:
        return None
    if proxy:
        return f"{proxy}{urlparse(img_url).path}"
    if "i.pximg.net" in img_url:
        return None
    return img_url


setu_cmd = on_command(
    "涩图",
    aliases={"来点涩图", "涩涩", "setu"},
    priority=5,
    block=True,
)


@setu_cmd.handle()
async def _setu_handler(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    if not _get_proxy_host():
        await setu_cmd.finish(
            "❌ 图片代理未配置\n"
            "请在 config/bot.yaml 的 miku_setu 区填写 pixiv_proxy_host"
        )

    source = str(_get_cfg("source", "lolicon") or "lolicon").strip().lower()
    tag = args.extract_plain_text().strip()
    record_plugin_usage("miku_setu", user_id=str(event.user_id), command_name="涩图")

    withdraw = int(_get_cfg("withdraw_seconds", 60) or 0)

    items: List[dict] = []
    if source == "pixiv":
        if not tag:
            tag = "原神"
        token = await _get_access_token()
        if not token:
            await setu_cmd.finish(
                "❌ 未获取到 Pixiv access_token\n"
                "请检查 config/bot.yaml 中的 pixiv_refresh_token 是否有效"
            )
        tip_id = _extract_msg_id(await setu_cmd.send(f"🔍 正在搜索「{tag}」的涩图..."))
        _schedule_withdraw(bot, event, tip_id, withdraw)
        items = await _search_illust(token, tag)
        if not items:
            await setu_cmd.finish(f"❌ 未搜索到「{tag}」的插画")
    else:
        tip_id = _extract_msg_id(await setu_cmd.send(f"🔍 正在获取「{tag or '随机涩图'}」..."))
        _schedule_withdraw(bot, event, tip_id, withdraw)
        items = await _search_lolicon(tag)
        if not items:
            await setu_cmd.finish(f"❌ 未找到「{tag or '涩图'}」相关图片，换个词试试")

    sent = 0
    for item in items:
        if sent >= 3:
            break
        img_url = _build_image_url(item)
        if not img_url:
            continue
        try:
            msg_id = _extract_msg_id(await bot.send(event, MessageSegment.image(img_url)))
            sent += 1
            _schedule_withdraw(bot, event, msg_id, withdraw)
        except Exception as e:
            logger.warning(f"[miku_setu] 发送图片失败: {e}")

    if sent == 0:
        await setu_cmd.finish("❌ 图片发送失败，请检查代理配置")


register_plugin_info(
    "miku_setu",
    name="Miku涩图",
    icon="🌊",
    order=10,
    description="涩图（Lolicon 源 / Pixiv 源，图片走自建代理）",
    commands=["涩图"],
    usage="涩图 <tag> / 来点涩图",
)

logger.info("[miku_setu] 涩图插件已加载（source 配置: lolicon=默认国内直连 / pixiv=需 refresh_token）")
