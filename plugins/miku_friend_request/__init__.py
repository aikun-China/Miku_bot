"""
Miku 好友/群请求管理插件
================
- 自动接收好友请求、加群请求、群邀请
- 管理员可查看请求列表（带序号）
- 管理员可按序号同意/拒绝
- 同意后默认开启免打扰（兼容主流 OneBot 实现）
- 群邀请会显示邀请者信息
- 群禁言检测：bot被禁言后通知超级用户，支持退群

指令：
  请求列表 / 请求
  同意 <序号>
  拒绝 <序号> [原因]
  全部同意
  全部拒绝
  退群 <群号>
"""

from nonebot import on_command, on_request, on_notice
from nonebot.adapters.onebot.v11 import (
    Bot,
    MessageEvent,
    FriendRequestEvent,
    GroupRequestEvent,
    PrivateMessageEvent,
    GroupMessageEvent,
    GroupBanNoticeEvent,
    NoticeEvent,
)
from nonebot.adapters.onebot.v11.message import MessageSegment, Message
from nonebot.permission import SUPERUSER
from nonebot.params import CommandArg
from nonebot.log import logger
from nonebot.plugin import PluginMetadata

from pathlib import Path
from datetime import datetime
import json

from utils.config_manager import config_manager

__plugin_meta__ = PluginMetadata(
    name="Miku请求管理",
    description="管理好友和群聊添加请求",
    usage="请求列表 / 同意 <序号> / 拒绝 <序号>",
    type="application",
    supported_adapters={"~onebot.v11"},
)

# 注意：已移除 __plugin_name__ 和 __plugin_describe__
# 避免与 NoneBot2 框架保留变量冲突

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

REQUEST_DATA_FILE = PROJECT_ROOT / "data" / "friend_requests.json"
REQUEST_DATA_FILE.parent.mkdir(parents=True, exist_ok=True)

# ============================================================
# 插件配置
# ============================================================
_TEMPLATE = (
    "\n"
    "miku_friend_request:\n"
    "  # 是否启用请求管理插件\n"
    "  enabled: true\n"
    "  # 同意后是否自动开启免打扰（群聊）\n"
    "  auto_dnd: true\n"
    "  # 请求自动过期时间（小时），0 表示不过期\n"
    "  expire_hours: 48\n"
    "  # 管理员指令触发词\n"
    "  admin_commands:\n"
    "  - 请求列表\n"
    "  - 同意\n"
    "  - 拒绝\n"
)

_cfg = config_manager.register_plugin(
    "miku_friend_request",
    defaults={
        "enabled": True,
        "auto_dnd": True,
        "expire_hours": 48,
        "admin_commands": ["请求列表", "同意", "拒绝"],
    },
    template_str=_TEMPLATE,
    description="请求管理插件配置",
)


def _is_enabled() -> bool:
    raw = config_manager.get("miku_friend_request", "enabled", True)
    return str(raw).strip().lower() not in ("false", "0", "no", "")


def _conf(key: str, default=None):
    return config_manager.get("miku_friend_request", key, default)


