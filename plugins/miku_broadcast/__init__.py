"""
Miku 广播插件
================
- 群聊广播：发送「广播 内容」，Bot 自动转发到所有加入的群聊
- 好友广播：发送「好友广播 内容」，Bot 自动转发给所有好友
- 综合广播：发送「全体广播 内容」，同时广播到所有群聊和好友
- 支持指定群聊/好友：广播 [群号1,群号2] 内容 或 广播 123456 内容
- 支持图片、文本、@ 等混合内容
- 广播后给管理员反馈：目标 X 个，已广播 Y 个

指令：
  广播 <内容>               — 广播到所有群
  广播 [群号1,群号2] <内容>  — 广播到指定群
  广播 全部 <内容>           — 广播到所有群（显式）
  好友广播 <内容>            — 广播给所有好友
  好友广播 [QQ1,QQ2] <内容>  — 广播给指定好友
  全体广播 <内容>            — 同时广播到所有群和所有好友
  群列表                    — 查看 Bot 加入的所有群
  好友列表                  — 查看 Bot 的所有好友
"""

from nonebot import on_command, on_message
from nonebot.adapters.onebot.v11 import Bot, MessageEvent, GroupMessageEvent, PrivateMessageEvent
from nonebot.adapters.onebot.v11.message import MessageSegment, Message
from nonebot.permission import SUPERUSER
from nonebot.params import CommandArg, EventPlainText
from nonebot.rule import Rule
from nonebot.log import logger
from nonebot.plugin import PluginMetadata

from pathlib import Path
from datetime import datetime, timedelta
import json

from utils.config_manager import config_manager

__plugin_meta__ = PluginMetadata(
    name="Miku广播",
    description="管理员广播消息到所有群聊/好友",
    usage="广播 <内容> / 好友广播 <内容> / 全体广播 <内容>",
    type="application",
    supported_adapters={"~onebot.v11"},
)

__plugin_name__ = "miku_broadcast"
__plugin_describe__ = "群聊/好友广播"

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

BROADCAST_LOG_FILE = PROJECT_ROOT / "data" / "broadcast_log.json"
BROADCAST_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

# ============================================================
# 插件配置
# ============================================================
_TEMPLATE = (
    "\n"
    "miku_broadcast:\n"
    "  # 是否启用广播插件\n"
    "  enabled: true\n"
    "  # 广播冷却时间（秒），防止频繁广播\n"
    "  cooldown_seconds: 60\n"
    "  # 是否在广播内容前加前缀\n"
    "  prefix_enabled: true\n"
    "  prefix_text: \"📢 广播通知\"\n"
    "  # 管理员指令触发词列表\n"
    "  admin_commands:\n"
    "  - 广播\n"
)

_cfg = config_manager.register_plugin(
    "miku_broadcast",
    defaults={
        "enabled": True,
        "cooldown_seconds": 60,
        "prefix_enabled": True,
        "prefix_text": "📢 广播通知",
        "admin_commands": ["广播", "群列表"],
    },
    template_str=_TEMPLATE,
    description="广播插件配置",
)


def _is_enabled() -> bool:
    raw = config_manager.get("miku_broadcast", "enabled", True)
    return str(raw).strip().lower() not in ("false", "0", "no", "")


def _conf(key: str, default=None):
    return config_manager.get("miku_broadcast", key, default)


# ============================================================
# 冷却时间管理
# ============================================================
def _get_last_broadcast_time() -> datetime | None:
    if BROADCAST_LOG_FILE.exists():
        try:
            data = json.loads(BROADCAST_LOG_FILE.read_text(encoding="utf-8"))
            ts = data.get("last_broadcast")
            if ts:
                return datetime.fromisoformat(ts)
        except Exception:
            pass
    return None


