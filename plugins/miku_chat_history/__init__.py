"""
Miku 聊天记录插件
================
- 自动记录所有群聊和私聊消息
- 支持 WebUI 查看完整聊天历史
- 消息自动清理（可配置保留天数）
"""

from nonebot import on_message, get_driver, logger
from nonebot.adapters.onebot.v11 import (
    MessageEvent,
    GroupMessageEvent,
    PrivateMessageEvent,
    Message,
)
from nonebot.plugin import PluginMetadata
from nonebot.params import CommandArg

from pathlib import Path
import sys
import json
import time
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from utils.config_manager import config_manager

__plugin_meta__ = PluginMetadata(
    name="聊天记录",
    description="自动记录群聊和私聊消息",
    usage="自动运行，无需手动调用",
    type="application",
    supported_adapters={"~onebot.v11"},
)

__plugin_name__ = "miku_chat_history"
__plugin_describe__ = "聊天记录自动保存"

# ============================================================
# 配置注册
# ============================================================
_CHAT_HISTORY_TEMPLATE = (
    "\n"
    "miku_chat_history:\n"
    "  # 是否启用聊天记录功能\n"
    "  enabled: true\n"
    "  # 每个文件保存的最大消息条数（超出后自动截断旧消息）\n"
    "  max_messages_per_file: 500\n"
)

_cfg = config_manager.register_plugin(
    "miku_chat_history",
    defaults={
        "enabled": True,
        "max_messages_per_file": 500,
    },
    template_str=_CHAT_HISTORY_TEMPLATE,
    description="聊天记录插件配置",
)


def _is_enabled() -> bool:
    raw = config_manager.get("miku_chat_history", "enabled", True)
    return str(raw).strip().lower() not in ("false", "0", "no", "")


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = PROJECT_ROOT / "data" / "chat_history"
GROUP_DIR = DATA_DIR / "group"
PRIVATE_DIR = DATA_DIR / "private"

GROUP_DIR.mkdir(parents=True, exist_ok=True)
PRIVATE_DIR.mkdir(parents=True, exist_ok=True)

driver = get_driver()

MAX_MESSAGES_PER_FILE = int(config_manager.get("miku_chat_history", "max_messages_per_file", 500) or 500)
MAX_FILES_PER_DIR = 100


def _extract_text(msg: Message) -> str:
    """从消息中提取纯文本"""
    text_parts = []
    for seg in msg:
        if seg.type == "text":
            t = str(seg.data.get("text", ""))
            text_parts.append(t)
        elif seg.type == "image":
            text_parts.append("[图片]")
        elif seg.type == "at":
            qq = seg.data.get("qq", "")
            text_parts.append(f"@{qq}")
        elif seg.type == "face":
            text_parts.append("[表情]")
        elif seg.type == "record":
            text_parts.append("[语音]")
        elif seg.type == "video":
            text_parts.append("[视频]")
        elif seg.type == "file":
            text_parts.append("[文件]")
        else:
            text_parts.append(f"[{seg.type}]")
    return "".join(text_parts)


def _save_message(
    target_id: str,
    user_id: str,
    nickname: str,
    content: str,
    is_group: bool,
    message_id: Optional[int] = None,
):
    """保存消息到文件"""
    target_dir = GROUP_DIR if is_group else PRIVATE_DIR
    file_path = target_dir / f"{target_id}.json"

    messages = []
    if file_path.exists():
        try:
            messages = json.loads(file_path.read_text(encoding="utf-8"))
        except Exception:
            messages = []

    entry = {
        "role": "user",
        "user_id": str(user_id),
        "nickname": nickname,
        "content": content,
        "time": int(time.time()),
        "message_id": message_id,
    }

    messages.append(entry)

    if len(messages) > MAX_MESSAGES_PER_FILE:
        messages = messages[-MAX_MESSAGES_PER_FILE:]

    try:
        file_path.write_text(json.dumps(messages, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning(f"[chat_history] 保存消息失败: {e}")


msg_record = on_message(priority=99, block=False)


@msg_record.handle()
async def _record_message(event: MessageEvent):
    """记录所有消息"""
    if not _is_enabled():
        return
    try:
        user_id = str(event.user_id)
        nickname = ""

        if hasattr(event, "sender") and event.sender:
            nickname = event.sender.nickname or ""

        content = _extract_text(event.get_message())

        if not content.strip():
            return

        message_id = event.message_id if hasattr(event, "message_id") else None

        if isinstance(event, GroupMessageEvent):
            group_id = str(event.group_id)
            _save_message(
                target_id=group_id,
                user_id=user_id,
                nickname=nickname,
                content=content,
                is_group=True,
                message_id=message_id,
            )
        elif isinstance(event, PrivateMessageEvent):
            _save_message(
                target_id=user_id,
                user_id=user_id,
                nickname=nickname,
                content=content,
                is_group=False,
                message_id=message_id,
            )
    except Exception as e:
        logger.warning(f"[chat_history] 记录消息失败: {e}")