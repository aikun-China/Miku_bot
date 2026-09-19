"""
MikuBot 黑名单拦截插件
========================
高优先级消息拦截（priority=1，block=True），支持两种拦截模式：

1. 用户/群组黑名单（静默丢弃）：
   - user_disabled: {qq号: []}        → 该用户的所有消息被静默忽略
   - group_disabled: {群号: []}        → 该群的所有消息被静默忽略
   - global_disabled: []               → 若包含 "__all__"，所有非超级用户消息被静默忽略

2. 插件级禁用（按消息意图拦截）：
   - global_disabled: ["miku_ai", ...]  → 禁用指定插件的命令
   - group_disabled: {群号: ["miku_ai"]} → 群级别禁用指定插件
   - user_disabled: {qq号: ["miku_ai"]}  → 用户级别禁用指定插件

被静默忽略时 Bot 不回复任何消息，不打印日志。
"""

import json
import sys
from pathlib import Path

from typing import Optional
from nonebot import on_message, logger
from nonebot.rule import Rule
from nonebot.adapters.onebot.v11 import (
    Bot, MessageEvent, GroupMessageEvent, PrivateMessageEvent,
)
from nonebot.exception import FinishedException
from nonebot.plugin import PluginMetadata

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from utils.config_manager import config_manager

try:
    from plugins.miku_menu import register_plugin_info
except ImportError:
    register_plugin_info = lambda *args, **kwargs: None


__plugin_meta__ = PluginMetadata(
    name="Miku黑名单拦截",
    description="消息黑名单拦截与插件启用状态拦截",
    usage="由管理员在 WebUI 管理，无需用户操作",
    type="application",
    supported_adapters={"~onebot.v11"},
)


# ============================================================
# 配置
# ============================================================
_BLACKLIST_TEMPLATE = (
    "\n"
    "miku_blacklist:\n"
    "  # 是否启用黑名单拦截功能\n"
    "  enabled: true\n"
)

_blacklist_cfg = config_manager.register_plugin(
    "miku_blacklist",
    defaults={"enabled": True},
    template_str=_BLACKLIST_TEMPLATE,
    description="黑名单拦截插件配置",
)

# 本插件是否启用（每次调用时动态读取，支持 WebUI 热更新）
def _is_enabled() -> bool:
    raw = config_manager.get("miku_blacklist", "enabled", True)
    return str(raw).strip().lower() not in ("false", "0", "no", "")

BASE_DIR = Path(__file__).resolve().parent.parent
BLACKLIST_FILE = BASE_DIR / "data" / "blacklist.json"


# ============================================================
# 插件 → 关键词/命令映射
# 用于识别"这条消息意图调用哪个插件"
# 注：miku_ai 的 AI 对话由它自己的 _should_reply 规则判定，
#     但我们在这里也能通过关键词做一层兜底拦截。
# ============================================================
PLUGIN_KEYWORDS = {
    # 管理员
    "miku_admin": [
        "重启", "restart", "reload",
        "配置检查", "检查配置", "checkconfig",
        "刷新配置", "reload_config", "reloadcfg",
        "检查更新", "checkupdate", "updatecheck",
        "立即更新", "update", "upgrade",
    ],
    # 基本信息
    "miku_basic": [
        "信息", "ping", "状态", "info",
        "bot信息", "机器人信息",
    ],
    # 天气
    "miku_weather": [
        "天气", "weather", "tianqi",
    ],
    # 签到
    "miku_checkin": [
        "签到", "checkin", "sign", "signin",
        "每日签到", "qiandao",
    ],
    # 个人资料
    "miku_profile": [
        "资料", "个人资料", "profile", "我的资料", "我的信息", "查看资料",
    ],
    # AI 对话
    "miku_ai": [
        "清空AI记录", "清空AI", "清除AI记录", "重置AI",
        "AI状态", "ai状态", "AI配置", "ai配置",
    ],
    # 菜单（覆盖 menu_cmd 与 detail_cmd 所有别名）
    "miku_menu": [
        "菜单", "menu", "功能列表", "插件列表",
        "说明", "帮助", "帮助菜单", "功能菜单",
        "功能", "指令列表", "help", "功能表",
        "详细帮助", "详情", "详细功能",
    ],
    # 通知管理员
    "miku_notify_admin": [
        "通知管理员", "对管理员说", "告诉管理员",
        "跟管理员说", "转告管理员", "有事找管理员",
    ],
    # 缓存清理（管理型，无用户命令）
    # miku_blacklist：自身无用户命令
}


