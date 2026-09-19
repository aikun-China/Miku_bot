"""
Miku 拍他插件
=============
- 对 @ 的群成员发送戳一戳（拍一拍）
- 指令：拍他 @目标 — 戳一次
- 指令：拍死他 @目标 — 连续戳五次
- 仅群聊可用，需要机器人有发送戳一戳的权限
"""

import sys
import asyncio
from pathlib import Path

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, MessageSegment
from nonebot.permission import SUPERUSER
from nonebot.rule import to_me
from nonebot.log import logger
from nonebot.plugin import PluginMetadata

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

try:
    from plugins.miku_menu import register_plugin_info
except ImportError:
    register_plugin_info = lambda *args, **kwargs: None

from utils.config_manager import config_manager


__plugin_meta__ = PluginMetadata(
    name="Miku拍他",
    description="对群成员发送戳一戳，拍他/拍死他",
    usage="拍他 @目标  /  拍死他 @目标",
    type="application",
    supported_adapters={"~onebot.v11"},
)


# ============================================================
# 配置
# ============================================================
_PAI_TEMPLATE = (
    "\n"
    "miku_pai:\n"
    "  # 是否启用拍他插件\n"
    "  enabled: true\n"
    "  # 拍死他的次数\n"
    "  pai_dead_times: 5\n"
    "  # 每次戳一戳的间隔时间（秒）\n"
    "  pai_interval: 0.5\n"
    "  # 是否需要@机器人才能使用\n"
    "  need_at: false\n"
    "  # 冷却时间（秒），同一用户两次使用的最小间隔\n"
    "  cooldown: 3\n"
)

_cfg = config_manager.register_plugin(
    "miku_pai",
    defaults={
        "enabled": True,
        "pai_dead_times": 5,
        "pai_interval": 0.5,
        "need_at": False,
        "cooldown": 3,
    },
    template_str=_PAI_TEMPLATE,
    description="拍他插件配置（戳一戳群成员）",
)


def _is_enabled() -> bool:
    raw = config_manager.get("miku_pai", "enabled", True)
    return str(raw).strip().lower() not in ("false", "0", "no", "")


def _conf(key: str, default=None):
    return config_manager.get("miku_pai", key, default)


def _need_at() -> bool:
    raw = _conf("need_at", False)
    return str(raw).strip().lower() in ("true", "1", "yes", "on")


# ============================================================
# 冷却时间记录
# ============================================================
_last_use = {}  # {user_id: timestamp}


def _check_cooldown(user_id: int) -> bool:
    """检查冷却，返回 True 表示可以使用"""
    cooldown = float(_conf("cooldown", 3) or 3)
    if cooldown <= 0:
        return True
    import time
    now = time.time()
    last = _last_use.get(user_id, 0)
    if now - last < cooldown:
        return False
    _last_use[user_id] = now
    return True


# ============================================================
# 从消息中提取 @ 的目标用户
# ============================================================
def _extract_at_target(event: GroupMessageEvent) -> int:
    """
    从消息中提取第一个被@的用户QQ号。
    返回 0 表示没有找到有效目标。
    """
    for seg in event.message:
        if seg.type == "at":
            qq = seg.data.get("qq", "")
            if qq and qq != "all":
                try:
                    return int(qq)
                except (ValueError, TypeError):
                    pass
    return 0


# ============================================================
# 发送戳一戳
# ============================================================
async def _send_poke(bot: Bot, group_id: int, user_id: int) -> bool:
    """
    向群内指定用户发送戳一戳。
    返回 True 表示成功。
    """
    try:
        # 尝试调用 group_poke API（OneBot V11 标准扩展）
        await bot.call_api(
            "group_poke",
            group_id=int(group_id),
            user_id=int(user_id),
        )
        return True
    except Exception as e1:
        logger.debug(f"[miku_pai] group_poke 失败: {e1}")
        # 备用：尝试 poke API
        try:
            await bot.call_api(
                "poke",
                group_id=int(group_id),
                user_id=int(user_id),
            )
            return True
        except Exception as e2:
            logger.debug(f"[miku_pai] poke 也失败: {e2}")
            return False


# ============================================================
# 拍他（一次）
# ============================================================
pai_cmd = on_command(
    "拍他",
    aliases={"戳他", "拍一下", "戳一下"},
    priority=10,
    block=True,
)


