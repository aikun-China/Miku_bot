"""
MikuBot AI 聊天插件
====================
- 支持 云端 AI（OpenAI 兼容协议：OpenAI / DeepSeek / 通义千问 / 智谱等）
- 支持 本地 AI（Ollama / LM Studio 等本地服务提供 OpenAI 兼容接口）
- 支持 识图（图片理解 vision）
- 群聊：仅 @Bot 时回复
- 单聊：每条消息都回复
- 聊天记录：分别保存群聊和单聊历史
- 人格提示词：plugins/miku_ai/personality.txt（默认是初音未来的人设）
- 分段回答：长回复自动按段落拆分发送
"""

from nonebot import on_message, on_command, get_driver
from nonebot.adapters.onebot.v11 import (
    Bot, Message, MessageEvent, MessageSegment, GroupMessageEvent, PrivateMessageEvent,
)
from nonebot.rule import Rule
from nonebot.typing import T_State
from nonebot.log import logger
from nonebot.exception import FinishedException

import sys
import time
import asyncio
import json
from pathlib import Path
from typing import List, Optional, Dict

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

try:
    from plugins.miku_menu import register_plugin_info
except ImportError:
    register_plugin_info = lambda *args, **kwargs: None

try:
    from plugins.miku_stats import record_plugin_usage
except ImportError:
    try:
        from utils.plugin_stats import record_plugin_usage
    except ImportError:
        record_plugin_usage = lambda *args, **kwargs: None

from utils.html_render import render_template
from utils.screenshot import screenshot_html, to_image_uri
from utils.bg_helper import find_and_load_bg
try:
    from utils.balance_check import get_cached_balance
except ImportError:
    get_cached_balance = None
from .config import get_config, is_enabled, BASE_DIR, TEMPLATES_DIR, BALANCE_CACHE_DIR
from .data_source import (
    process_chat, split_reply_segments, save_history_async,
    get_history_manager, build_system_prompt,
)
from .emoji_library import EmojiLibrary


_driver = get_driver()

# ── 按会话维度的消息处理队列 ──
# 同一会话（群/私聊）的消息排队执行，避免并发导致的历史记录混乱和API超限
_session_locks: Dict[str, asyncio.Lock] = {}
_session_queue_count: Dict[str, int] = {}  # 每个会话的待处理消息数
_session_locks_lock = asyncio.Lock()


async def _get_session_lock(session_id: str) -> asyncio.Lock:
    """获取指定会话的锁，不存在则创建"""
    async with _session_locks_lock:
        if session_id not in _session_locks:
            _session_locks[session_id] = asyncio.Lock()
        return _session_locks[session_id]


def _get_max_queue(is_group: bool) -> int:
    """获取对应会话类型的队列深度上限"""
    if is_group:
        return int(get_config("group_max_queue", 3) or 3)
    return int(get_config("private_max_queue", 5) or 5)


async def _try_enqueue(session_id: str, max_depth: int) -> bool:
    """尝试入队，返回 True=可入队，False=队列已满"""
    async with _session_locks_lock:
        count = _session_queue_count.get(session_id, 0)
        if count >= max_depth:
            return False
        _session_queue_count[session_id] = count + 1
        return True


async def _dequeue(session_id: str):
    """出队，并清理闲置锁"""
    async with _session_locks_lock:
        count = _session_queue_count.get(session_id, 0)
        if count <= 1:
            _session_queue_count.pop(session_id, None)
            # 锁无人等待时清理，避免内存泄漏
            lock = _session_locks.get(session_id)
            if lock and not lock.locked():
                _session_locks.pop(session_id, None)
        else:
            _session_queue_count[session_id] = count - 1


@_driver.on_startup
async def _on_startup():
    EmojiLibrary.init_db()
    if bool(get_config("emoji_auto_cleanup", True)):
        EmojiLibrary.cleanup_old()
    logger.info("[miku_ai] 插件已加载")


@_driver.on_shutdown
async def _on_shutdown():
    await save_history_async()
    logger.info("[miku_ai] 聊天记录已保存")


register_plugin_info(
    "miku_ai",
    name="AI聊天",
    icon="🤖",
    order=3,
    description="AI聊天插件，支持云端/本地模型、识图、好感度系统",
    commands=["清空聊天记录", "查询余额", "查询好感度"],
    usage="""AI聊天功能：

【私聊】直接发消息即可对话
【群聊】@Bot 后对话

【命令】
  清空聊天记录 - 清空当前会话记录
  查询余额 - 查询AI API余额
  查询好感度 - 查询当前好感度""",
)


