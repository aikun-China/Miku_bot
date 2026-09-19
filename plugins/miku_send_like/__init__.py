"""
Miku 点赞插件
================
- 指令：点赞 / 赞我（需 @ 机器人）
- 指令：点赞信息
- 功能：每天为调用者点赞 5 次，每次 10 个，共 50 个
- 数据保存：data/send_like_log.json
"""

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, MessageEvent
from nonebot.adapters.onebot.v11.message import MessageSegment
from nonebot.rule import to_me
from nonebot.log import logger
from nonebot.plugin import PluginMetadata

from datetime import datetime
from pathlib import Path
import json

from utils.config_manager import config_manager

__plugin_meta__ = PluginMetadata(
    name="Miku点赞",
    description="给主人点赞，每天一次哦",
    usage="@机器人 点赞 / 赞我 / 点赞信息",
    type="application",
    supported_adapters={"~onebot.v11"},
)

__plugin_name__ = "miku_send_like"
__plugin_describe__ = "QQ 名片点赞"

# 项目根目录
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# 点赞日志文件
LIKE_LOG_FILE = PROJECT_ROOT / "data" / "send_like_log.json"
LIKE_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)


# ============================================================
# 插件配置注册
# ============================================================
_SEND_LIKE_TEMPLATE = (
    "\n"
    "miku_send_like:\n"
    "  # 是否启用点赞插件\n"
    "  enabled: true\n"
    "  # 每次调用点赞的次数\n"
    "  like_rounds: 5\n"
    "  # 每轮点赞的个数（QQ 单次上限 10）\n"
    "  like_times_per_round: 10\n"
    "  # 每日限制次数\n"
    "  daily_limit: 1\n"
    "  # 管理员指令触发词列表\n"
    "  admin_commands:\n"
    "  - 点赞信息\n"
)

_send_like_cfg = config_manager.register_plugin(
    "miku_send_like",
    defaults={
        "enabled": True,
        "like_rounds": 5,
        "like_times_per_round": 10,
        "daily_limit": 1,
        "admin_commands": ["点赞信息"],
    },
    template_str=_SEND_LIKE_TEMPLATE,
    description="点赞插件配置",
)


def _is_enabled() -> bool:
    raw = config_manager.get("miku_send_like", "enabled", True)
    return str(raw).strip().lower() not in ("false", "0", "no", "")


def _conf(key: str, default=None):
    return config_manager.get("miku_send_like", key, default)


# ============================================================
# 点赞日志持久化
# ============================================================
def _load_like_log() -> dict:
    if LIKE_LOG_FILE.exists():
        try:
            return json.loads(LIKE_LOG_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_like_log(data: dict):
    LIKE_LOG_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _get_user_records(user_id: str) -> dict:
    data = _load_like_log()
    return data.setdefault(str(user_id), {"total": 0, "days": 0, "last_date": ""})


def _record_like(user_id: str, count: int):
    data = _load_like_log()
    user_id = str(user_id)
    today = _today_str()

    records = data.setdefault(user_id, {"total": 0, "days": 0, "last_date": ""})
    records["total"] = records.get("total", 0) + count
    if records.get("last_date") != today:
        records["days"] = records.get("days", 0) + 1
        records["last_date"] = today

    _save_like_log(data)


def _has_liked_today(user_id: str) -> bool:
    records = _get_user_records(str(user_id))
    return records.get("last_date") == _today_str()


# ============================================================
# 指令：点赞 / 赞我
# ============================================================
like_cmd = on_command(
    "点赞",
    aliases={"赞我"},
    rule=to_me(),
    priority=5,
    block=True,
)


@like_cmd.handle()
async def handle_like(bot: Bot, event: MessageEvent):
    if not _is_enabled():
        return

    user_id = event.user_id

    # 每日限制
    daily_limit = int(_conf("daily_limit", 1))
    if daily_limit > 0 and _has_liked_today(user_id):
        await like_cmd.finish(
            MessageSegment.at(user_id) + "\n请不要这么贪心，今天已经点过赞了哦！"
        )
        return

    rounds = max(1, int(_conf("like_rounds", 5)))
    times_per_round = max(1, min(10, int(_conf("like_times_per_round", 10))))

    like_count = 0
    failed = False
    try:
        for _ in range(rounds):
            await bot.send_like(user_id=int(user_id), times=times_per_round)
            like_count += times_per_round
    except Exception as e:
        failed = True
        logger.error(f"[点赞] 调用 bot.send_like 失败: {e}")

    if like_count > 0:
        _record_like(user_id, like_count)
        await like_cmd.finish(
            MessageSegment.at(user_id)
            + f"\n🎵 Miku 给你点了 {like_count} 个赞哦，不客气！"
        )
    elif failed:
        await like_cmd.finish(
            MessageSegment.at(user_id) + "\n点赞失败了，可能是今天已经点过或接口受限..."
        )
    else:
        await like_cmd.finish(MessageSegment.at(user_id) + "\n点赞没有成功...")


# ============================================================
# 指令：点赞信息
# ============================================================
like_info_cmd = on_command(
    "点赞信息",
    aliases={"我的点赞", "点赞统计"},
    priority=5,
    block=True,
)


@like_info_cmd.handle()
async def handle_like_info(bot: Bot, event: MessageEvent):
    if not _is_enabled():
        return

    user_id = event.user_id
    records = _get_user_records(str(user_id))
    total = records.get("total", 0)
    days = records.get("days", 0)

    if total <= 0:
        await like_info_cmd.finish(
            MessageSegment.at(user_id) + "\n🎵 Miku 还没有给你点过赞哦..."
        )
        return

    await like_info_cmd.finish(
        MessageSegment.at(user_id)
        + f"\n🎵 累计点赞 {days} 天，共给你点了 {total} 个赞哦，记得谢谢 Miku！"
    )


# ============================================================
# 菜单注册
# ============================================================
try:
    from plugins.miku_menu import register_plugin_info
except ImportError:
    register_plugin_info = lambda *args, **kwargs: None

register_plugin_info(
    "miku_send_like",
    name="点赞小助手",
    icon="👍",
    order=12,
    description="每天给主人点赞一次",
    commands=["点赞", "赞我", "点赞信息"],
    usage="""@机器人 点赞 / 赞我
每天可点赞一次，每次 50 个赞

点赞信息
查看累计点赞天数和总数""",
)