def _load_blacklist() -> dict:
    """加载黑名单数据"""
    if not BLACKLIST_FILE.exists():
        return {"global_disabled": [], "group_disabled": {}, "user_disabled": {}}
    try:
        with open(BLACKLIST_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"global_disabled": [], "group_disabled": {}, "user_disabled": {}}


def _extract_text(event: MessageEvent) -> str:
    """从消息事件中提取纯文本内容（去掉首尾空白）"""
    try:
        msg = event.get_message()
        # 收集所有 text 段
        texts = []
        for seg in msg:
            if seg.type == "text" and seg.data.get("text"):
                texts.append(seg.data["text"])
        return "".join(texts).strip()
    except Exception:
        return ""


def _get_sender_name(event: MessageEvent) -> str:
    """获取发信人的昵称"""
    try:
        sender = event.sender
        if sender and sender.nickname:
            return sender.nickname
    except Exception:
        pass
    return "用户"


def _match_disabled_plugin(text: str, disabled: dict) -> Optional[str]:
    """
    根据消息文本判断它想调用哪个被禁用的插件。
    返回插件名，未匹配返回 None。
    匹配规则：大小写不敏感，中英文关键词都覆盖。
    """
    if not text or not disabled:
        return None

    # 统一转小写做前缀匹配（中文不区分大小写，英文统一小写）
    stripped = text.lstrip("/# ")
    stripped_lower = stripped.lower()

    for plugin_name, keywords in PLUGIN_KEYWORDS.items():
        if plugin_name not in disabled:
            continue
        for kw in keywords:
            if not kw:
                continue
            if stripped_lower.startswith(kw.lower()):
                return plugin_name

    # 特别处理 miku_ai（AI 对话）：普通聊天文本也属于它
    if "miku_ai" in disabled:
        is_plain_chat = True
        for plugin_name, keywords in PLUGIN_KEYWORDS.items():
            if plugin_name == "miku_ai":
                continue
            for kw in keywords:
                if not kw:
                    continue
                if stripped_lower.startswith(kw.lower()):
                    is_plain_chat = False
                    break
            if not is_plain_chat:
                break
        if is_plain_chat:
            return "miku_ai"

    return None


# ============================================================
# Rule 判定与处理
# ============================================================
def _match_plugin_by_name(text: str, plugin_list: list) -> Optional[str]:
    """
    检查消息意图的插件是否在给定的禁用插件列表里。
    返回命中的插件名，未命中返回 None。
    """
    if not text or not plugin_list:
        return None
    # 特殊标记：__all__ 表示完全禁用 Bot
    if "__all__" in plugin_list:
        return "__all__"
    # 识别消息意图的插件，再检查是否在禁用列表
    disabled_dict = {name: True for name in plugin_list}
    return _match_disabled_plugin(text, disabled_dict)


