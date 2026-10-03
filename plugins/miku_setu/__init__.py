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
- 图片发送：bot 进程内预下载图片后以 base64 发送（协议端无需再 fetch URL，
          规避其 fetch 失败/超时）；每次命令只发一张
          （num 为候选数量，某张下载/发送失败自动换下一候选，不再连发多图）
- 安全：refresh_token 不输出到日志/群聊
- 触发：群聊+私聊均可用；priority=5 高于 miku_ai 的全消息监听(10)，
        命令命中后 block=True 阻断传播，避免 AI 插件对同一消息重复回复
- 撤回：withdraw_seconds（默认 60，0=关闭）秒后自动撤回**图片**（2026-10-03 起
        不再撤回文字提示，避免群内出现两条撤回记录）；计时起点为图片发送成功时；
        群聊/私聊均尝试（私聊撤回取决于协议端支持，失败会记录日志便于排查）
"""

import asyncio
import sys
from pathlib import Path
from typing import List, Optional, Set
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


_withdraw_tasks: Set["asyncio.Task"] = set()


async def _withdraw_later(bot: Bot, message_id: int, seconds: int, in_group: bool):
    """延时撤回单条消息（消息可能已被手动删除或超期，失败仅记日志不抛出）。"""
    chat_type = "群聊" if in_group else "私聊"
    await asyncio.sleep(seconds)
    try:
        await bot.delete_msg(message_id=message_id)
        logger.info(f"[miku_setu] 已自动撤回{chat_type}消息 msg_id={message_id}")
    except Exception as e:
        logger.warning(f"[miku_setu] 自动撤回失败（{chat_type}）msg_id={message_id}: {e}")


def _schedule_withdraw(bot: Bot, event: MessageEvent, message_id: Optional[int], seconds: int):
    """安排撤回任务；群聊/私聊均尝试（私聊撤回取决于协议端支持，失败可见于日志）。"""
    if seconds <= 0 or not message_id:
        return
    in_group = isinstance(event, GroupMessageEvent)
    chat_type = "群聊" if in_group else "私聊"
    logger.debug(f"[miku_setu] 计划 {seconds}s 后撤回{chat_type}消息 msg_id={message_id}")
    task = asyncio.create_task(_withdraw_later(bot, message_id, seconds, in_group))
    _withdraw_tasks.add(task)
    task.add_done_callback(_withdraw_tasks.discard)


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


def _item_tags(item: dict) -> List[str]:
    return [str(t).strip().lower() for t in (item.get("tags") or [])]


def _enforce_r18(items: List[dict]) -> List[dict]:
    """r18=0 时严格排除 R-18：API 的 r18 字段与 pixiv 的 R-18 标签不完全一致
    （实测存在字段 false 但图片带 R-18 标签的擦边图），两者任一命中即排除。"""
    try:
        r18_cfg = int(_get_cfg("r18", 1))
    except (TypeError, ValueError):
        r18_cfg = 1
    if r18_cfg != 0:
        return items
    return [it for it in items if not it.get("r18") and "r-18" not in _item_tags(it)]


def _filter_lolicon_items(items: List[dict], wanted: List[str]) -> List[dict]:
    """客户端复核搜索结果。

    Lolicon 的 tag 匹配偏宽松：搜 miku 会按拼音/别名混入中野三玖、田尻未来等
    其他角色（实测确认）。策略：优先保留标签精确命中的图片；若无精确命中
    再信任 API 原始结果（其别名库能正确处理「三玖」等中文别名，不宜一刀切丢弃）。
    """
    if wanted:
        strict = [it for it in items if any(w in _item_tags(it) for w in wanted)]
        if strict:
            items = strict
    return _enforce_r18(items)


async def _search_lolicon(word: str) -> List[dict]:
    """通过 Lolicon API 获取涩图（source=lolicon 时使用）。

    - 多关键词按空格拆分，tag 间为 OR 关系；多词时同时携带完整短语标签
    - API 响应波动大（实测 1s~20s+，偶发超时），失败自动重试一次
    - 结果经 _filter_lolicon_items 客户端复核
    """
    words = word.split()[:5]
    params: List[tuple] = [
        ("r18", str(_get_cfg("r18", 1))),
        ("num", str(_get_cfg("num", 3))),
    ]
    wanted: List[str] = []
    if words:
        tag_candidates = [" ".join(words)] + words if len(words) > 1 else list(words)
        for t in tag_candidates:
            key = t.strip().lower()
            if key and key not in wanted:
                wanted.append(key)
                params.append(("tag", t.strip()))
    for attempt in (1, 2):
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                r = await client.get("https://api.lolicon.app/setu/v2", params=params)
                if r.status_code == 200:
                    body = r.json()
                    if not body.get("error"):
                        return _filter_lolicon_items(body.get("data") or [], wanted)
                    logger.warning(f"[miku_setu] Lolicon 返回错误: {body.get('error')}")
                    return []
                logger.warning(f"[miku_setu] Lolicon HTTP {r.status_code}（第{attempt}次）")
        except Exception as e:
            logger.warning(f"[miku_setu] Lolicon 请求失败（第{attempt}次）: {e}")
        if attempt == 1:
            await asyncio.sleep(1.5)
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


async def _download_image(url: str) -> Optional[bytes]:
    """bot 进程内预下载图片，供 base64 发送（协议端不再自行 fetch URL）。"""
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            r = await client.get(url)
            if r.status_code == 200 and r.content:
                if len(r.content) > 20 * 1024 * 1024:
                    logger.warning(
                        f"[miku_setu] 图片过大（{len(r.content) // 1024 // 1024}MB），跳过该候选"
                    )
                    return None
                return r.content
            logger.warning(f"[miku_setu] 图片下载 HTTP {r.status_code}: {url}")
    except Exception as e:
        logger.warning(f"[miku_setu] 图片下载失败: {e}")
    return None


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
        await setu_cmd.send(f"🔍 正在搜索「{tag}」的涩图...")
        items = await _search_illust(token, tag)
        if not items:
            await setu_cmd.finish(f"❌ 未搜索到「{tag}」的插画")
    else:
        await setu_cmd.send(f"🔍 正在获取「{tag or '随机涩图'}」...")
        items = await _search_lolicon(tag)
        if not items:
            await setu_cmd.finish(f"❌ 未找到「{tag or '涩图'}」相关图片，换个词试试")

    for item in items:
        img_url = _build_image_url(item)
        if not img_url:
            continue
        img_bytes = await _download_image(img_url)
        if not img_bytes:
            continue
        try:
            # 图片已转为 base64 内联发送，协议端无需再 fetch URL；
            # _timeout 按次放宽到 90s，保证大图上传拿到 message_id 以便撤回
            if isinstance(event, GroupMessageEvent):
                ret = await bot.call_api(
                    "send_group_msg",
                    group_id=event.group_id,
                    message=MessageSegment.image(img_bytes),
                    _timeout=90,
                )
            else:
                ret = await bot.call_api(
                    "send_private_msg",
                    user_id=event.user_id,
                    message=MessageSegment.image(img_bytes),
                    _timeout=90,
                )
            msg_id = _extract_msg_id(ret)
            _schedule_withdraw(bot, event, msg_id, withdraw)
            return
        except Exception as e:
            logger.warning(f"[miku_setu] 发送图片失败，尝试下一候选: {e}")

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
