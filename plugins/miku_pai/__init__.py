"""
Miku 拍他插件
=============
- 对 @ 的群成员发送戳一戳（拍一拍）
- 指令：拍他 @目标 — 戳一次
- 指令：拍死他 @目标 — 连续戳五次
- 仅群聊可用，需要机器人有发送戳一戳的权限
"""

import sys
import re
import asyncio
from pathlib import Path
from typing import Any

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

# ── AI 动作中心（可选依赖：没有它拍他照常工作，只是不能被 AI 调用）──
try:
    from utils.ai_actions import (
        ActionContext,
        STATUS_ASK,
        STATUS_ERROR,
        STATUS_OK,
        STATUS_SKIP,
        register_ai_action,
    )

    _AI_ACTIONS_READY = True
except ImportError:  # pragma: no cover - 仅在精简部署时走到
    _AI_ACTIONS_READY = False
    ActionContext = Any  # type: ignore[assignment,misc]
    register_ai_action = None  # type: ignore[assignment]
    STATUS_OK, STATUS_ASK, STATUS_ERROR, STATUS_SKIP = "ok", "ask", "error", "skip"


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
def _extract_at_target(event: GroupMessageEvent, exclude: int = 0) -> int:
    """
    从消息中提取第一个被@的用户QQ号。
    exclude 用于跳过指定 QQ（AI 动作里跳过机器人自己，避免「拍到自己」）。
    返回 0 表示没有找到有效目标。
    """
    for seg in event.message:
        if seg.type == "at":
            qq = seg.data.get("qq", "")
            if qq and qq != "all":
                try:
                    qq_int = int(qq)
                except (ValueError, TypeError):
                    continue
                if exclude and qq_int == int(exclude):
                    continue
                return qq_int
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
# AI 动作：poke.member（拍一下某人）
# ============================================================
# 只注册「拍一下」这一个能力；「拍死他」这种连续 5 次的玩法不暴露给 AI，
# 避免模型一句话把群友刷屏。
#
# 注意 target 声明成「可选」而不是「必填」：
# 路由层拿到的群消息文本是从 text 段重建的，@ 段会被剥掉，
# 所以「帮我拍一下 @某人」在文本里只剩「帮我拍一下」。
# 如果把 target 设为必填，路由层会在真正执行前就追问「想拍谁呀」——
# 明明已经 @ 了人。因此目标解析放在 handler 里做：先读 event 的 at 段，
# 再退回 ctx.args["target"]，都没有才返回 ask。


def _parse_target_id(raw) -> int:
    """从「@123456」「[CQ:at,qq=123456]」「123456」里抠出 QQ 号，失败返回 0。"""
    text = str(raw or "")
    if not text:
        return 0
    match = re.search(r"(?:qq=)?(\d{5,12})", text)
    if not match:
        return 0
    try:
        return int(match.group(1))
    except (TypeError, ValueError):
        return 0


async def _ai_poke_member(ctx: ActionContext) -> dict:
    """AI 动作 poke.member：给群成员发一次戳一戳。"""
    if not _is_enabled():
        return {"status": STATUS_SKIP}

    # 私聊没法戳一戳，交回普通聊天
    if not ctx.is_group:
        return {"status": STATUS_SKIP}

    group_id = ctx.group_id or str(getattr(ctx.event, "group_id", "") or "")
    user_id = ctx.user_id or str(getattr(ctx.event, "user_id", "") or "")
    if not group_id or not user_id:
        return {"status": STATUS_SKIP}

    # 目标：优先消息段里的 @（跳过机器人自己），其次 ctx.args["target"]（纯数字 QQ 号）
    try:
        bot_self_id = int(getattr(ctx.bot, "self_id", 0) or 0)
    except (TypeError, ValueError):
        bot_self_id = 0
    try:
        target_id = _extract_at_target(ctx.event, exclude=bot_self_id)
    except Exception:
        target_id = 0
    if not target_id:
        target_id = _parse_target_id(ctx.arg_str("target"))
    if not target_id:
        return {"status": STATUS_ASK, "question": "想拍谁呀？@一下他～"}

    if str(target_id) == str(user_id):
        return {"status": STATUS_ERROR, "reply": "🤔 自己拍自己？有点奇怪呢..."}

    # 冷却与命令路径共用（_last_use 按 int user_id 记录）
    try:
        cooldown_key = int(user_id)
    except (TypeError, ValueError):
        cooldown_key = user_id
    if not _check_cooldown(cooldown_key):
        cooldown = float(_conf("cooldown", 3) or 3)
        return {
            "status": STATUS_ERROR,
            "reply": f"⏳ 冷却中，请 {cooldown:.0f} 秒后再试～",
        }

    try:
        ok = await _send_poke(ctx.bot, int(group_id), target_id)
    except Exception as e:
        logger.warning(f"[miku_pai] 动作 poke.member 发送失败: {type(e).__name__}: {e}")
        ok = False

    if ok:
        await ctx.emit(f"👋 已经帮你拍了 {target_id} 一下～")
        return {"status": STATUS_OK}

    return {
        "status": STATUS_ERROR,
        "reply": "❌ 拍失败了...可能是机器人没有戳一戳权限，或者接口不支持",
    }


if _AI_ACTIONS_READY:
    try:
        # patterns 只覆盖命令管不到的说法（「拍一下他」「帮我拍一下」这类），
        # 「拍他 @目标」「拍死他 @目标」命令路径保持原样
        register_ai_action(
            name="poke.member",
            plugin="miku_pai",
            plugin_display="拍他",
            description="在群里戳一戳（拍一拍）指定成员",
            handler=_ai_poke_member,
            params={
                "target": {
                    "type": "string",
                    # 说明见上文：群消息文本里 @ 段会被剥掉，必填会让「已@目标」被误追问
                    "required": False,
                    "desc": "要拍的人，QQ号或@提及",
                    "ask": "想拍谁呀？@一下他～",
                },
            },
            examples=["拍一下他", "帮我拍一下@某人", "戳戳他"],
            patterns=[
                r"^(?:帮我|给我|替我)\s*(?:拍|戳)(?:一下|一拍|一戳)?\s*(?:他|她|它|一下)?\s*(?P<target>@\S+|\d{5,12})?$",
                r"^(?:拍|戳)(?:一下|一拍)(?:他|她|它)[呀啊啦吧嘛哦~～]?$",
                r"^(?:拍|戳)(?:他|她|它)\s*(?P<target>@\S+|\d{5,12})$",
                r"^(?:拍|戳)(?:一下|一拍)\s*(?:他|她|它)?\s*(?P<target>@\S+|\d{5,12})$",
                r"^(?:拍拍|戳戳)(?:他|她|它)?[呀啊啦吧嘛哦~～]?\s*(?P<target>@\S+|\d{5,12})?$",
            ],
            keywords=["拍一下", "拍一拍", "戳一下", "戳戳", "拍拍"],
            scope="group",
            order=24,
        )

        logger.info("[miku_pai] 已注册 AI 动作：poke.member")
    except Exception as e:  # 注册失败不能影响拍他自己
        logger.warning(f"[miku_pai] 注册 AI 动作失败：{type(e).__name__}: {e}")


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
