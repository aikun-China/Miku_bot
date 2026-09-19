"""
Miku 个人资料插件
=================
- 响应风格：图片卡片（支持 text fallback）
- 指令：我的信息 / 个人资料 / 资料 / profile / 查看资料
- 展示内容：UID、昵称、QQ号、好感度、金币、签到天数、道具背包
- 数据来源：data/users/{user_id}.json
- 真实头像缓存：data/avatars/{user_id}.jpg（供所有插件共用）
"""

from nonebot import on_command
from nonebot.adapters.onebot.v11 import (
    Message, MessageSegment, MessageEvent,
)
from nonebot.exception import FinishedException
from nonebot.log import logger

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

try:
    from plugins.miku_menu import register_plugin_info
except ImportError:
    register_plugin_info = lambda *args, **kwargs: None

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)

from utils.html_render import render_template
from utils.screenshot import screenshot_html, to_image_uri
from utils.bg_helper import find_and_load_bg, find_bg_image, get_image_size
from utils.avatar_cache import get_avatar_data_uri
from utils.user_store import get_user, get_items, ITEM_DOUBLE_FAVOR
from utils.config_manager import config_manager
from utils.anime_quotes import random_quote

import json


_PROFILE_TEMPLATE = (
    "\n"
    "miku_profile:\n"
    "  # 是否启用个人资料功能（true/false）\n"
    "  enabled: true\n"
    "  # 响应风格：card（图片卡片）/ text（纯文本）\n"
    "  response_style: card\n"
    "  # 图片卡片宽度（像素）\n"
    "  card_width: 720\n"
)

_cfg = config_manager.register_plugin(
    "miku_profile",
    defaults={
        "enabled": True,
        "response_style": "card",
        "card_width": 720,
    },
    template_str=_PROFILE_TEMPLATE,
    description="个人资料插件配置",
)


def _conf(key: str, default=None):
    return config_manager.get("miku_profile", key, default)


def _get_user_message_count(user_id: int) -> int:
    """获取用户发言数。"""
    msg_stats_file = PROJECT_ROOT / "data" / "message_stats.json"
    if msg_stats_file.exists():
        try:
            with open(msg_stats_file, "r", encoding="utf-8") as f:
                data = json.load(f) or {}
                user_stats = data.get(str(user_id), {})
                return int(user_stats.get("total", 0))
        except Exception:
            pass
    return 0


def _get_user_top_plugins(user_id: int, top_n: int = 3) -> list:
    """获取用户常用的插件及调用次数。"""
    cmd_stats_file = PROJECT_ROOT / "data" / "command_stats.json"
    if cmd_stats_file.exists():
        try:
            with open(cmd_stats_file, "r", encoding="utf-8") as f:
                data = json.load(f) or {}
                user_cmds = data.get(str(user_id), {})
                # 过滤内部元数据键，只保留实际命令
                user_cmds = {k: v for k, v in user_cmds.items() if not k.startswith("__")}
                
                plugin_map = {
                    "ping": ("基础工具", "miku_basic"),
                    "信息": ("基础工具", "miku_basic"),
                    "头像": ("基础工具", "miku_basic"),
                    "我的信息": ("个人资料", "miku_profile"),
                    "个人资料": ("个人资料", "miku_profile"),
                    "资料": ("个人资料", "miku_profile"),
                    "profile": ("个人资料", "miku_profile"),
                    "签到": ("每日签到", "miku_checkin"),
                    "签": ("每日签到", "miku_checkin"),
                    "今日签到": ("每日签到", "miku_checkin"),
                    "菜单": ("菜单", "miku_menu"),
                    "帮助": ("菜单", "miku_menu"),
                    "功能列表": ("菜单", "miku_menu"),
                    "消息排行": ("统计排行", "miku_stats"),
                    "发言排行": ("统计排行", "miku_stats"),
                    "功能排行": ("统计排行", "miku_stats"),
                    "插件排行": ("统计排行", "miku_stats"),
                    "好感排行": ("统计排行", "miku_stats"),
                    "金币排行": ("统计排行", "miku_stats"),
                    "ai": ("AI聊天", "miku_ai"),
                    "聊天": ("AI聊天", "miku_ai"),
                    "说": ("AI聊天", "miku_ai"),
                    "问": ("AI聊天", "miku_ai"),
                    "拍一拍": ("拍一拍", "miku_poke"),
                    "戳一戳": ("拍一拍", "miku_poke"),
                    "通知管理员": ("通知管理员", "miku_notify_admin"),
                }
                
                plugin_counts = {}
                for cmd, count in user_cmds.items():
                    if cmd in plugin_map:
                        plugin_name = plugin_map[cmd][0]
                        plugin_counts[plugin_name] = plugin_counts.get(plugin_name, 0) + count
                
                sorted_plugins = sorted(plugin_counts.items(), key=lambda x: x[1], reverse=True)
                return sorted_plugins[:top_n]
        except Exception:
            pass
    return []