def _set_last_broadcast_time(t: datetime):
    data = {}
    if BROADCAST_LOG_FILE.exists():
        try:
            data = json.loads(BROADCAST_LOG_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    data["last_broadcast"] = t.isoformat()
    BROADCAST_LOG_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ============================================================
# 辅助：解析群号列表
# ============================================================
def _parse_group_list(text: str) -> list | None:
    """
    解析广播命令中的群号列表
    返回 None 表示不指定（全部群）
    返回 [] 表示格式错误
    返回 [123456] 表示指定单个或多个群
    """
    text = text.strip()
    # 全部
    if text in ("全部", "all", "*", "所有"):
        return None  # None = 全部群
    # [123, 456] 格式
    if text.startswith("["):
        end = text.find("]")
        if end > 0:
            inner = text[1:end]
            try:
                groups = [int(x.strip()) for x in inner.split(",") if x.strip().isdigit()]
                return groups if groups else []
            except ValueError:
                return []
    # 单个群号：纯数字
    if text.isdigit():
        return [int(text)]
    # 其他（普通内容，不是指定群号）
    return None


# ============================================================
# 辅助：获取 Bot 加入的所有群
# ============================================================
async def _get_all_groups(bot: Bot) -> list:
    try:
        groups = await bot.get_group_list()
        return sorted(groups, key=lambda g: g.get("group_id", 0))
    except Exception as e:
        logger.error(f"[广播] 获取群列表失败: {e}")
        return []


# ============================================================
# 辅助：获取 Bot 的所有好友
# ============================================================
async def _get_all_friends(bot: Bot) -> list:
    try:
        friends = await bot.get_friend_list()
        return sorted(friends, key=lambda f: f.get("user_id", 0))
    except Exception as e:
        logger.error(f"[广播] 获取好友列表失败: {e}")
        return []


# ============================================================
# 辅助：构造带前缀的广播消息
# ============================================================
def _build_broadcast_message(message: Message) -> Message:
    """构造广播消息（按配置添加前缀）"""
    prefix_enabled = str(_conf("prefix_enabled", True)).strip().lower() not in ("false", "0", "no")
    prefix_text = str(_conf("prefix_text", "📢 广播通知"))
    msg = Message()
    if prefix_enabled and prefix_text:
        msg.append(MessageSegment.text(prefix_text + "\n"))
    msg.extend(message)
    return msg


# ============================================================
# 辅助：发送广播消息到指定群
# ============================================================
async def _broadcast_to_groups(
    bot: Bot,
    groups: list,
    message: Message,
    user_id: int,
) -> tuple[int, int]:
    """
    向指定群列表发送广播消息
    返回 (成功数, 失败数)
    """
    success = 0
    failed = 0
    send_msg = _build_broadcast_message(message)

    for group in groups:
        group_id = group.get("group_id")
        if not group_id:
            continue

        try:
            await bot.send_group_msg(group_id=group_id, message=send_msg)
            success += 1
            logger.info(f"[广播] 发送到群 {group_id} 成功")
        except Exception as e:
            failed += 1
            logger.error(f"[广播] 发送到群 {group_id} 失败: {e}")

    return success, failed


# ============================================================
# 辅助：发送广播消息到指定好友
# ============================================================
async def _broadcast_to_friends(
    bot: Bot,
    friends: list,
    message: Message,
    user_id: int,
) -> tuple[int, int]:
    """
    向指定好友列表发送广播消息
    返回 (成功数, 失败数)
    """
    success = 0
    failed = 0
    send_msg = _build_broadcast_message(message)

    for friend in friends:
        friend_id = friend.get("user_id")
        if not friend_id:
            continue

        try:
            await bot.send_private_msg(user_id=friend_id, message=send_msg)
            success += 1
            logger.info(f"[广播] 发送给好友 {friend_id} 成功")
        except Exception as e:
            failed += 1
            logger.error(f"[广播] 发送给好友 {friend_id} 失败: {e}")

    return success, failed


# ============================================================
# 辅助：从命令消息中解析目标ID列表和广播内容
# ============================================================
def _extract_broadcast_content(raw_msg: Message, id_parser=_parse_group_list):
    """
    从原始消息中提取：目标ID列表（可能为None表示全部）+ 广播内容Message
    id_parser: 解析目标ID的函数（_parse_group_list 或类似的QQ号解析函数）
    返回 (target_list, broadcast_msg) 或 (None, None) 表示无内容
    """
    if not raw_msg:
        return None, None

    # 找第一个文本段
    cmd_text = ""
    content_start_idx = 0
    for i, seg in enumerate(raw_msg):
        if seg.type == "text" and str(seg.data.get("text", "")).strip():
            cmd_text = str(seg.data.get("text", "")).strip()
            content_start_idx = i
            break

    # 拆分：命令词 + 目标 + 文本内容
    text_parts = cmd_text.split(maxsplit=2)
    target_part = ""
    first_text_content = ""
    if len(text_parts) >= 2:
        target_part = text_parts[1]
    if len(text_parts) >= 3:
        first_text_content = text_parts[2]

    target_list = id_parser(target_part)

    # 如果第一个参数不是目标（返回 None 表示全部），
    # 那它其实是内容的一部分
    if target_list is None and target_part and target_part not in ("全部", "all", "*", "所有"):
        first_text_content = target_part + (" " + first_text_content if first_text_content else "")
        target_part = ""

    # 构建广播内容
    broadcast_msg = Message()
    if first_text_content.strip():
        broadcast_msg.append(MessageSegment.text(first_text_content.strip() + " "))
    for seg in list(raw_msg)[content_start_idx + 1:]:
        broadcast_msg.append(seg)

    if not broadcast_msg or not str(broadcast_msg).strip():
        return None, None

    return target_list, broadcast_msg


# ============================================================
# 辅助：解析QQ号列表（用于好友广播）
# ============================================================
def _parse_friend_list(text: str) -> list | None:
    """
    解析广播命令中的QQ号列表
    返回 None 表示不指定（全部好友）
    返回 [] 表示格式错误
    返回 [123456] 表示指定单个或多个好友
    """
    text = text.strip()
    if text in ("全部", "all", "*", "所有"):
        return None
    if text.startswith("["):
        end = text.find("]")
        if end > 0:
            inner = text[1:end]
            try:
                ids = [int(x.strip()) for x in inner.split(",") if x.strip().isdigit()]
                return ids if ids else []
            except ValueError:
                return []
    if text.isdigit():
        return [int(text)]
    return None


# ============================================================
# 辅助：冷却检查
# ============================================================
def _check_cooldown() -> int:
    """返回剩余冷却秒数，0表示可以广播"""
    cooldown = int(_conf("cooldown_seconds", 60))
    if cooldown <= 0:
        return 0
    last = _get_last_broadcast_time()
    if last and (datetime.now() - last).total_seconds() < cooldown:
        return int(cooldown - (datetime.now() - last).total_seconds())
    return 0


# ============================================================
# 指令：广播（群聊广播）
# ============================================================
broadcast_cmd = on_command(
    "广播",
    aliases={"群发", "通知", "broadcast", "群聊广播"},
    priority=5,
    block=True,
    permission=SUPERUSER,
)


@broadcast_cmd.handle()
async def handle_broadcast(bot: Bot, event: MessageEvent):
    if not _is_enabled():
        return

    group_list, broadcast_msg = _extract_broadcast_content(event.message, _parse_group_list)
    if broadcast_msg is None:
        await broadcast_cmd.finish("❓ 用法：广播 <内容>\n      广播 [群号1,群号2] <内容>")
        return

    # 冷却检查
    remaining_cd = _check_cooldown()
    if remaining_cd > 0:
        await broadcast_cmd.finish(f"⏱️ 广播冷却中，请 {remaining_cd} 秒后再试")
        return

    # 获取所有群
    all_groups = await _get_all_groups(bot)
    if not all_groups:
        await broadcast_cmd.finish("❌ Bot 还没有加入任何群聊")
        return

    # 确定目标群列表
    if group_list is None:
        target_groups = all_groups
        target_desc = f"全部群（{len(target_groups)} 个）"
    else:
        if not group_list:
            await broadcast_cmd.finish("❓ 群号格式错误，如：广播 [123456, 789012] 内容")
            return
        target_groups = [g for g in all_groups if g.get("group_id") in group_list]
        missed = set(group_list) - {g.get("group_id") for g in target_groups}
        target_desc = f"{len(target_groups)} 个群（指定 {len(group_list)} 个）"
        if missed:
            target_desc += f"，{len(missed)} 个未找到/未加入"

    if not target_groups:
        await broadcast_cmd.finish("❌ 没有找到目标群聊")
        return

    _set_last_broadcast_time(datetime.now())
    success, failed = await _broadcast_to_groups(bot, target_groups, broadcast_msg, event.user_id)

    total = len(target_groups)
    lines = [
        "✅ 群聊广播完成",
        "━━━━━━━━━━━━",
        f"📋 目标群聊：{total} 个",
        f"✅ 已广播：{success} 个",
    ]
    if failed > 0:
        lines.append(f"❌ 失败：{failed} 个")
    lines.append("━━━━━━━━━━━━")
    await broadcast_cmd.finish("\n".join(lines))


# ============================================================
# 指令：好友广播
# ============================================================
friend_broadcast_cmd = on_command(
    "好友广播",
    aliases={"私信广播", "私聊广播", "friend_broadcast"},
    priority=5,
    block=True,
    permission=SUPERUSER,
)


@friend_broadcast_cmd.handle()
async def handle_friend_broadcast(bot: Bot, event: MessageEvent):
    if not _is_enabled():
        return

    friend_list, broadcast_msg = _extract_broadcast_content(event.message, _parse_friend_list)
    if broadcast_msg is None:
        await friend_broadcast_cmd.finish("❓ 用法：好友广播 <内容>\n      好友广播 [QQ1,QQ2] <内容>")
        return

    remaining_cd = _check_cooldown()
    if remaining_cd > 0:
        await friend_broadcast_cmd.finish(f"⏱️ 广播冷却中，请 {remaining_cd} 秒后再试")
        return

    all_friends = await _get_all_friends(bot)
    if not all_friends:
        await friend_broadcast_cmd.finish("❌ Bot 还没有任何好友")
        return

    if friend_list is None:
        target_friends = all_friends
        target_desc = f"全部好友（{len(target_friends)} 个）"
    else:
        if not friend_list:
            await friend_broadcast_cmd.finish("❓ QQ号格式错误，如：好友广播 [123456, 789012] 内容")
            return
        target_friends = [f for f in all_friends if f.get("user_id") in friend_list]
        missed = set(friend_list) - {f.get("user_id") for f in target_friends}
        target_desc = f"{len(target_friends)} 个好友（指定 {len(friend_list)} 个）"
        if missed:
            target_desc += f"，{len(missed)} 个未找到"

    if not target_friends:
        await friend_broadcast_cmd.finish("❌ 没有找到目标好友")
        return

    _set_last_broadcast_time(datetime.now())
    success, failed = await _broadcast_to_friends(bot, target_friends, broadcast_msg, event.user_id)

    total = len(target_friends)
    lines = [
        "✅ 好友广播完成",
        "━━━━━━━━━━━━",
        f"📋 目标好友：{total} 个",
        f"✅ 已广播：{success} 个",
    ]
    if failed > 0:
        lines.append(f"❌ 失败：{failed} 个")
    lines.append("━━━━━━━━━━━━")
    await friend_broadcast_cmd.finish("\n".join(lines))


# ============================================================
# 指令：全体广播（群聊 + 好友）
# ============================================================
all_broadcast_cmd = on_command(
    "全体广播",
    aliases={"全部广播", "全面广播", "全员广播", "broadcast_all"},
    priority=5,
    block=True,
    permission=SUPERUSER,
)


@all_broadcast_cmd.handle()
async def handle_all_broadcast(bot: Bot, event: MessageEvent):
    if not _is_enabled():
        return

    # 全体广播不支持指定目标，直接取内容
    _, broadcast_msg = _extract_broadcast_content(event.message, lambda x: None)
    if broadcast_msg is None:
        await all_broadcast_cmd.finish("❓ 用法：全体广播 <内容>")
        return

    remaining_cd = _check_cooldown()
    if remaining_cd > 0:
        await all_broadcast_cmd.finish(f"⏱️ 广播冷却中，请 {remaining_cd} 秒后再试")
        return

    all_groups = await _get_all_groups(bot)
    all_friends = await _get_all_friends(bot)
    total_targets = len(all_groups) + len(all_friends)
    if total_targets == 0:
        await all_broadcast_cmd.finish("❌ Bot 没有加入任何群聊，也没有任何好友")
        return

    _set_last_broadcast_time(datetime.now())

    g_success, g_failed = 0, 0
    f_success, f_failed = 0, 0
    if all_groups:
        g_success, g_failed = await _broadcast_to_groups(bot, all_groups, broadcast_msg, event.user_id)
    if all_friends:
        f_success, f_failed = await _broadcast_to_friends(bot, all_friends, broadcast_msg, event.user_id)

    total_success = g_success + f_success
    total_failed = g_failed + f_failed
    lines = [
        "✅ 全体广播完成",
        "━━━━━━━━━━━━",
        f"📋 目标群聊：{len(all_groups)} 个（成功 {g_success}，失败 {g_failed}）",
        f"📋 目标好友：{len(all_friends)} 个（成功 {f_success}，失败 {f_failed}）",
        f"✅ 总成功：{total_success} 个",
    ]
    if total_failed > 0:
        lines.append(f"❌ 总失败：{total_failed} 个")
    lines.append("━━━━━━━━━━━━")
    await all_broadcast_cmd.finish("\n".join(lines))


# ============================================================
# 指令：群列表
# ============================================================
grouplist_cmd = on_command(
    "群列表",
    aliases={"已加群", "我的群"},
    priority=5,
    block=True,
    permission=SUPERUSER,
)


@grouplist_cmd.handle()
async def handle_grouplist(bot: Bot, event: MessageEvent):
    if not _is_enabled():
        return

    groups = await _get_all_groups(bot)
    if not groups:
        await grouplist_cmd.finish("❌ Bot 还没有加入任何群聊")
        return

    lines = [f"📋 已加入的群聊（共 {len(groups)} 个）", "━━━━━━━━━━━━"]
    for g in groups:
        gid = g.get("group_id", "?")
        gname = g.get("group_name", "（群名未知）")
        lines.append(f"[{gid}] {gname}")
    lines += ["━━━━━━━━━━━━", "用法：广播 [群号1,群号2] <内容>"]

    await grouplist_cmd.finish("\n".join(lines))


# ============================================================
# 指令：好友列表
# ============================================================
friendlist_cmd = on_command(
    "好友列表",
    aliases={"我的好友", "好友们"},
    priority=5,
    block=True,
    permission=SUPERUSER,
)


@friendlist_cmd.handle()
async def handle_friendlist(bot: Bot, event: MessageEvent):
    if not _is_enabled():
        return

    friends = await _get_all_friends(bot)
    if not friends:
        await friendlist_cmd.finish("❌ Bot 还没有任何好友")
        return

    lines = [f"📋 好友列表（共 {len(friends)} 个）", "━━━━━━━━━━━━"]
    for f in friends:
        fid = f.get("user_id", "?")
        fname = f.get("nickname", "") or f.get("remark", "") or "（昵称未知）"
        lines.append(f"[{fid}] {fname}")
    lines += ["━━━━━━━━━━━━", "用法：好友广播 [QQ1,QQ2] <内容>"]

    await friendlist_cmd.finish("\n".join(lines))


# ============================================================
# 菜单注册
# ============================================================
try:
    from plugins.miku_menu import register_plugin_info
except ImportError:
    register_plugin_info = lambda *args, **kwargs: None

register_plugin_info(
    "miku_broadcast",
    name="广播管理",
    icon="📢",
    order=91,
    description="管理员向所有群聊/好友广播消息",
    commands=["广播", "好友广播", "全体广播", "群列表", "好友列表"],
    usage="""仅管理员可用：

【群聊广播】
广播 <内容>
广播到所有加入的群聊
广播 [群号1,群号2] <内容>
广播到指定群聊
如：广播 [123456, 789012] 你好

【好友广播】
好友广播 <内容>
广播给所有好友
好友广播 [QQ1,QQ2] <内容>
广播给指定好友
如：好友广播 [123456, 789012] 你好

【全体广播】
全体广播 <内容>
同时广播到所有群聊和所有好友

【列表查询】
群列表 — 查看 Bot 已加入的所有群
好友列表 — 查看 Bot 的所有好友

支持文本、图片、@ 等混合内容
广播后反馈目标数和成功数""",
)