@pai_cmd.handle()
async def _handle_pai(bot: Bot, event: GroupMessageEvent):
    if not _is_enabled():
        return

    # 如果需要@机器人
    if _need_at():
        if not event.is_tome():
            return

    user_id = event.user_id
    group_id = event.group_id

    # 冷却检查
    if not _check_cooldown(user_id):
        cooldown = float(_conf("cooldown", 3) or 3)
        await pai_cmd.finish(
            MessageSegment.at(user_id) + f"\n⏳ 冷却中，请 {cooldown:.0f} 秒后再试～"
        )
        return

    # 提取目标
    target_id = _extract_at_target(event)
    if target_id == 0:
        await pai_cmd.finish(
            MessageSegment.at(user_id) + "\n❓ 请 @ 你要拍的对象哦～\n用法：拍他 @目标"
        )
        return

    # 不能拍自己
    if target_id == user_id:
        await pai_cmd.finish(
            MessageSegment.at(user_id) + "\n🤔 自己拍自己？有点奇怪呢..."
        )
        return

    # 发送戳一戳
    ok = await _send_poke(bot, group_id, target_id)
    if ok:
        await pai_cmd.finish(
            MessageSegment.at(user_id) + f"\n👋 已经帮你拍了 {target_id} 一下～"
        )
    else:
        await pai_cmd.finish(
            MessageSegment.at(user_id)
            + "\n❌ 拍失败了...可能是机器人没有戳一戳权限，或者接口不支持"
        )


# ============================================================
# 拍死他（连续多次）
# ============================================================
pai_dead_cmd = on_command(
    "拍死他",
    aliases={"戳死他", "拍烂他", "拍爆他"},
    priority=10,
    block=True,
)


@pai_dead_cmd.handle()
async def _handle_pai_dead(bot: Bot, event: GroupMessageEvent):
    if not _is_enabled():
        return

    if _need_at():
        if not event.is_tome():
            return

    user_id = event.user_id
    group_id = event.group_id

    if not _check_cooldown(user_id):
        cooldown = float(_conf("cooldown", 3) or 3)
        await pai_dead_cmd.finish(
            MessageSegment.at(user_id) + f"\n⏳ 冷却中，请 {cooldown:.0f} 秒后再试～"
        )
        return

    target_id = _extract_at_target(event)
    if target_id == 0:
        await pai_dead_cmd.finish(
            MessageSegment.at(user_id) + "\n❓ 请 @ 你要拍死的对象哦～\n用法：拍死他 @目标"
        )
        return

    if target_id == user_id:
        await pai_dead_cmd.finish(
            MessageSegment.at(user_id) + "\n🤔 自己拍死自己？太狠了吧..."
        )
        return

    times = int(_conf("pai_dead_times", 5) or 5)
    interval = float(_conf("pai_interval", 0.5) or 0.5)
    times = max(1, min(20, times))  # 限制最多20次

    success_count = 0
    fail_count = 0

    # 先回复一下，防止等待太久
    await bot.send(
        event,
        MessageSegment.at(user_id) + f"\n💫 准备连续拍 {target_id} {times} 下！",
    )

    for i in range(times):
        ok = await _send_poke(bot, group_id, target_id)
        if ok:
            success_count += 1
        else:
            fail_count += 1

        if i < times - 1 and interval > 0:
            await asyncio.sleep(interval)

    # 结果
    if success_count > 0:
        msg = (
            MessageSegment.at(user_id)
            + f"\n✅ 完成！成功拍了 {success_count} 下"
        )
        if fail_count > 0:
            msg += f"，失败 {fail_count} 下"
        await pai_dead_cmd.finish(msg)
    else:
        await pai_dead_cmd.finish(
            MessageSegment.at(user_id)
            + "\n❌ 全失败了...可能是机器人没有戳一戳权限，或者接口不支持"
        )


# ============================================================
# 菜单注册
# ============================================================
register_plugin_info(
    "miku_pai",
    name="拍他",
    icon="👋",
    order=11,
    description="对群成员发送戳一戳，拍他/拍死他",
    commands=["拍他", "拍死他"],
    usage="""👋 拍他 / 拍死他

【拍他】
  拍他 @目标  — 对目标发送一次戳一戳
  别名：戳他、拍一下、戳一下

【拍死他】
  拍死他 @目标  — 对目标连续发送多次戳一戳
  别名：戳死他、拍烂他、拍爆他

【配置项】
  在 config/bot.yaml 的 miku_pai 区可配置：
  • pai_dead_times：拍死他的次数（默认5）
  • pai_interval：每次间隔秒数（默认0.5）
  • need_at：是否需要@机器人（默认false）
  • cooldown：冷却时间秒数（默认3）

【注意】
  • 仅群聊可用
  • 需要机器人有发送戳一戳的权限
  • 不能对自己使用
"""
)

logger.info("[miku_pai] 拍他插件已加载")