async def _is_talking_to_bot(event: MessageEvent, bot: Bot) -> bool:
    try:
        if not is_enabled():
            return False

        if isinstance(event, PrivateMessageEvent):
            result = bool(get_config("private_reply_every", True))
            logger.info(f"[miku_ai] 私聊检测: user_id={event.user_id}, result={result}")
            return result

        if isinstance(event, GroupMessageEvent):
            if not bool(get_config("group_reply_on_mention", True)):
                return False

            # NoneBot 在 @bot 时会移除 at 段并设置 to_me=True，优先检测
            if getattr(event, "to_me", False):
                return True

            # 备用：手动检测 at 段（某些协议端可能不设置 to_me）
            msg = event.get_message()
            bot_self_id = str(bot.self_id)
            for seg in msg:
                if seg.type == "at":
                    at_qq = str(seg.data.get("qq", ""))
                    if at_qq == bot_self_id:
                        return True

        return False
    except Exception as e:
        logger.error(f"[miku_ai] _is_talking_to_bot 异常: {e}")
        return False


def _extract_text_and_images(event: MessageEvent, reply_msg=None):
    msg = event.get_message()
    text_parts: List[str] = []
    image_urls: List[str] = []
    image_files: List[str] = []
    reply_id = None
    
    for seg in msg:
        if seg.type == "text":
            t = str(seg.data.get("text", "")).strip()
            if t:
                text_parts.append(t)
        elif seg.type == "image":
            url = seg.data.get("url", "")
            file = seg.data.get("file", "")
            if url:
                image_urls.append(url)
                image_files.append(file)
        elif seg.type == "reply":
            reply_id = seg.data.get("id", "")
    
    if reply_msg:
        reply_text_parts: List[str] = []
        
        for seg in reply_msg:
            if seg.type == "text":
                t = str(seg.data.get("text", "")).strip()
                if t:
                    reply_text_parts.append(t)
        
        if reply_text_parts:
            reply_text = ' '.join(reply_text_parts)
            # 引用消息单独限制长度，防止 token 爆炸
            max_quoted = int(get_config("max_quoted_chars", 300) or 300)
            if len(reply_text) > max_quoted:
                reply_text = reply_text[:max_quoted] + "...（引用已截断）"
            text_parts.insert(0, f"【引用】{reply_text}")
    
    text = "\n".join(text_parts).strip()
    return text, image_urls, image_files, reply_id


def _build_card_html(reply: str, favor_info: dict = None) -> str:
    bg_image = find_and_load_bg("chat")
    html = render_template(
        "chat_card.html",
        template_dir=TEMPLATES_DIR,
        reply_text=reply,
        bg_image=bg_image,
        favor_info=favor_info or {},
        show_favor=bool(get_config("show_favor_change", True)),
    )
    return html


_chat_matcher = on_message(rule=Rule(_is_talking_to_bot), priority=10, block=False)


@_chat_matcher.handle()
async def _handle_chat(bot: Bot, event: MessageEvent, state: T_State):
    # ── 按会话排队，同会话消息一个一个处理 ──
    user_id = str(event.user_id)
    group_id = str(event.group_id) if isinstance(event, GroupMessageEvent) else ""
    session_id = f"g{group_id}" if group_id else f"u{user_id}"

    # 队列深度检查，防刷屏（群聊/私聊不同上限）
    is_group = bool(group_id)
    max_depth = _get_max_queue(is_group)
    if not await _try_enqueue(session_id, max_depth):
        logger.info(f"[miku_ai] 会话{session_id}队列已满，丢弃消息")
        try:
            await _chat_matcher.send("主人发太快啦，Miku跟不上啦～等一下再说吧！")
        except Exception:
            pass
        await _chat_matcher.finish()
        return

    session_lock = await _get_session_lock(session_id)
    if session_lock.locked():
        logger.info(f"[miku_ai] 会话{session_id}有正在处理的消息，排队等待")

    try:
        async with session_lock:
            # 超时保护：单条消息处理最多120秒，防止API卡死导致会话永久阻塞
            try:
                await asyncio.wait_for(
                    _handle_chat_inner(bot, event, state),
                    timeout=120,
                )
            except asyncio.TimeoutError:
                logger.error(f"[miku_ai] 会话{session_id}处理超时(120s)，强制释放")
                try:
                    await _chat_matcher.send("Miku思考太久了，脑子过热啦...等一下再试试吧～")
                except Exception:
                    pass
    finally:
        await _dequeue(session_id)


