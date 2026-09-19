"""
MikuBot 通知管理员插件
=====================
- 用户在群聊或私聊中发送「通知管理员」「对管理员说」等关键词时
- Bot 自动将消息以引用回复的形式转发给所有管理员
- 管理员收到的消息带有原消息引用框，可看到群名、昵称、QQ号和内容
- 管理员回复后，Bot 会把回复转发回原消息发送者
- 支持回复确认：管理员回复时可选择引用格式

指令：
  通知管理员 <内容>
  告诉管理员 <内容>
  对管理员说 <内容>
"""

import re
import sys
import json
import time
from pathlib import Path
from datetime import datetime

from nonebot import on_message, on_command, get_driver
from nonebot.adapters.onebot.v11 import (
    Bot, MessageEvent, GroupMessageEvent, PrivateMessageEvent,
)
from nonebot.adapters.onebot.v11.message import MessageSegment, Message
from nonebot.plugin import PluginMetadata
from nonebot.log import logger
from nonebot.exception import FinishedException

# 导入配置管理器
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from utils.config_manager import config_manager

# 导入菜单注册
try:
    from plugins.miku_menu import register_plugin_info
except ImportError:
    register_plugin_info = lambda *args, **kwargs: None


__plugin_meta__ = PluginMetadata(
    name="Miku通知管理员",
    description="用户发送通知管理员关键词时，以引用回复形式转发",
    usage="在任意群聊或私聊中发送「通知管理员 xxx」",
    type="application",
    supported_adapters={"~onebot.v11"},
)


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
NOTIFY_REPLY_FILE = PROJECT_ROOT / "data" / "notify_admin_replies.json"
NOTIFY_REPLY_FILE.parent.mkdir(parents=True, exist_ok=True)


# ============================================================
# 插件配置注册
# ============================================================
_cfg = config_manager.register_plugin(
    "miku_notify_admin",
    defaults={
        "enabled": True,
        "quote_style": True,
        "forward_images": True,
    },
    template_str=(
        "\n"
        "miku_notify_admin:\n"
        "  # 是否启用通知管理员功能\n"
        "  enabled: true\n"
        "  # 是否使用引用回复样式（true=引用框格式，false=纯文本）\n"
        "  quote_style: true\n"
        "  # 是否转发图片等富媒体内容\n"
        "  forward_images: true\n"
    ),
    description="通知管理员插件配置",
)


def _is_enabled() -> bool:
    raw = config_manager.get("miku_notify_admin", "enabled", True)
    return str(raw).strip().lower() not in ("false", "0", "no", "")


def _conf(key: str, default=None):
    return config_manager.get("miku_notify_admin", key, default)


# ============================================================
# 触发关键词列表
# ============================================================
_NOTIFY_KEYWORDS = [
    r"通知管理员",
    r"告诉管理员",
    r"对管理员说",
    r"跟管理员说",
    r"转告管理员",
    r"有事找管理员",
    r"跟他说",
    r"帮我跟管理员",
    r"帮我告诉管理员",
    r"麻烦告诉管理员",
    r"请告诉管理员",
    r"能不能告诉管理员",
]

_NOTIFY_PATTERNS = [re.compile(kw) for kw in _NOTIFY_KEYWORDS]


def _strip_command_prefix(text: str) -> str:
    stripped = text.lstrip()
    for prefix in ("/", "!", "！"):
        if stripped.startswith(prefix):
            return stripped[len(prefix):].lstrip()
    return stripped


def _match_notify_keyword(text: str) -> bool:
    clean_text = _strip_command_prefix(text)
    if not clean_text:
        return False
    for pattern in _NOTIFY_PATTERNS:
        if pattern.match(clean_text):
            return True
    return False


def _extract_notify_message(text: str) -> str:
    clean_text = _strip_command_prefix(text)
    for pattern in _NOTIFY_PATTERNS:
        match = pattern.match(clean_text)
        if match:
            after = clean_text[match.end():].strip()
            after = re.sub(r"^[,，、\s]+", "", after)
            return after
    return ""


# ============================================================
# 回复管理（管理员回复 → 转发回用户）
# ============================================================
_NEXT_NOTIFY_ID = 1