async def _render_profile_card(user_id: int, nickname: str) -> str:
    """渲染个人资料图片卡片。"""
    user_data = get_user(user_id)

    uid = user_data.get("uid", "")
    coins = user_data.get("coins", 0)
    favor = round(float(user_data.get("favor", 0.0)), 2)
    total_days = user_data.get("total_checkin_days", 0)
    items = get_items(user_id)
    double_favor_card_count = items.get(ITEM_DOUBLE_FAVOR, 0)
    
    # 获取发言数
    message_count = _get_user_message_count(user_id)
    
    # 获取常用插件（顶部卡片式）
    top_plugins = _get_user_top_plugins(user_id, 3)
    top_freq_plugins_html = ""
    if top_plugins:
        for rank, (plugin_name, count) in enumerate(top_plugins, start=1):
            top_freq_plugins_html += f'''
<div class="top-freq-card">
    <div class="top-freq-rank">#{rank}</div>
    <div class="top-freq-name">{plugin_name}</div>
    <div class="top-freq-count">{count} 次</div>
</div>
'''
    else:
        top_freq_plugins_html = '''
<div class="top-freq-card">
    <div class="top-freq-rank">-</div>
    <div class="top-freq-name">暂无数据</div>
    <div class="top-freq-count">-</div>
</div>
'''

    # 获取动漫语录
    anime_quote, anime_source = random_quote()

    # 获取用户真实头像
    avatar_uri = await get_avatar_data_uri(user_id, default_char=(nickname or "U")[0])
    if avatar_uri:
        avatar_html = f'<img src="{avatar_uri}" alt="头像">'
    else:
        avatar_html = f'<span class="avatar-fallback">{(nickname or "U")[0]}</span>'

    bg_uri = find_and_load_bg("profile", TEMPLATES_DIR)
    if not bg_uri:
        bg_uri = find_and_load_bg("menu", TEMPLATES_DIR)

    html = render_template(
        "profile",
        TEMPLATES_DIR,
        bg_data_uri=bg_uri,
        nickname=nickname or "未知用户",
        avatar_html=avatar_html,
        user_id=str(user_id),
        uid=uid or "未分配",
        coins=coins,
        favor=favor,
        total_days=total_days,
        message_count=message_count,
        top_freq_plugins_html=top_freq_plugins_html,
        anime_quote=anime_quote,
        anime_source=anime_source,
        double_favor_card_count=double_favor_card_count,
    )

    # 使用底图实际尺寸作为截图尺寸
    bg_path = find_bg_image("profile", TEMPLATES_DIR)
    if not bg_path:
        bg_path = find_bg_image("menu", TEMPLATES_DIR)
    bg_size = get_image_size(bg_path) if bg_path else None
    if bg_size:
        w, h = bg_size
    else:
        w = 2560
        h = 1440
    img_path = await screenshot_html(html, width=w, height=h)
    return to_image_uri(str(img_path))


async def _render_text_profile(user_id: int, nickname: str) -> str:
    """生成纯文本个人资料。"""
    user_data = get_user(user_id)

    uid = user_data.get("uid", "")
    coins = user_data.get("coins", 0)
    favor = round(float(user_data.get("favor", 0.0)), 2)
    total_days = user_data.get("total_checkin_days", 0)
    items = get_items(user_id)
    double_favor_card_count = items.get(ITEM_DOUBLE_FAVOR, 0)

    lines = [
        f"👤 {nickname} 的个人资料",
        "━━━━━━━━━━━━",
        f"QQ：{user_id}",
        f"UID：{uid or '未分配'}",
        f"好感度：{favor}",
        f"金币：{coins}",
        f"累计签到：{total_days} 天",
        f"双倍好感卡：{double_favor_card_count} 张",
        "━━━━━━━━━━━━",
        "发送「签到」获取更多奖励",
    ]
    return "\n".join(lines)


profile_cmd = on_command(
    "我的信息",
    aliases={"个人资料", "资料", "profile", "查看资料", "我的资料"},
    priority=5,
    block=True,
)


@profile_cmd.handle()
async def _(event: MessageEvent):
    if not _conf("enabled", True):
        await profile_cmd.finish("个人资料功能已关闭")

    # 获取发送者的 QQ 号（最可靠的方式）
    user_id = getattr(event, "user_id", None)
    if not user_id:
        try:
            user_id = event.sender.user_id if hasattr(event, "sender") and event.sender else None
        except Exception:
            user_id = None
    if not user_id:
        await profile_cmd.finish("获取用户信息失败，请稍后再试")

    # 优先用 event.sender.nickname，否则用 event.get_user_nickname()
    nickname = ""
    try:
        if hasattr(event, "sender") and event.sender and getattr(event.sender, "nickname", None):
            nickname = event.sender.nickname
    except Exception:
        pass
    if not nickname:
        try:
            nickname = event.get_user_nickname()
        except Exception:
            nickname = ""
    if not nickname:
        nickname = str(user_id)

    # 若没有昵称，尝试通过 Bot API 获取（仅作为最后的 fallback）
    if nickname == str(user_id):
        try:
            from nonebot import get_bot
            bot = get_bot()
            info = await bot.get_stranger_info(user_id=user_id)
            if info and info.get("nickname"):
                nickname = info["nickname"]
        except Exception:
            pass

    try:
        style = str(_conf("response_style", "card") or "card")
        if style == "card":
            card_uri = await _render_profile_card(user_id, nickname)
            if card_uri:
                await profile_cmd.finish(MessageSegment.image(card_uri))
        await profile_cmd.finish(await _render_text_profile(user_id, nickname))
    except FinishedException:
        raise
    except Exception as e:
        logger.exception(f"[miku_profile] 处理失败: {e}")
        await profile_cmd.finish("获取个人资料失败，请稍后再试")


register_plugin_info(
    "miku_profile",
    name="个人资料",
    icon="👤",
    order=5,
    description="查看个人信息、好感度、金币、签到天数",
    commands=["我的信息", "个人资料", "资料"],
    usage="""👤 查看个人资料

【指令】
我的信息 / 个人资料 / 资料 / profile

【展示内容】
・用户 UID（签到自动分配）
・昵称和 QQ 号
・当前好感度
・当前金币数量
・累计签到天数
・双倍好感卡数量

【示例】
发送「我的信息」即可查看""",
)

logger.info("[miku_profile] 个人资料插件已加载")