# ============================================================
# 请求数据持久化
# ============================================================
def _load_requests() -> dict:
    if REQUEST_DATA_FILE.exists():
        try:
            data = json.loads(REQUEST_DATA_FILE.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {"requests": [], "counter": 0}
        except Exception:
            pass
    return {"requests": [], "counter": 0}


def _save_requests(data: dict):
    REQUEST_DATA_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _add_request(req: dict):
    data = _load_requests()
    data["counter"] = data.get("counter", 0) + 1
    req["id"] = data["counter"]
    req["time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    req["handled"] = False
    data.setdefault("requests", []).append(req)
    _save_requests(data)
    return req["id"]


def _get_request_by_id(req_id: int) -> dict | None:
    data = _load_requests()
    for req in data.get("requests", []):
        if req.get("id") == req_id and not req.get("handled"):
            return req
    return None


def _mark_handled(req_id: int, action: str):
    data = _load_requests()
    for req in data.get("requests", []):
        if req.get("id") == req_id:
            req["handled"] = True
            req["action"] = action
            req["handle_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            break
    _save_requests(data)


def _get_pending_requests() -> list:
    data = _load_requests()
    expire_hours = int(_conf("expire_hours", 48))
    pending = []
    now = datetime.now()
    for req in data.get("requests", []):
        if req.get("handled"):
            continue
        # 过期检查
        if expire_hours > 0:
            try:
                req_time = datetime.strptime(req.get("time", ""), "%Y-%m-%d %H:%M:%S")
                if (now - req_time).total_seconds() > expire_hours * 3600:
                    req["handled"] = True
                    req["action"] = "expired"
                    continue
            except Exception:
                pass
        pending.append(req)
    # 保存过期标记
    _save_requests(data)
    return pending


# ============================================================
# 免打扰设置（兼容主流 OneBot 实现）
# ============================================================
async def _set_group_dnd(bot: Bot, group_id: int, dnd: bool = True):
    """尝试设置群消息免打扰，失败则静默跳过"""
    try:
        # NapCat / LLOneBot 风格
        await bot.call_api(
            "set_group_notify",
            group_id=group_id,
            is_notify=not dnd,
        )
        return True
    except Exception:
        pass
    try:
        # 另一种常见实现
        await bot.call_api(
            "set_group_setting",
            group_id=group_id,
            type_="notify",
            value="0" if dnd else "1",
        )
        return True
    except Exception:
        pass
    logger.debug(f"[请求管理] 群 {group_id} 免打扰设置失败（协议端不支持此接口）")
    return False


# ============================================================
# 事件监听：好友请求 + 群请求/邀请（统一处理）
# ============================================================
all_request = on_request(priority=5, block=False)


@all_request.handle()
async def handle_all_requests(bot: Bot, event):
    if not _is_enabled():
        return

    event_type = type(event).__name__
    logger.info(f"[请求管理] 收到请求事件: {event_type}")
    logger.debug(f"[请求管理] 事件详情: {dict(event) if hasattr(event, '__dict__') else str(event)}")

    # 好友请求
    if isinstance(event, FriendRequestEvent):
        await _handle_friend_request(bot, event)
    # 群请求/邀请
    elif isinstance(event, GroupRequestEvent):
        await _handle_group_request(bot, event)
    else:
        # 尝试通过属性判断（兼容不标准的协议端）
        if hasattr(event, "request_type"):
            req_type = event.request_type
            logger.info(f"[请求管理] 通过 request_type 识别: {req_type}")
            if req_type == "friend":
                try:
                    await _handle_friend_request(bot, event)
                except Exception as e:
                    logger.warning(f"[请求管理] 处理好友请求失败: {e}")
            elif req_type == "group":
                try:
                    await _handle_group_request(bot, event)
                except Exception as e:
                    logger.warning(f"[请求管理] 处理群请求失败: {e}")
        else:
            logger.warning(f"[请求管理] 无法识别的请求事件类型: {event_type}")
            # 尝试打印所有属性辅助调试
            attrs = {k: getattr(event, k, None) for k in dir(event) if not k.startswith('_')}
            logger.debug(f"[请求管理] 事件属性: {attrs}")


async def _handle_friend_request(bot: Bot, event):
    """处理好友请求"""
    user_id = getattr(event, "user_id", "")
    flag = getattr(event, "flag", "")
    comment = getattr(event, "comment", "") or ""

    if not user_id or not flag:
        logger.warning(f"[请求管理] 好友请求缺少必要字段: user_id={user_id}, flag={flag}")
        return

    req_id = _add_request({
        "type": "friend",
        "user_id": str(user_id),
        "flag": str(flag),
        "comment": str(comment),
    })

    logger.info(f"[请求管理] 收到好友请求: {user_id}, 请求ID: {req_id}")

    msg = (
        f"🔔 新的好友请求\n"
        f"━━━━━━━━━━━━\n"
        f"序号：{req_id}\n"
        f"类型：好友请求\n"
        f"QQ：{user_id}\n"
        f"验证信息：{comment or '（无）'}\n"
        f"━━━━━━━━━━━━\n"
        f"发送「同意 {req_id}」或「拒绝 {req_id}」处理"
    )
    for su in config_manager.superusers:
        try:
            await bot.send_private_msg(user_id=int(su), message=msg)
        except Exception:
            pass


async def _handle_group_request(bot: Bot, event):
    """处理群请求/邀请"""
    sub_type = getattr(event, "sub_type", "")
    user_id = getattr(event, "user_id", "")
    group_id = getattr(event, "group_id", "")
    flag = getattr(event, "flag", "")
    comment = getattr(event, "comment", "") or ""

    if not user_id or not group_id or not flag:
        logger.warning(
            f"[请求管理] 群请求缺少必要字段: "
            f"user_id={user_id}, group_id={group_id}, flag={flag}"
        )
        return

    type_label = "群邀请" if sub_type == "invite" else "加群请求"

    user_name = f"QQ{user_id}"
    try:
        if sub_type == "invite":
            user_info = await bot.get_stranger_info(user_id=int(user_id))
            user_name = user_info.get("nickname", user_name)
        else:
            try:
                member_info = await bot.get_group_member_info(
                    group_id=int(group_id), user_id=int(user_id)
                )
                user_name = member_info.get("card", "") or member_info.get("nickname", user_name)
            except Exception:
                pass
    except Exception:
        pass

    group_name = f"群{group_id}"
    try:
        group_info = await bot.get_group_info(group_id=int(group_id))
        group_name = group_info.get("group_name", group_name)
    except Exception:
        pass

    req_id = _add_request({
        "type": "group",
        "sub_type": sub_type,
        "user_id": str(user_id),
        "user_name": user_name,
        "group_id": str(group_id),
        "group_name": group_name,
        "flag": str(flag),
        "comment": str(comment),
    })

    logger.info(f"[请求管理] 收到{type_label}: {user_name}({user_id}) -> {group_name}({group_id}), 请求ID: {req_id}")

    msg_lines = [
        f"🔔 新的{type_label}",
        "━━━━━━━━━━━━",
        f"序号：{req_id}",
        f"类型：{type_label}",
        f"群号：{group_id}",
        f"群名：{group_name}",
    ]
    if sub_type == "invite":
        msg_lines.append(f"邀请者：{user_name} ({user_id})")
    else:
        msg_lines.append(f"申请人：{user_name} ({user_id})")
    if comment:
        msg_lines.append(f"验证信息：{comment}")
    msg_lines += [
        "━━━━━━━━━━━━",
        f"发送「同意 {req_id}」或「拒绝 {req_id}」处理",
    ]

    msg = "\n".join(msg_lines)
    if sub_type == "invite":
        # 群邀请：单聊通知超级用户（需要超级用户决定是否进群）
        for su in config_manager.superusers:
            try:
                await bot.send_private_msg(user_id=int(su), message=msg)
            except Exception:
                pass
    else:
        # 加群请求：群聊通知（让群管理员可见并处理）
        try:
            await bot.send_group_msg(group_id=int(group_id), message=msg)
        except Exception as e:
            logger.warning(f"[请求管理] 群 {group_id} 发送加群请求通知失败: {e}，回退到私聊超级用户")
            # 回退：发不到群就私聊超级用户
            for su in config_manager.superusers:
                try:
                    await bot.send_private_msg(user_id=int(su), message=msg)
                except Exception:
                    pass


# ============================================================
# 指令：请求列表
# ============================================================
list_cmd = on_command(
    "请求列表",
    aliases={"请求", "好友请求", "群请求", "申请列表"},
    priority=5,
    block=True,
    permission=SUPERUSER,
)


@list_cmd.handle()
async def handle_list(bot: Bot, event: MessageEvent):
    if not _is_enabled():
        return

    pending = _get_pending_requests()

    if not pending:
        await list_cmd.finish("✅ 当前没有待处理的请求")
        return

    lines = [f"📋 待处理请求（共 {len(pending)} 条）", "━━━━━━━━━━━━"]
    for req in pending:
        req_id = req.get("id", "?")
        req_time = req.get("time", "")
        if req.get("type") == "friend":
            lines.append(
                f"[{req_id}] 好友请求\n"
                f"    QQ：{req.get('user_id')}\n"
                f"    验证：{req.get('comment', '（无）')}\n"
                f"    时间：{req_time}"
            )
        else:
            sub_type = req.get("sub_type", "")
            type_label = "群邀请" if sub_type == "invite" else "加群请求"
            user_label = "邀请者" if sub_type == "invite" else "申请人"
            lines.append(
                f"[{req_id}] {type_label}\n"
                f"    群：{req.get('group_name', req.get('group_id'))}\n"
                f"    {user_label}：{req.get('user_name', req.get('user_id'))} ({req.get('user_id')})\n"
                f"    验证：{req.get('comment', '（无）')}\n"
                f"    时间：{req_time}"
            )
        lines.append("")

    lines += [
        "━━━━━━━━━━━━",
        "同意 <序号>  —  同意请求",
        "拒绝 <序号> [原因]  —  拒绝请求",
        "全部同意 / 全部拒绝",
    ]

    await list_cmd.finish("\n".join(lines))


# ============================================================
# 指令：同意
# ============================================================
approve_cmd = on_command(
    "同意",
    aliases={"通过", "accept", "approve"},
    priority=5,
    block=True,
)


def _check_permission(bot: Bot, event: MessageEvent, req: dict) -> tuple[bool, str]:
    """
    权限检查：
    - 私聊：必须是超级用户，可处理任何请求
    - 群聊：必须是该群的群主/管理员，且只能处理「该群的加群请求」（群邀请/好友请求仍需超级用户）
    返回：(是否通过, 错误消息)
    """
    user_id = str(event.user_id)
    is_su = user_id in [str(s) for s in config_manager.superusers]

    # 私聊：必须超级用户
    if isinstance(event, PrivateMessageEvent):
        if not is_su:
            return False, "❌ 仅超级用户可在私聊中处理请求"
        return True, ""

    # 群聊：群主/管理员权限检查（群邀请、好友请求仍需超级用户）
    if isinstance(event, GroupMessageEvent):
        req_type = req.get("type", "")
        sub_type = req.get("sub_type", "")
        req_group_id = str(req.get("group_id", ""))
        current_group_id = str(event.group_id)

        # 群邀请/好友请求：必须超级用户
        if req_type == "friend" or (req_type == "group" and sub_type == "invite"):
            if not is_su:
                return False, "❌ 群邀请/好友请求需超级用户私聊处理"
            return True, ""

        # 加群请求：仅允许该群的群主/管理员
        if req_type == "group" and sub_type != "invite":
            if req_group_id != current_group_id:
                return False, f"❌ 此请求属于其他群，请到对应群内处理"
            # 超级用户直接通过
            if is_su:
                return True, ""
            # 检查发送者是否为群管理员/群主
            sender_role = ""
            try:
                sender = getattr(event, "sender", None)
                if sender and hasattr(sender, "role"):
                    sender_role = sender.role or ""
            except Exception:
                pass
            if sender_role in ("owner", "admin"):
                return True, ""
            return False, "❌ 仅群主/管理员/超级用户可处理本群的加群请求"

    # 兜底：超级用户通过
    if is_su:
        return True, ""
    return False, "❌ 无权限处理此请求"


@approve_cmd.handle()
async def handle_approve(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    if not _is_enabled():
        return

    raw = args.extract_plain_text().strip()

    # 全部同意
    if raw in ("全部", "all", "*"):
        await _approve_all(bot, event)
        return

    # 按序号同意
    if not raw.isdigit():
        await approve_cmd.finish("❓ 用法：同意 <序号>，如「同意 1」")
        return

    req_id = int(raw)
    req = _get_request_by_id(req_id)
    if not req:
        await approve_cmd.finish(f"❌ 找不到序号为 {req_id} 的待处理请求")
        return

    # 权限检查
    ok, err = _check_permission(bot, event, req)
    if not ok:
        await approve_cmd.finish(err)
        return

    success_msg = None
    try:
        if req.get("type") == "friend":
            await bot.set_friend_add_request(
                flag=req["flag"], approve=True
            )
            _mark_handled(req_id, "approve")
            success_msg = f"✅ 已同意好友请求 [{req_id}]（QQ：{req.get('user_id')}）"
        else:
            await bot.set_group_add_request(
                flag=req["flag"],
                sub_type=req.get("sub_type", "add"),
                approve=True,
            )
            _mark_handled(req_id, "approve")

            # 自动免打扰（仅针对群邀请/加群，且是 bot 加入的群）
            if _conf("auto_dnd", True):
                # 等一下让群信息同步
                import asyncio
                await asyncio.sleep(2)
                await _set_group_dnd(bot, int(req.get("group_id", 0)), dnd=True)

            group_name = req.get("group_name", req.get("group_id"))
            success_msg = f"✅ 已同意群请求 [{req_id}]（群：{group_name}）"
    except Exception as e:
        logger.error(f"[请求管理] 同意请求失败: {e}")
        await approve_cmd.finish(f"❌ 同意失败：{e}")
        return

    await approve_cmd.finish(success_msg)


async def _approve_all(bot: Bot, event: MessageEvent):
    # 批量操作：仅超级用户可用
    user_id = str(event.user_id)
    if user_id not in [str(s) for s in config_manager.superusers]:
        await approve_cmd.finish("❌ 仅超级用户可执行「全部同意」")
        return

    pending = _get_pending_requests()
    if not pending:
        await approve_cmd.finish("✅ 当前没有待处理的请求")
        return

    success = 0
    failed = 0
    for req in pending:
        try:
            if req.get("type") == "friend":
                await bot.set_friend_add_request(flag=req["flag"], approve=True)
            else:
                await bot.set_group_add_request(
                    flag=req["flag"],
                    sub_type=req.get("sub_type", "add"),
                    approve=True,
                )
            _mark_handled(req["id"], "approve")
            success += 1
        except Exception as e:
            logger.error(f"[请求管理] 全部同意 - 序号 {req.get('id')} 失败: {e}")
            failed += 1

    msg = f"✅ 全部同意完成\n成功：{success} 条\n失败：{failed} 条"
    await approve_cmd.finish(msg)


# ============================================================
# 指令：拒绝
# ============================================================
reject_cmd = on_command(
    "拒绝",
    aliases={"不同意", "reject", "deny"},
    priority=5,
    block=True,
)


@reject_cmd.handle()
async def handle_reject(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    if not _is_enabled():
        return

    raw = args.extract_plain_text().strip()

    # 全部拒绝
    if raw in ("全部", "all", "*"):
        await _reject_all(bot, event)
        return

    # 解析：序号 [原因]
    parts = raw.split(maxsplit=1)
    if not parts or not parts[0].isdigit():
        await reject_cmd.finish("❓ 用法：拒绝 <序号> [原因]，如「拒绝 1 不太熟」")
        return

    req_id = int(parts[0])
    reason = parts[1] if len(parts) > 1 else ""

    req = _get_request_by_id(req_id)
    if not req:
        await reject_cmd.finish(f"❌ 找不到序号为 {req_id} 的待处理请求")
        return

    # 权限检查
    ok, err = _check_permission(bot, event, req)
    if not ok:
        await reject_cmd.finish(err)
        return

    success_msg = None
    try:
        if req.get("type") == "friend":
            await bot.set_friend_add_request(
                flag=req["flag"], approve=False
            )
        else:
            await bot.set_group_add_request(
                flag=req["flag"],
                sub_type=req.get("sub_type", "add"),
                approve=False,
                reason=reason,
            )
        _mark_handled(req_id, "reject")
        success_msg = f"✅ 已拒绝请求 [{req_id}]" + (f"\n原因：{reason}" if reason else "")
    except Exception as e:
        logger.error(f"[请求管理] 拒绝请求失败: {e}")
        await reject_cmd.finish(f"❌ 拒绝失败：{e}")
        return

    await reject_cmd.finish(success_msg)


async def _reject_all(bot: Bot, event: MessageEvent):
    # 批量操作：仅超级用户可用
    user_id = str(event.user_id)
    if user_id not in [str(s) for s in config_manager.superusers]:
        await reject_cmd.finish("❌ 仅超级用户可执行「全部拒绝」")
        return

    pending = _get_pending_requests()
    if not pending:
        await reject_cmd.finish("✅ 当前没有待处理的请求")
        return

    success = 0
    failed = 0
    for req in pending:
        try:
            if req.get("type") == "friend":
                await bot.set_friend_add_request(flag=req["flag"], approve=False)
            else:
                await bot.set_group_add_request(
                    flag=req["flag"],
                    sub_type=req.get("sub_type", "add"),
                    approve=False,
                )
            _mark_handled(req["id"], "reject")
            success += 1
        except Exception as e:
            logger.error(f"[请求管理] 全部拒绝 - 序号 {req.get('id')} 失败: {e}")
            failed += 1

    msg = f"✅ 全部拒绝完成\n成功：{success} 条\n失败：{failed} 条"
    await reject_cmd.finish(msg)


# ============================================================
# 群禁言检测：bot被禁言时通知超级用户
# ============================================================
ban_notice = on_notice(priority=5, block=False)

# 记录已通知的禁言事件，避免重复通知
_ban_notified = {}  # {group_id: timestamp}


@ban_notice.handle()
async def handle_group_ban(bot: Bot, event):
    """检测bot被禁言事件"""
    if not _is_enabled():
        return

    # 判断是否为群禁言事件
    is_ban_event = False
    if isinstance(event, GroupBanNoticeEvent):
        is_ban_event = True
    elif hasattr(event, "notice_type"):
        if event.notice_type == "group_ban":
            is_ban_event = True
    else:
        return

    if not is_ban_event:
        return

    sub_type = getattr(event, "sub_type", "")
    user_id = getattr(event, "user_id", 0)
    group_id = getattr(event, "group_id", 0)
    operator_id = getattr(event, "operator_id", 0)
    duration = getattr(event, "duration", 0)

    # 只处理禁言（非解禁），且被禁言的是bot自己
    if sub_type == "lift_ban":
        return  # 解禁事件，忽略

    # 获取bot自己的QQ号
    bot_id = int(bot.self_id)

    # 如果被禁言的不是bot，忽略
    if user_id and user_id != bot_id:
        return

    if not group_id:
        return

    import time as _time
    now = _time.time()
    # 同一群10分钟内不重复通知
    if group_id in _ban_notified and now - _ban_notified[group_id] < 600:
        return
    _ban_notified[group_id] = now

    logger.warning(f"[请求管理] Bot在群 {group_id} 被禁言，操作者: {operator_id}，时长: {duration}秒")

    # 获取群名
    group_name = f"群{group_id}"
    try:
        group_info = await bot.get_group_info(group_id=group_id)
        group_name = group_info.get("group_name", group_name)
    except Exception:
        pass

    # 获取操作者昵称
    op_name = f"QQ{operator_id}"
    try:
        op_info = await bot.get_group_member_info(group_id=group_id, user_id=operator_id)
        op_name = op_info.get("card", "") or op_info.get("nickname", op_name)
    except Exception:
        pass

    # 格式化禁言时长
    if duration >= 86400:
        duration_str = f"{duration // 86400}天{(duration % 86400) // 3600}小时"
    elif duration >= 3600:
        duration_str = f"{duration // 3600}小时{(duration % 3600) // 60}分钟"
    else:
        duration_str = f"{duration // 60}分钟"

    msg = (
        f"⚠️ Bot被禁言通知\n"
        f"━━━━━━━━━━━━\n"
        f"群号：{group_id}\n"
        f"群名：{group_name}\n"
        f"操作者：{op_name} ({operator_id})\n"
        f"禁言时长：{duration_str}\n"
        f"━━━━━━━━━━━━\n"
        f"发送「退群 {group_id}」退出该群"
    )

    for su in config_manager.superusers:
        try:
            await bot.send_private_msg(user_id=int(su), message=msg)
        except Exception:
            pass


# ============================================================
# 指令：退群
# ============================================================
leave_cmd = on_command(
    "退群",
    aliases={"离开群", "quit_group"},
    priority=5,
    block=True,
    permission=SUPERUSER,
)


@leave_cmd.handle()
async def handle_leave_group(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    """超级用户退群指令"""
    raw = args.extract_plain_text().strip()

    # 支持在群聊中直接退群（无需输入群号）
    group_id = None
    if isinstance(event, GroupMessageEvent):
        group_id = event.group_id
    elif raw.isdigit():
        group_id = int(raw)

    if not group_id:
        await leave_cmd.finish("❓ 用法：退群 <群号>，或在群聊中直接发送「退群」")
        return

    # 获取群名
    group_name = f"群{group_id}"
    try:
        group_info = await bot.get_group_info(group_id=group_id)
        group_name = group_info.get("group_name", group_name)
    except Exception:
        pass

    success_msg = None
    try:
        await bot.set_group_leave(group_id=group_id)
        logger.info(f"[请求管理] 超级用户退出群: {group_name}({group_id})")
        success_msg = f"✅ 已退出群：{group_name} ({group_id})"
    except Exception as e:
        logger.error(f"[请求管理] 退群失败: {e}")
        await leave_cmd.finish(f"❌ 退群失败：{e}")
        return

    await leave_cmd.finish(success_msg)


# ============================================================
# 菜单注册
# ============================================================
try:
    from plugins.miku_menu import register_plugin_info
except ImportError:
    register_plugin_info = lambda *args, **kwargs: None

register_plugin_info(
    "miku_friend_request",
    name="请求管理",
    icon="📨",
    order=92,
    description="管理好友请求和群聊邀请/申请，群禁言检测",
    commands=["请求列表", "同意", "拒绝", "退群"],
    usage="""超级用户指令：

请求列表
查看所有待处理的好友/群请求

同意 <序号>
同意指定序号的请求
同意全部 - 同意所有待处理请求

拒绝 <序号> [原因]
拒绝指定序号的请求
拒绝全部 - 拒绝所有待处理请求

退群 <群号>
退出指定群聊（或在群聊中直接发送退群）

新请求会自动私聊通知超级用户
同意群请求后默认开启免打扰
Bot被禁言时自动通知超级用户""",
)