async def _handle_chat_inner(bot: Bot, event: MessageEvent, state: T_State):
    try:
        user_id = str(event.user_id)
        user_name = getattr(event.sender, "nickname", "") or str(user_id)
        group_id = str(event.group_id) if isinstance(event, GroupMessageEvent) else ""
        
        reply_msg = None
        msg = event.get_message()
        for seg in msg:
            if seg.type == "reply":
                reply_id = seg.data.get("id", "")
                if reply_id:
                    try:
                        reply_raw = await bot.get_msg(message_id=int(reply_id))
                        if isinstance(reply_raw, dict):
                            reply_msg = Message(reply_raw.get("message", []))
                        else:
                            reply_msg = reply_raw
                    except Exception as e:
                        logger.warning(f"[miku_ai] 获取引用消息失败: {e}")
                break
        
        text, image_urls, image_files, _ = _extract_text_and_images(event, reply_msg)
        
        if not text and not image_urls:
            if isinstance(event, GroupMessageEvent):
                msg = event.get_message()
                bot_self_id = str(bot.self_id)
                for seg in msg:
                    if seg.type == "at" and str(seg.data.get("qq", "")) == bot_self_id:
                        text = "你在叫我吗？"
                        break
            if not text and not image_urls:
                await _chat_matcher.finish()
                return
        
        # 群聊中过滤掉 @Bot 的文本，但保留已提取的引用前缀
        if isinstance(event, GroupMessageEvent):
            filtered_segs = []
            for seg in msg:
                if seg.type == "at" and str(seg.data.get("qq", "")) == str(bot.self_id):
                    continue
                filtered_segs.append(seg)
            text_parts_new = []
            for seg in filtered_segs:
                if seg.type == "text":
                    t = str(seg.data.get("text", "")).strip()
                    if t:
                        text_parts_new.append(t)
            # 保留引用前缀（如果有的话）
            prefix = ""
            if text.startswith("【引用】"):
                idx = text.find("\n")
                prefix = text[:idx] if idx >= 0 else text
            text_body = "\n".join(text_parts_new).strip()
            text = (prefix + "\n" + text_body).strip() if prefix else text_body
        
        # 群聊仅在 @Bot 时触发回复
        try:
            reply, extra = await process_chat(
                user_id=user_id,
                user_name=user_name,
                text=text,
                image_urls=image_urls,
                group_id=group_id,
                image_files=image_files,
            )
        except Exception as e:
            import traceback
            logger.error(f"[miku_ai] AI调用失败: {e}\n{traceback.format_exc()}")
            # 萌系提示，不暴露原始错误给用户
            err_msg = str(e) if "Miku" in str(e) or "主人" in str(e) else "呜...Miku脑子卡住了...等一下再试试吧～"
            await _chat_matcher.finish(err_msg)
            return
        
        try:
            record_plugin_usage("miku_ai", "chat", user_id, group_id)
        except Exception:
            pass
        
        show_favor = bool(get_config("show_favor_change", True))
        favor_changed = extra.get("favor_changed", False)
        favor_delta = extra.get("favor_delta", 0.0)
        favor_new = extra.get("favor_new", 0.0)
        
        favor_info = {}
        if show_favor and favor_changed:
            favor_info = {
                "changed": True,
                "delta": favor_delta,
                "new": favor_new,
                "is_increase": favor_delta > 0,
            }
        
        segments = split_reply_segments(reply, max_length=150)
        response_style = str(get_config("response_style", "text") or "text").lower()

        # leekchat风格：发送表情包
        emoji_path = extra.get("emoji_path")

        if response_style == "card" and len(segments) == 1 and not emoji_path:
            try:
                html = _build_card_html(reply, favor_info)
                img_bytes = await screenshot_html(html, wait=1.0)
                if img_bytes:
                    img_uri = to_image_uri(img_bytes)
                    await _chat_matcher.finish(MessageSegment.image(img_uri))
                    return
            except Exception as e:
                logger.warning(f"[miku_ai] 卡片渲染失败，降级为文本: {e}")

        for i, seg in enumerate(segments):
            try:
                await _chat_matcher.send(seg)
            except Exception as send_err:
                # 单条发送失败（如 waitForSelfEcho timeout），等待后重试一次
                logger.warning(f"[miku_ai] 第{i+1}段发送失败: {send_err}，2秒后重试")
                await asyncio.sleep(2.0)
                try:
                    await _chat_matcher.send(seg)
                except Exception:
                    logger.error(f"[miku_ai] 第{i+1}段重试仍失败，跳过")
            if i < len(segments) - 1:
                # 段间延迟 1.5 秒，避免 NapCat waitForSelfEcho timeout
                await asyncio.sleep(1.5)

        # 发送表情包（如果有）
        if emoji_path:
            try:
                from pathlib import Path
                p = Path(emoji_path)
                if p.exists():
                    await _chat_matcher.send(MessageSegment.image(p.read_bytes()))
            except Exception as e:
                logger.warning(f"[miku_ai] 发送表情包失败: {e}")

        await _chat_matcher.finish()
    
    except FinishedException:
        raise
    except Exception as e:
        logger.error(f"[miku_ai] 处理消息异常: {e}")
        try:
            await _chat_matcher.finish("呜...出了点问题...")
        except Exception:
            pass