def _check_blacklist(event: MessageEvent) -> bool:
    """
    Rule：返回 True 表示需要静默忽略 / 拦截消息。
    
    优先级（先命中先处理）：
    1) 用户在 user_disabled 中（含空列表）→ 静默忽略所有消息
    2) 群组在 group_disabled 中（含空列表）→ 静默忽略所有消息
    3) global_disabled 含 "__all__" → 静默忽略所有非超级用户消息
    4) 插件级禁用（global/group/user_disabled 中的插件名）→ 拦截该插件命令
    """
    # 1. 本插件开关
    if not _is_enabled():
        return False

    user_id = str(event.get_user_id())
    bl = _load_blacklist()

    # ── 静默忽略层（用户/群组黑名单）────────────────────────
    # 用户在黑名单中（任意配置，即使是空列表也意味着完全禁用该用户）
    user_entry = bl.get("user_disabled", {}).get(user_id)
    if user_entry is not None:
        # 空列表 [] = 完全禁用该用户；其他 = 仅禁用指定插件
        if isinstance(user_entry, list) and len(user_entry) == 0:
            return True  # 静默忽略
        # 非空列表 = 插件级禁用，后面统一处理
    # 群组在黑名单中
    if isinstance(event, GroupMessageEvent):
        group_id = str(event.group_id)
        group_entry = bl.get("group_disabled", {}).get(group_id)
        if group_entry is not None:
            # 空列表 [] = 完全禁用该群；含 __all__ = 完全禁用该群
            if isinstance(group_entry, list):
                if len(group_entry) == 0 or "__all__" in group_entry:
                    return True  # 静默忽略
    # __all__ 完全禁用（特殊关键词，标记并拦截所有消息）
    global_list = bl.get("global_disabled", [])
    if "__all__" in global_list:
        if user_id not in config_manager.superusers:
            event.__dict__["_blocked_plugin"] = "__all__"
            return True

    # ── 插件级禁用层 ────────────────────────────────────────
    text = _extract_text(event)
    if not text:
        return False

    # 从各禁用列表中提取插件名（排除 __all__）
    def _get_disabled_plugins() -> list:
        plugins = [p for p in global_list if p != "__all__"]
        if isinstance(event, GroupMessageEvent):
            group_plugins = bl.get("group_disabled", {}).get(str(event.group_id), [])
            if isinstance(group_plugins, list):
                plugins.extend(p for p in group_plugins if p != "__all__")
        if user_entry is not None and isinstance(user_entry, list):
            plugins.extend(p for p in user_entry if p != "__all__")
        return plugins

    disabled_plugins = _get_disabled_plugins()
    if not disabled_plugins:
        return False

    # 匹配消息意图的插件
    matched = _match_plugin_by_name(text, disabled_plugins)
    if matched:
        logger.debug(f"[黑名单] 命中禁用插件：{matched}, 消息='{text[:50]}'")
        event.__dict__["_blocked_plugin"] = matched
        return True

    return False


block_handler = on_message(priority=1, block=True, rule=Rule(_check_blacklist))


@block_handler.handle()
async def handle_block(bot: Bot, event: MessageEvent):
    """
    命中拦截规则 → 静默丢弃。

    - 用户/群组黑名单：完全不记录日志，直接静默忽略
    - 插件级禁用：记录 debug 日志，静默丢弃
    - 超级用户不受任何黑名单限制
    """
    # 超级用户永远不受限制
    user_id = str(event.get_user_id())
    if user_id in config_manager.superusers:
        return

    # 命中原因：插件级禁用才设置 _blocked_plugin
    matched_plugin = event.__dict__.get("_blocked_plugin")

    if matched_plugin:
        # 插件级禁用：记录 debug 日志，静默丢弃
        logger.debug(f"[黑名单] 拦截插件 {matched_plugin} 的消息: {event.get_user_id()}")
    # else: 用户/群组黑名单，直接静默忽略，不打印任何日志

    raise FinishedException()


# ─── 注册到菜单 ───
register_plugin_info(
    "miku_blacklist",
    name="黑名单",
    icon="🚫",
    order=93,
    description="管理用户/群组/插件的黑名单，由管理员在 WebUI 配置",
    commands=[],
    usage="""无用户指令，由管理员在 WebUI 管理页面配置黑名单策略。""",
)
