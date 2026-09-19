"""
Miku 统计 + 排行 + 管理员作弊指令插件
========================================
- 消息数量统计（分近一天/周/月/年/总，总消息/单群/私聊）
- 功能调用统计（按命令词记录）
- 排行命令：消息排行、功能排行、好感排行、金币排行
- 管理员作弊指令：加/减/设置金币、加/减/设置好感度、重置数据

数据文件：
  data/message_stats.json    — 消息统计
  data/command_stats.json    — 功能调用统计
"""

from nonebot import on_message, on_command, get_driver
from nonebot.adapters.onebot.v11 import (
    Bot, MessageEvent, GroupMessageEvent, PrivateMessageEvent, MessageSegment
)
from nonebot.permission import SUPERUSER
from nonebot.log import logger
from nonebot.exception import FinishedException
from pathlib import Path
import sys
import json
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from utils.config_manager import config_manager
from utils.user_store import get_user, add_coins, add_favor, get_balance, get_favor, set_coins, set_favor, reset_user
from utils.html_render import render_template
from utils.screenshot import screenshot_html, to_image_uri
from utils.bg_helper import find_and_load_bg
from utils.avatar_cache import get_avatar_data_uri


try:
    from plugins.miku_menu import register_plugin_info
except ImportError:
    register_plugin_info = lambda *args, **kwargs: None


_STATS_TEMPLATE = (
    "\n"
    "miku_stats:\n"
    "  # 是否启用统计插件\n"
    "  enabled: true\n"
    "  # 排行显示条数（默认 10）\n"
    "  top_count: 10\n"
)

_cfg = config_manager.register_plugin(
    "miku_stats",
    defaults={
        "enabled": True,
        "top_count": 10,
    },
    template_str=_STATS_TEMPLATE,
    description="统计与排行插件配置",
)


def _is_enabled() -> bool:
    raw = config_manager.get("miku_stats", "enabled", True)
    return str(raw).strip().lower() not in ("false", "0", "no", "")


def _top_count() -> int:
    return int(config_manager.get("miku_stats", "top_count", 10) or 10)


# ── 数据文件路径 ────────────────────────────────────────────
STATS_DIR = PROJECT_ROOT / "data"
STATS_DIR.mkdir(parents=True, exist_ok=True)
MSG_STATS_FILE = STATS_DIR / "message_stats.json"
CMD_STATS_FILE = STATS_DIR / "command_stats.json"

# ── 模板目录 ────────────────────────────────────────────────
TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)