_clear_cmd = on_command("清空聊天记录", aliases={"清除聊天记录", "重置聊天"}, priority=5, block=True)


@_clear_cmd.handle()
async def _handle_clear(event: MessageEvent):
    if not is_enabled():
        await _clear_cmd.finish()
        return
    
    user_id = str(event.user_id)
    group_id = str(event.group_id) if isinstance(event, GroupMessageEvent) else ""
    session_id = get_history_manager().get_session_id(user_id, group_id)
    
    get_history_manager().clear_history(session_id)
    await save_history_async()
    
    if isinstance(event, GroupMessageEvent):
        await _clear_cmd.finish("好的~ 群聊记录已经清空啦♪")
    else:
        await _clear_cmd.finish("好的~ 我们的聊天记录已经清空啦♪")


_balance_cmd = on_command("查询余额", aliases={"余额查询", "查余额"}, priority=5, block=True)


@_balance_cmd.handle()
async def _handle_balance(bot: Bot, event: MessageEvent):
    if not is_enabled():
        await _balance_cmd.finish()
        return
    
    if get_cached_balance is None:
        await _balance_cmd.finish("余额查询功能未启用，请检查依赖安装")
        return
    
    api_key = str(get_config("cloud_api_key", "") or "")
    base_url = str(get_config("cloud_base_url", "") or "").rstrip("/")
    model = str(get_config("cloud_model", "") or "")
    
    if not api_key or "localhost" in base_url or "127.0.0.1" in base_url:
        await _balance_cmd.finish("当前使用本地模式，没有余额查询哦~")
        return
    
    try:
        balance_info = await get_cached_balance(
            api_key=api_key,
            base_url=base_url,
            model=model,
            cache_dir=BALANCE_CACHE_DIR,
            cache_minutes=int(get_config("balance_cache_minutes", 5) or 5),
        )
        
        lines = ["💎 AI 余额查询"]
        if balance_info.get("total") is not None:
            lines.append(f"总额度: ${balance_info['total']:.4f}")
        if balance_info.get("available") is not None:
            lines.append(f"可用余额: ${balance_info['available']:.4f}")
        if balance_info.get("used") is not None:
            lines.append(f"已使用: ${balance_info['used']:.4f}")
        if balance_info.get("is_granted"):
            lines.append("（免费赠送额度）")
        
        if len(lines) <= 1:
            lines.append("暂无余额信息")
        
        await _balance_cmd.finish("\n".join(lines))
    except Exception as e:
        logger.warning(f"[miku_ai] 余额查询失败: {e}")
        await _balance_cmd.finish(f"查询余额失败了... {e}")


_favor_cmd = on_command("查询好感度", aliases={"好感度查询", "查好感度"}, priority=5, block=True)


@_favor_cmd.handle()
async def _handle_favor(event: MessageEvent):
    if not is_enabled():
        await _favor_cmd.finish()
        return
    
    user_id = str(event.user_id)
    favor = get_history_manager().get_favor(user_id)
    
    if favor >= 80:
        level = "超喜欢"
        emoji = "💚"
    elif favor >= 50:
        level = "很喜欢"
        emoji = "💚"
    elif favor >= 20:
        level = "有好感"
        emoji = "💛"
    elif favor >= 0:
        level = "普通"
        emoji = "🤍"
    elif favor >= -20:
        level = "有点讨厌"
        emoji = "💔"
    else:
        level = "非常讨厌"
        emoji = "💔"
    
    msg = f"好感度: {favor:.2f}/100\n等级: {level} {emoji}"
    await _favor_cmd.finish(msg)