def _load_reply_queue() -> list:
    if NOTIFY_REPLY_FILE.exists():
        try:
            data = json.loads(NOTIFY_REPLY_FILE.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
        except Exception:
            pass
    return []


def _save_reply_queue(queue: list):
    NOTIFY_REPLY_FILE.write_text(
        json.dumps(queue, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _gen_notify_id() -> int:
    """生成唯一通知ID（按当前最大ID+1）"""
    global _NEXT_NOTIFY_ID
    queue = _load_reply_queue()
    max_id = 0
    for q in queue:
        if q.get("notify_id", 0) > max_id:
            max_id = q["notify_id"]
    _NEXT_NOTIFY_ID = max_id + 1
    return _NEXT_NOTIFY_ID


def _add_notify_to_queue(context: dict) -> int:
    """将一条新通知加入待回复队列（所有管理员共享，任意管理员可回复）
    返回通知ID
    """
    queue = _load_reply_queue()
    nid = _gen_notify_id()
    queue.append({
        "notify_id": nid,
        "reply_by": None,
        "reply_text": "",
        "user_id": context.get("user_id"),
        "user_nickname": context.get("user_nickname"),
        "group_id": context.get("group_id"),
        "group_name": context.get("group_name"),
        "original_content": context.get("original_content"),
        "timestamp": datetime.now().isoformat(),
        "handled": False,
    })
    _save_reply_queue(queue)
    return nid


def _find_pending_notify(notify_id: int | None = None) -> dict | None:
    """查找待回复通知
    notify_id=None → 返回最新一条未处理
    notify_id=数字 → 返回指定ID
    """
    queue = _load_reply_queue()
    pending = [q for q in queue if not q.get("handled")]
    if not pending:
        return None
    if notify_id is not None:
        for q in pending:
            if q.get("notify_id") == notify_id:
                return q
        return None
    # 返回最新一条
    return pending[-1]


def _mark_notify_handled(notify_id: int, reply_by: int, reply_text: str):
    """标记通知为已回复"""
    queue = _load_reply_queue()
    for i, item in enumerate(queue):
        if item.get("notify_id") == notify_id:
            queue[i]["handled"] = True
            queue[i]["reply_by"] = reply_by
            queue[i]["reply_text"] = reply_text
            queue[i]["reply_time"] = datetime.now().isoformat()
            break
    _save_reply_queue(queue)


# ============================================================
# 构建引用回复消息
# ============================================================
def _build_quote_message(event: MessageEvent, notify_content: str) -> Message:
    """构建带引用回复的消息（模拟引用框效果）"""
    quote_enabled = str(_conf("quote_style", True)).strip().lower() not in ("false", "0", "no")

    try:
        user_nickname = event.sender.nickname if hasattr(event.sender, "nickname") else "未知用户"
    except AttributeError:
        user_nickname = "未知用户"
    user_id = event.get_user_id()
    now_str = datetime.now().strftime("%H:%M")

    is_group = isinstance(event, GroupMessageEvent)

    msg = Message()

    if quote_enabled:
        # 引用框样式（通知内容在引用框内的底部）
        if is_group:
            try:
                group_name = event.group_name if hasattr(event, "group_name") else f"群{event.group_id}"
            except AttributeError:
                group_name = f"群{event.group_id}"

            quote_block = (
                f"━━ 引用消息 ━━\n"
                f"群聊名字：{group_name}\n"
                f"{user_nickname}({user_id})  {now_str}\n"
                f"━━━━━━━━━━━━━━━\n"
                f"{notify_content}"
            )
        else:
            quote_block = (
                f"━━ 引用消息 ━━\n"
                f"{user_nickname}({user_id})  {now_str}\n"
                f"━━━━━━━━━━━━━━━\n"
                f"{notify_content}"
            )

        msg.append(MessageSegment.text(quote_block))
    else:
        # 纯文本样式（兼容旧版）
        if is_group:
            try:
                group_name = event.group_name if hasattr(event, "group_name") else f"群{event.group_id}"
            except AttributeError:
                group_name = f"群{event.group_id}"
            msg.append(MessageSegment.text(
                f"📨 通知管理员\n"
                f"群聊：{group_name}\n"
                f"{user_nickname}({user_id})：{notify_content}"
            ))
        else:
            msg.append(MessageSegment.text(
                f"📨 通知管理员\n"
                f"{user_nickname}({user_id})：{notify_content}"
            ))

    return msg


def _build_reply_quote_message(event: MessageEvent, original: dict, reply_text: str) -> Message:
    """构建管理员回复的引用消息（发给原用户）"""
    try:
        admin_nickname = event.sender.nickname if hasattr(event.sender, "nickname") else "管理员"
    except AttributeError:
        admin_nickname = "管理员"
    admin_id = event.get_user_id()
    now_str = datetime.now().strftime("%H:%M")

    msg = Message()

    # 引用框
    group_info = f"群聊名字：{original.get('group_name', '')}\n" if original.get("group_name") else ""
    quote_block = (
        f"━━ 管理员回复 ━━\n"
        f"{group_info}"
        f"用户 {original.get('user_nickname', '')}({original.get('user_id', '')})  {now_str}\n"
        f"原内容：{original.get('original_content', '')}\n"
        f"━━━━━━━━━━━━━━━"
    )

    msg.append(MessageSegment.text(quote_block + "\n\n"))
    msg.append(MessageSegment.text(f"@{original.get('user_nickname', '用户')} {reply_text}"))

    return msg


# ============================================================
# 消息处理器：通知管理员
# ============================================================
notify_handler = on_message(priority=10, block=False)


@notify_handler.handle()
async def handle_notify(bot: Bot, event: MessageEvent):
    try:
        if not _is_enabled():
            return

        user_id = event.get_user_id()

        try:
            superusers = config_manager.superusers
        except Exception:
            superusers = []

        if user_id in superusers:
            return

        message_text = str(event.get_message()).strip()
        if not message_text:
            return

        if not _match_notify_keyword(message_text):
            return

        notify_content = _extract_notify_message(message_text)
        if not notify_content:
            return

        # 保存上下文信息（用于管理员回复时引用）
        context = {
            "user_id": user_id,
            "user_nickname": getattr(event.sender, "nickname", "") or f"QQ{user_id}",
            "original_content": notify_content,
        }
        if isinstance(event, GroupMessageEvent):
            context["group_id"] = event.group_id
            context["group_name"] = getattr(event, "group_name", "") or f"群{event.group_id}"

        # 加入队列，生成唯一ID
        notify_id = _add_notify_to_queue(context)

        # 构建带引用的消息（末尾加通知ID，方便管理员回复指定）
        forward_msg = _build_quote_message(event, notify_content)
        forward_msg.append(MessageSegment.text(f"\n\n📋 通知ID：{notify_id}"))

        # 发送给所有管理员
        success_count = 0
        for admin_id in superusers:
            try:
                await bot.send_private_msg(
                    user_id=int(admin_id),
                    message=forward_msg,
                )
                success_count += 1
            except Exception:
                pass

        if success_count > 0:
            await notify_handler.finish(
                f"✅ 已将你的消息转达给管理员，管理员稍后会回复你哦~\n"
                f"📝 通知内容：{notify_content}"
            )
        else:
            await notify_handler.finish("📝 你的消息已收到，管理员会尽快处理。")
    except Exception:
        pass


# ============================================================
# 指令：管理员回复
# ============================================================
admin_reply_cmd = on_command(
    "回复",
    aliases={"reply", "答复"},
    priority=5,
    block=True,
    permission=lambda bot, event: event.get_user_id() in config_manager.superusers,
)


@admin_reply_cmd.handle()
async def handle_admin_reply(bot: Bot, event: MessageEvent):
    if not _is_enabled():
        return

    admin_id = event.get_user_id()
    try:
        superusers = config_manager.superusers
    except Exception:
        superusers = []

    if admin_id not in superusers:
        return

    raw_msg = event.message
    if not raw_msg:
        await admin_reply_cmd.finish("❓ 用法：回复 <内容>\n      回复 <通知ID> <内容>")
        return

    # 解析回复内容
    cmd_text = ""
    for seg in raw_msg:
        if seg.type == "text" and str(seg.data.get("text", "")).strip():
            cmd_text = str(seg.data.get("text", "")).strip()
            break

    # 格式：回复 [ID] 内容  或  回复 内容（自动最新）
    text_parts = cmd_text.split(maxsplit=2)
    # text_parts[0] = 回复
    # text_parts[1] = 可能是通知ID，也可能是内容
    # text_parts[2] = 如果[1]是ID，这里是内容

    target_nid = None
    reply_content = ""

    if len(text_parts) >= 2:
        maybe_id = text_parts[1]
        if maybe_id.isdigit():
            target_nid = int(maybe_id)
            if len(text_parts) >= 3:
                reply_content = text_parts[2]
        else:
            reply_content = text_parts[1]
            if len(text_parts) >= 3:
                reply_content += " " + text_parts[2]

    # 追加后续消息段（图片等）
    full_reply = Message()
    if reply_content.strip():
        full_reply.append(MessageSegment.text(reply_content.strip()))
    for seg in list(raw_msg)[1:]:
        full_reply.append(seg)

    final_reply_text = str(full_reply).strip()
    if not final_reply_text:
        await admin_reply_cmd.finish("❓ 用法：回复 <内容>\n      回复 <通知ID> <内容>")
        return

    # 查找目标通知
    target = _find_pending_notify(target_nid)

    if not target:
        queue = _load_reply_queue()
        pending = [q for q in queue if not q.get("handled")]
        if pending:
            msg = "❓ 没有找到指定通知ID。当前待回复通知ID："
            msg += "、".join(str(q.get("notify_id", "?")) for q in pending[-5:])
            await admin_reply_cmd.finish(msg)
        else:
            await admin_reply_cmd.finish("❓ 没有待回复の通知。用户发「通知管理员」后才能回复。")
        return

    # 构建引用回复消息
    reply_msg = _build_reply_quote_message(event, target, final_reply_text)

    # 转发给原用户
    user_id = target.get("user_id")
    nid = target.get("notify_id")
    try:
        if target.get("group_id"):
            await bot.send_group_msg(
                group_id=int(target["group_id"]),
                message=reply_msg,
            )
        else:
            await bot.send_private_msg(
                user_id=int(user_id),
                message=reply_msg,
            )

        # 标记为已回复（任何管理员回复都标记，不区分admin_id）
        _mark_notify_handled(nid, admin_id, final_reply_text)

        await admin_reply_cmd.finish(
            f"✅ 已回复 {target.get('user_nickname', '用户')}\n"
            f"📋 通知ID：{nid}"
        )
    except FinishedException:
        raise
    except Exception as e:
        await admin_reply_cmd.finish(f"❌ 回复失败：{e}")


# ============================================================
# 指令：查看待回复列表
# ============================================================
pending_cmd = on_command(
    "待回复",
    aliases={"待处理", "pending"},
    priority=5,
    block=True,
    permission=lambda bot, event: event.get_user_id() in config_manager.superusers,
)


@pending_cmd.handle()
async def handle_pending(bot: Bot, event: MessageEvent):
    if not _is_enabled():
        return

    admin_id = event.get_user_id()
    queue = _load_reply_queue()
    pending = [q for q in queue if not q.get("handled")]

    if not pending:
        await pending_cmd.finish("📋 当前没有待回复的通知。")
        return

    lines = [f"📋 待回复通知（共 {len(pending)} 条）", "━━━━━━━━━━━━"]
    for i, item in enumerate(reversed(pending)):
        nid = item.get("notify_id", "?")
        uid = item.get("user_id", "?")
        uname = item.get("user_nickname", "?")
        content = item.get("original_content", "")[:50]
        group_info = f" | 群{item.get('group_id', '')}" if item.get("group_id") else ""
        lines.append(f"[ID{nid}] {uname}({uid}){group_info}")
        lines.append(f"    {content}{'...' if len(item.get('original_content', '')) > 50 else ''}")
    lines += ["━━━━━━━━━━━━", "用法：回复 <内容> 自动回复最新一条", "            回复 <通知ID> <内容> 指定回复某条"]

    await pending_cmd.finish("\n".join(lines))


# ============================================================
# 菜单注册
# ============================================================
register_plugin_info(
    "miku_notify_admin",
    name="通知管理员",
    icon="📨",
    order=14,
    description="用户发通知管理员关键词，以引用回复形式转发",
    commands=["通知管理员", "回复", "待回复"],
    usage="""【用户端】
通知管理员 <内容>
告诉管理员 <内容>
对管理员说 <内容>
→ Bot 以引用框格式转发给管理员

【管理员端】
回复 <内容>
→ 自动回复最近一条未处理通知，带引用框
待回复
→ 查看所有未处理通知列表""",
)