# ── 消息统计工具 ────────────────────────────────────────────
def _load_msg_stats() -> Dict:
    if not MSG_STATS_FILE.exists():
        return {}
    try:
        with open(MSG_STATS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def _save_msg_stats(data: Dict) -> None:
    try:
        with open(MSG_STATS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"[miku_stats] 保存消息统计失败: {e}")


# ── 群每日消息统计 ────────────────────────────────────────
GROUP_DAILY_FILE = STATS_DIR / "group_daily_stats.json"


def _load_group_daily_stats() -> Dict:
    if not GROUP_DAILY_FILE.exists():
        return {}
    try:
        with open(GROUP_DAILY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def _save_group_daily_stats(data: Dict) -> None:
    try:
        with open(GROUP_DAILY_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"[miku_stats] 保存群每日统计失败: {e}")


def _record_group_daily(group_id: str, day_key: str):
    """记录某个群某天的消息数。"""
    data = _load_group_daily_stats()
    group_id = str(group_id)
    if group_id not in data:
        data[group_id] = {"total": 0, "daily": {}}
    g = data[group_id]
    g["total"] = int(g.get("total", 0)) + 1
    daily = g.setdefault("daily", {})
    daily[day_key] = int(daily.get(day_key, 0)) + 1
    _save_group_daily_stats(data)


# ── 全局每日消息统计 ─────────────────────────────────────
GLOBAL_DAILY_FILE = STATS_DIR / "global_daily_stats.json"


def _load_global_daily_stats() -> Dict:
    if not GLOBAL_DAILY_FILE.exists():
        return {}
    try:
        with open(GLOBAL_DAILY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def _save_global_daily_stats(data: Dict) -> None:
    try:
        with open(GLOBAL_DAILY_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"[miku_stats] 保存全局每日统计失败: {e}")


def _record_global_daily(day_key: str, is_private: bool):
    """记录全局某天的消息数。"""
    data = _load_global_daily_stats()
    if day_key not in data:
        data[day_key] = {"total": 0, "group": 0, "private": 0}
    d = data[day_key]
    d["total"] = int(d.get("total", 0)) + 1
    if is_private:
        d["private"] = int(d.get("private", 0)) + 1
    else:
        d["group"] = int(d.get("group", 0)) + 1
    _save_global_daily_stats(data)


def _record_message(user_id: str, group_id: Optional[str] = None, is_private: bool = False):
    """记录一条消息统计。"""
    data = _load_msg_stats()
    user_id = str(user_id)
    if user_id not in data:
        data[user_id] = {"total": 0, "groups": {}, "private": 0, "daily": {}, "weekly": {}, "monthly": {}, "yearly": {}}

    u = data[user_id]
    u["total"] = int(u.get("total", 0)) + 1

    now = datetime.now()
    day_key = now.strftime("%Y-%m-%d")
    week_key = now.strftime("%Y-W%W")
    month_key = now.strftime("%Y-%m")
    year_key = now.strftime("%Y")

    for key, d in [(day_key, u["daily"]), (week_key, u["weekly"]), (month_key, u["monthly"]), (year_key, u["yearly"])]:
        d[key] = int(d.get(key, 0)) + 1

    if is_private:
        u["private"] = int(u.get("private", 0)) + 1
    elif group_id:
        g = u.setdefault("groups", {})
        g[str(group_id)] = int(g.get(str(group_id), 0)) + 1

    _save_msg_stats(data)
    _record_group_daily(group_id, day_key) if group_id else None
    _record_global_daily(day_key, is_private)


# ── 功能调用统计工具 ─────────────────────────────────────────
def _load_cmd_stats() -> Dict:
    if not CMD_STATS_FILE.exists():
        return {}
    try:
        with open(CMD_STATS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def _save_cmd_stats(data: Dict) -> None:
    try:
        with open(CMD_STATS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"[miku_stats] 保存功能统计失败: {e}")


def _record_command(user_id: str, command_name: str):
    """记录一次功能调用（用户维度）。"""
    data = _load_cmd_stats()
    user_id = str(user_id)
    if user_id not in data:
        data[user_id] = {"__total__": 0}
    u = data[user_id]
    u[command_name] = int(u.get(command_name, 0)) + 1
    u["__total__"] = int(u.get("__total__", 0)) + 1
    _save_cmd_stats(data)


# ── 插件调用统计（新增）─────────────────────────────────────────
PLUGIN_STATS_FILE = STATS_DIR / "plugin_stats.json"


def _load_plugin_stats() -> Dict:
    if not PLUGIN_STATS_FILE.exists():
        return {}
    try:
        with open(PLUGIN_STATS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def _save_plugin_stats(data: Dict) -> None:
    try:
        with open(PLUGIN_STATS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"[miku_stats] 保存插件统计失败: {e}")


def _record_plugin_call(plugin_name: str, command_name: str = ""):
    """记录一次插件调用（插件维度）。"""
    data = _load_plugin_stats()
    if plugin_name not in data:
        data[plugin_name] = {"__total__": 0, "__commands__": {}, "__daily__": {}}
    p = data[plugin_name]
    p["__total__"] = int(p.get("__total__", 0)) + 1
    if command_name:
        cmds = p.setdefault("__commands__", {})
        cmds[command_name] = int(cmds.get(command_name, 0)) + 1
    day_key = datetime.now().strftime("%Y-%m-%d")
    daily = p.setdefault("__daily__", {})
    daily[day_key] = int(daily.get(day_key, 0)) + 1
    _save_plugin_stats(data)


def record_plugin_usage(plugin_name: str, user_id: str = "", command_name: str = ""):
    """
    公共接口：记录一次插件使用（插件维度 + 用户维度）
    其他插件可以直接调用此函数来统计使用次数
    """
    try:
        _record_plugin_call(plugin_name, command_name)
        if user_id and command_name:
            _record_command(str(user_id), command_name)
    except Exception as e:
        logger.warning(f"[miku_stats] 记录插件使用失败 ({plugin_name}): {e}")


# ── 命令到插件的映射 ────────────────────────────────────────
_COMMAND_TO_PLUGIN = {
    # 基础信息
    "ping": "miku_basic",
    "信息": "miku_basic",
    "状态": "miku_basic",
    "头像": "miku_basic",
    
    # 个人资料
    "我的信息": "miku_profile",
    "个人资料": "miku_profile",
    "资料": "miku_profile",
    "profile": "miku_profile",
    "查看资料": "miku_profile",
    "我的资料": "miku_profile",
    
    # 签到
    "签到": "miku_checkin",
    "签": "miku_checkin",
    "今日签到": "miku_checkin",
    "每日签到": "miku_checkin",
    "打卡": "miku_checkin",
    "每日打卡": "miku_checkin",
    
    # 菜单
    "菜单": "miku_menu",
    "帮助": "miku_menu",
    "功能列表": "miku_menu",
    
    # 统计
    "消息排行": "miku_stats",
    "发言排行": "miku_stats",
    "功能排行": "miku_stats",
    "插件排行": "miku_stats",
    "插件统计": "miku_stats",
    "好感排行": "miku_stats",
    "金币排行": "miku_stats",
    "谁最活跃": "miku_stats",
    "谁用的多": "miku_stats",
    "谁最喜欢我": "miku_stats",
    "富豪榜": "miku_stats",
    "谁最有钱": "miku_stats",
    
    # AI对话
    "ai": "miku_ai",
    "聊天": "miku_ai",
    "说": "miku_ai",
    "问": "miku_ai",
    
    # 戳一戳
    "拍一拍": "miku_poke",
    "戳一戳": "miku_poke",
    
    # 通知管理员
    "通知管理员": "miku_notify_admin",
    
    # 天气
    "天气": "miku_weather",
    
    # 管理员
    "重启": "miku_admin",
    "刷新配置": "miku_admin",
    "检查配置": "miku_admin",
    "检查更新": "miku_admin",
    "立即更新": "miku_admin",
    
    # 商店
    "购买": "miku_shop",
    "商城": "miku_shop",
    "商店": "miku_shop",
    "商品列表": "miku_shop",
    "背包": "miku_shop",
    "使用道具": "miku_shop",
    "上架": "miku_shop",
    "下架": "miku_shop",
    
    # 点赞
    "点赞": "miku_send_like",
    "每日点赞": "miku_send_like",
}


# ── 消息监听（priority=1, block=False 只记录不拦截）──────────
msg_listener = on_message(priority=1, block=False)


@msg_listener.handle()
async def _(bot: Bot, event: MessageEvent):
    if not _is_enabled():
        return
    # 忽略 Bot 自己发的消息
    try:
        if str(event.user_id) == str(bot.self_id):
            return
    except Exception:
        pass

    user_id = str(event.user_id)
    if isinstance(event, GroupMessageEvent):
        _record_message(user_id, group_id=str(event.group_id), is_private=False)
    else:
        _record_message(user_id, is_private=True)
    
    # 记录插件调用统计 + 用户命令统计
    message_text = str(event.get_message()).strip()
    if message_text:
        # 尝试匹配命令（支持命令前缀：/, !, ！, 或无前缀）
        # 去掉命令前缀后再匹配
        text_for_match = message_text
        for prefix in ("/", "!", "！"):
            if text_for_match.startswith(prefix):
                text_for_match = text_for_match[len(prefix):].strip()
                break
        
        for cmd, plugin_name in _COMMAND_TO_PLUGIN.items():
            if text_for_match.startswith(cmd):
                _record_plugin_call(plugin_name, cmd)
                _record_command(user_id, cmd)
                break


# ── 排行命令 ────────────────────────────────────────────────
# 消息排行
msg_rank_cmd = on_command(
    "消息排行",
    aliases={"发言排行", "谁最活跃", "消息统计"},
    priority=5,
    block=True,
)

# 功能排行
cmd_rank_cmd = on_command(
    "功能排行",
    aliases={"插件排行", "谁用的多", "功能统计"},
    priority=5,
    block=True,
)

# 插件调用排行（新增）
plugin_rank_cmd = on_command(
    "插件排行",
    aliases={"插件调用排行", "插件统计"},
    priority=5,
    block=True,
)

# 好感排行
favor_rank_cmd = on_command(
    "好感排行",
    aliases={"谁最喜欢我", "好感榜", "好感统计"},
    priority=5,
    block=True,
)

# 金币排行
coins_rank_cmd = on_command(
    "金币排行",
    aliases={"富豪榜", "谁最有钱", "金币统计"},
    priority=5,
    block=True,
)


# ── 辅助：构建排行文本 ─────────────────────────────────────
def _build_rank_text(title: str, items: List[tuple], unit: str = "") -> str:
    """items: [(name, value, extra?), ...] 已按从大到小排序"""
    if not items:
        return f"🎵 {title}\n━━━━━━━━━━━━\n暂无数据~"

    lines = [f"🎵 {title}", "━━━━━━━━━━━━"]
    medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
    for i, item in enumerate(items[:_top_count()]):
        name = str(item[0]) if item[0] else "未知"
        value = item[1]
        medal = medals[i] if i < len(medals) else f"{i+1}."
        extra = f" {item[2]}" if len(item) > 2 and item[2] else ""
        lines.append(f"{medal} {name} — {value}{unit}{extra}")
    lines.append("━━━━━━━━━━━━")
    return "\n".join(lines)


def _get_msg_rank_data(group_id: Optional[str] = None) -> List[tuple]:
    """返回消息排行数据 [(user_id, count, extra), ...] 按从大到小排序。"""
    data = _load_msg_stats()
    if not data:
        return []

    items = []
    for user_id, stats in data.items():
        if group_id:
            count = int(stats.get("groups", {}).get(str(group_id), 0))
        else:
            count = int(stats.get("total", 0))
        if count > 0:
            items.append((user_id, count, ""))

    items.sort(key=lambda x: x[1], reverse=True)
    return items


def _get_plugin_rank_data() -> List[tuple]:
    """返回插件调用排行数据 [(plugin_name, count, extra), ...] 按从大到小排序。"""
    data = _load_plugin_stats()
    if not data:
        return []
    items = []
    for plugin_name, stats in data.items():
        total = int(stats.get("__total__", 0))
        if total > 0:
            items.append((plugin_name, total, ""))
    items.sort(key=lambda x: x[1], reverse=True)
    return items


def _get_favor_rank_data() -> List[tuple]:
    """返回好感度排行数据 [(user_id, favor), ...] 按从大到小排序。"""
    items = []
    # 扫描所有用户文件
    users_dir = PROJECT_ROOT / "data" / "users"
    if not users_dir.exists():
        return items
    for f in users_dir.glob("*.json"):
        if f.name.startswith("_"):
            continue
        user_id = f.stem
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            favor = float(data.get("favor", 0.0))
            if favor > 0:
                items.append((user_id, round(favor, 2), ""))
        except Exception:
            continue
    items.sort(key=lambda x: x[1], reverse=True)
    return items


def _get_coins_rank_data() -> List[tuple]:
    """返回金币排行数据 [(user_id, coins), ...] 按从大到小排序。"""
    items = []
    users_dir = PROJECT_ROOT / "data" / "users"
    if not users_dir.exists():
        return items
    for f in users_dir.glob("*.json"):
        if f.name.startswith("_"):
            continue
        user_id = f.stem
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            coins = int(data.get("coins", 0))
            if coins > 0:
                items.append((user_id, coins, ""))
        except Exception:
            continue
    items.sort(key=lambda x: x[1], reverse=True)
    return items


def _resolve_nickname(bot: Bot, user_id: str) -> str:
    """尝试获取用户昵称，失败时返回 QQ 号。"""
    try:
        info = bot.sync_get_stranger_info(user_id=int(user_id))
        if info and info.get("nickname"):
            return info["nickname"]
    except Exception:
        pass
    return user_id


# ── 排行榜图片生成 ──────────────────────────────────────────
async def _render_rank_image(
    title: str,
    subtitle: str,
    items: List[tuple],
    score_label: str,
    rank_icon: str = "📊",
    decor_icon: str = "✨",
) -> Optional[str]:
    """生成排行榜图片（750*1334，带底图）。返回图片 URI，失败返回 None。"""
    try:
        # 获取底图
        bg_uri = find_and_load_bg("stats", TEMPLATES_DIR)
        if not bg_uri:
            bg_uri = find_and_load_bg("menu", TEMPLATES_DIR)

        # 构建排行项 HTML
        rank_items_html = ""
        for i, (name, value, extra, avatar_uri) in enumerate(items[:_top_count()], 1):
            rank_class = f"rank-{i}" if i <= 3 else "rank-other"
            rank_text = ["🥇", "🥈", "🥉", str(i)][min(i-1, 3)]
            
            if avatar_uri:
                avatar_html = f'<img src="{avatar_uri}" alt="头像">'
            else:
                avatar_html = f'<div class="avatar-fallback">{name[0]}</div>'
            
            rank_items_html += f'''
<div class="rank-item">
    <div class="rank-badge {rank_class}">{rank_text}</div>
    <div class="avatar-box">{avatar_html}</div>
    <div class="user-info">
        <div class="user-name">{name}</div>
        <div class="user-id">{extra}</div>
    </div>
    <div class="score-box">
        <div class="score-value">{value}</div>
        <div class="score-label">{score_label}</div>
    </div>
</div>
'''

        # 当前时间
        from datetime import datetime
        now = datetime.now()
        footer_text = now.strftime("%Y年%m月%d日 更新")

        # 渲染模板
        html = render_template(
            "ranking",
            TEMPLATES_DIR,
            bg_data_uri=bg_uri,
            title=title,
            subtitle=subtitle,
            rank_icon=rank_icon,
            decor_icon=decor_icon,
            rank_items=rank_items_html,
            footer_text=footer_text,
        )

        # 截图生成图片（750*1334）
        img_path = await screenshot_html(html, width=750, height=1334)
        return to_image_uri(str(img_path))
    except Exception as e:
        logger.exception(f"[miku_stats] 生成排行榜图片失败: {e}")
        return None


# ── 消息排行 handler ───────────────────────────────────────
@msg_rank_cmd.handle()
async def _(bot: Bot, event: MessageEvent):
    if not _is_enabled():
        return

    group_id = None
    if isinstance(event, GroupMessageEvent):
        group_id = str(event.group_id)

    items = _get_msg_rank_data(group_id=group_id)
    if not items:
        await msg_rank_cmd.finish("🎵 消息排行\n━━━━━━━━━━━━\n暂无数据，多发点消息再来查看吧~")
        return

    # 获取昵称和头像
    enriched = []
    for user_id, count, extra in items:
        nick = await _get_nickname_async(bot, user_id)
        avatar_uri = await get_avatar_data_uri(user_id, default_char=(nick or "U")[0])
        enriched.append((nick, count, f"QQ {user_id}", avatar_uri))

    title = "群消息排行" if group_id else "全群消息排行"
    subtitle = f"统计范围：{event.group_name if group_id else '所有群聊'}"

    # 尝试生成图片
    img_uri = await _render_rank_image(
        title=title,
        subtitle=subtitle,
        items=enriched,
        score_label="消息数",
        rank_icon="💬",
        decor_icon="💫",
    )

    if img_uri:
        await msg_rank_cmd.finish(MessageSegment.image(img_uri))
    else:
        # 回退到文字
        text_items = [(n, c, e) for n, c, e, _ in enriched]
        text = _build_rank_text(title, text_items, " 条")
        await msg_rank_cmd.finish(text)


# ── 功能排行 handler ───────────────────────────────────────
@cmd_rank_cmd.handle()
async def _(bot: Bot, event: MessageEvent):
    if not _is_enabled():
        return

    items = _get_cmd_rank_data()
    if not items:
        await cmd_rank_cmd.finish("🎵 功能调用排行\n━━━━━━━━━━━━\n暂无数据，多使用功能再来查看吧~")
        return

    enriched = []
    for user_id, count, extra in items:
        nick = await _get_nickname_async(bot, user_id)
        enriched.append((nick, count, extra))

    text = _build_rank_text("功能调用排行", enriched, " 次")
    await cmd_rank_cmd.finish(text)


# ── 插件调用排行 handler ───────────────────────────────────────
@plugin_rank_cmd.handle()
async def _(bot: Bot, event: MessageEvent):
    if not _is_enabled():
        return

    items = _get_plugin_rank_data()
    if not items:
        await plugin_rank_cmd.finish("🎵 插件调用排行\n━━━━━━━━━━━━\n暂无数据~")
        return

    # 尝试获取插件中文名
    try:
        from plugins.miku_menu import get_plugin_info
        enriched = []
        for plugin_name, count, extra in items:
            info = get_plugin_info(plugin_name)
            label = info.get("name", plugin_name)
            enriched.append((label, count, ""))
    except Exception:
        enriched = items

    text = _build_rank_text("插件调用排行", enriched, " 次")
    await plugin_rank_cmd.finish(text)


# ── 好感排行 handler ───────────────────────────────────────
@favor_rank_cmd.handle()
async def _(bot: Bot, event: MessageEvent):
    if not _is_enabled():
        return

    items = _get_favor_rank_data()
    if not items:
        await favor_rank_cmd.finish("🎵 好感度排行\n━━━━━━━━━━━━\n暂无数据，多和Miku聊天吧~")
        return

    enriched = []
    for user_id, count, extra in items:
        nick = await _get_nickname_async(bot, user_id)
        enriched.append((nick, count, extra))

    text = _build_rank_text("好感度排行", enriched, "")
    await favor_rank_cmd.finish(text)


# ── 金币排行 handler ───────────────────────────────────────
@coins_rank_cmd.handle()
async def _(bot: Bot, event: MessageEvent):
    if not _is_enabled():
        return

    items = _get_coins_rank_data()
    if not items:
        await coins_rank_cmd.finish("🎵 金币排行\n━━━━━━━━━━━━\n暂无数据，多签到获取金币吧~")
        return

    enriched = []
    for user_id, count, extra in items:
        nick = await _get_nickname_async(bot, user_id)
        enriched.append((nick, count, extra))

    text = _build_rank_text("金币富豪榜", enriched, " 金币")
    await coins_rank_cmd.finish(text)


async def _get_nickname_async(bot: Bot, user_id: str) -> str:
    """异步获取用户昵称。"""
    try:
        info = await bot.get_stranger_info(user_id=int(user_id))
        if info and info.get("nickname"):
            return info["nickname"]
    except Exception:
        pass
    return user_id


# ── 管理员作弊指令 ───────────────────────────────────────────
# 使用 SUPERUSER 权限，只有 .env 中配置的 SUPERUSERS 能执行

cheat_cmd = on_command(
    "作弊",
    aliases={"管理员作弊", "admin作弊"},
    priority=5,
    block=True,
    permission=SUPERUSER,
)


@cheat_cmd.handle()
async def _(bot: Bot, event: MessageEvent):
    if not _is_enabled():
        return

    text = str(event.get_message()).strip()
    # 去掉命令前缀
    # 格式：作弊 加金币 @QQ 100 / 作弊 设置好感 @QQ 50
    # 简化：解析纯文本参数
    parts = text.split()
    if len(parts) < 3:
        await cheat_cmd.finish(
            "🎮 管理员作弊指令\n"
            "━━━━━━━━━━━━\n"
            "用法：\n"
            "  作弊 加金币 [QQ号] [数量]\n"
            "  作弊 减金币 [QQ号] [数量]\n"
            "  作弊 设置金币 [QQ号] [数量]\n"
            "  作弊 加好感 [QQ号] [数量]\n"
            "  作弊 减好感 [QQ号] [数量]\n"
            "  作弊 设置好感 [QQ号] [数量]\n"
            "  作弊 重置数据 [QQ号]\n"
            "━━━━━━━━━━━━"
        )
        return

    action = parts[1]
    target_id = parts[2]
    amount = 0
    if len(parts) >= 4:
        try:
            amount = int(parts[3]) if "金币" in action else float(parts[3])
        except Exception:
            pass

    result = ""
    try:
        if action == "加金币":
            add_coins(target_id, amount)
            result = f"✅ 已给 {target_id} 增加 {amount} 金币，当前余额：{get_balance(target_id)}"
        elif action == "减金币":
            add_coins(target_id, -amount)
            result = f"✅ 已给 {target_id} 减少 {amount} 金币，当前余额：{get_balance(target_id)}"
        elif action == "设置金币":
            set_coins(target_id, amount)
            result = f"✅ 已设置 {target_id} 金币为 {amount}"
        elif action == "加好感":
            add_favor(target_id, amount)
            result = f"✅ 已给 {target_id} 增加 {amount} 好感度，当前好感：{get_favor(target_id)}"
        elif action == "减好感":
            add_favor(target_id, -amount)
            result = f"✅ 已给 {target_id} 减少 {amount} 好感度，当前好感：{get_favor(target_id)}"
        elif action == "设置好感":
            set_favor(target_id, amount)
            result = f"✅ 已设置 {target_id} 好感度为 {amount}"
        elif action == "重置数据":
            reset_user(target_id)
            # 同时清统计
            msg_stats = _load_msg_stats()
            msg_stats.pop(target_id, None)
            _save_msg_stats(msg_stats)
            cmd_stats = _load_cmd_stats()
            cmd_stats.pop(target_id, None)
            _save_cmd_stats(cmd_stats)
            result = f"✅ 已重置 {target_id} 的所有数据"
        else:
            result = "❌ 未知操作，请查看帮助"
    except Exception as e:
        result = f"❌ 操作失败: {e}"

    await cheat_cmd.finish(result)


# ── 菜单注册 ───────────────────────────────────────────────
register_plugin_info(
    "miku_stats",
    name="统计与排行",
    icon="📊",
    order=13,
    description="消息统计、功能调用统计、插件调用统计、好感/金币排行、管理员作弊指令",
    commands=[
        "消息排行", "发言排行", "谁最活跃", "消息统计",
        "功能排行", "谁用的多", "功能统计",
        "插件排行", "插件调用排行", "插件统计",
        "好感排行", "谁最喜欢我", "好感榜", "好感统计",
        "金币排行", "富豪榜", "谁最有钱", "金币统计",
        "作弊",
    ],
    usage="""📊 统计与排行系统

【消息统计】
  消息排行 / 发言排行 / 谁最活跃 / 消息统计
  → 显示消息数量排行（群聊中仅统计当前群，私聊中统计全群）

【功能调用统计（用户维度）】
  功能排行 / 谁用的多 / 功能统计
  → 显示用户使用功能次数排行

【插件调用统计（插件维度）】
  插件排行 / 插件调用排行 / 插件统计
  → 显示各插件被调用次数排行

【好感排行】
  好感排行 / 谁最喜欢我 / 好感榜 / 好感统计
  → 显示好感度最高的用户排行

【金币排行】
  金币排行 / 富豪榜 / 谁最有钱 / 金币统计
  → 显示金币最多的用户排行

【管理员作弊指令】（仅超级管理员可用）
  作弊 加金币 [QQ] [数量]
  作弊 减金币 [QQ] [数量]
  作弊 设置金币 [QQ] [数量]
  作弊 加好感 [QQ] [数量]
  作弊 减好感 [QQ] [数量]
  作弊 设置好感 [QQ] [数量]
  作弊 重置数据 [QQ]
""",
)

logger.info("[miku_stats] 统计与排行插件已加载")
