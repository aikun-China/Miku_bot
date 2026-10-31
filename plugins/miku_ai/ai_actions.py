"""
Miku AI 自身能力注册为「可被 AI 路由的动作」
=============================================
这些动作用于让路由层在用户用口语表达时也能命中 AI 插件自己的功能
（记忆、好感度），而不是只依赖固定命令。

注意：``记住`` / ``查记忆`` / ``查询好感度`` 已经是 on_command 命令，
路由之前就会被命令 matcher 吃掉，所以这里的 patterns/keywords 只覆盖
命令覆盖不到的口语说法，避免重复执行。
"""

from __future__ import annotations

import time
from typing import Any, Dict

from nonebot.log import logger

from utils.ai_actions import ActionContext, register_ai_action

from .data_source import get_history_manager
from .memory_store import MemoryStore, build_namespaces


def _namespace(user_id: str, group_id: str) -> str:
    return build_namespaces(user_id, group_id)[0]


def _format_time(ts: int) -> str:
    try:
        return time.strftime("%m-%d %H:%M", time.localtime(int(ts)))
    except (TypeError, ValueError, OSError):
        return ""


def _favor_level(favor: float) -> str:
    if favor >= 80:
        return "超喜欢 💚"
    if favor >= 50:
        return "很喜欢 💚"
    if favor >= 20:
        return "有好感 💛"
    if favor >= 0:
        return "普通 🤍"
    if favor >= -20:
        return "有点讨厌 💔"
    return "非常讨厌 💔"


# ── memory.remember ──────────────────────────────────────────
async def _remember(ctx: ActionContext) -> Dict[str, Any]:
    content = ctx.arg_str("content")
    if not content:
        return {"status": "skip"}
    try:
        ok = MemoryStore.remember(_namespace(ctx.user_id, ctx.group_id), content, source="manual")
    except Exception as e:
        logger.warning(f"[miku_ai] AI 记忆写入失败: {e}")
        return {"status": "error", "reply": "呜...这条没记上，小本本出问题了"}
    if not ok:
        return {"status": "error", "reply": "呜...这条没记上，内容是不是空的呀？"}
    return {"status": "ok", "reply": f"好啦，Miku把「{content[:60]}」记在小本本上了♪"}


# ── memory.list ─────────────────────────────────────────────
async def _memory_list(ctx: ActionContext) -> Dict[str, Any]:
    namespace = _namespace(ctx.user_id, ctx.group_id)
    try:
        items = MemoryStore.list_memories(namespace, limit=10)
        total = MemoryStore.count(namespace)
    except Exception as e:
        logger.warning(f"[miku_ai] AI 记忆查询失败: {e}")
        return {"status": "error", "reply": "呜...小本本打不开了"}
    if not items:
        return {"status": "ok", "reply": "Miku的小本本上还没有关于你的记忆哦～可以教我：【记住 内容】"}
    lines = [f"📖 关于你的记忆（共{total}条，显示最近{len(items)}条）"]
    for i, item in enumerate(items, 1):
        lines.append(f"{i}. {item['content']}（{_format_time(item['updated_at'])}）")
    return {"status": "ok", "reply": "\n".join(lines)}


# ── favor.query ─────────────────────────────────────────────
async def _favor(ctx: ActionContext) -> Dict[str, Any]:
    try:
        favor = float(get_history_manager().get_favor(ctx.user_id))
    except Exception as e:
        logger.warning(f"[miku_ai] AI 好感度查询失败: {e}")
        return {"status": "error", "reply": "呜...好感度查不出来了"}
    return {
        "status": "ok",
        "reply": f"好感度: {favor:.2f}/100\n等级: {_favor_level(favor)}",
    }


register_ai_action(
    "memory.remember",
    plugin="miku_ai",
    plugin_display="AI聊天",
    description="帮你（Miku）记住一条关于用户的信息，写进长期记忆",
    handler=_remember,
    params={
        "content": {
            "type": "string",
            "required": True,
            "desc": "要记住的内容",
            "ask": "要Miku记什么呢？",
            "max_len": 100,
        }
    },
    examples=["记一下我喜欢喝可乐", "帮我记住我生日是3月9日", "你要记得我叫小爱"],
    patterns=[
        r"^(?:帮我|请|麻烦)?\s*(?:记一下|记一下呗|记着|记住一下|记下)[：:，,\s]*(?P<content>.{2,100})$",
        r"^(?:你)?(?:要|得)?记得[：:，,\s]*(?P<content>.{2,100})$",
    ],
    keywords=["记一下", "帮我记住", "你要记得"],
    order=40,
)

register_ai_action(
    "memory.list",
    plugin="miku_ai",
    plugin_display="AI聊天",
    description="查看Miku记住的关于当前用户的内容",
    handler=_memory_list,
    examples=["你还记得我什么", "你记了我什么", "看看我的记忆"],
    keywords=["你记得我什么", "你还记得我", "记了我什么", "我的记忆"],
    order=41,
)

register_ai_action(
    "favor.query",
    plugin="miku_ai",
    plugin_display="AI聊天",
    description="查询用户和Miku之间的好感度数值与等级",
    handler=_favor,
    examples=["我跟你关系怎么样", "我的好感度是多少", "你有多喜欢我"],
    patterns=[
        r"^(?:我)?(?:跟|和)?(?:你)?(?:的)?好感度(?:是多少|多少|怎么样|多少了|有几)[？?]?$",
    ],
    keywords=["好感度多少", "好感度是多少", "关系怎么样"],
    order=42,
)
