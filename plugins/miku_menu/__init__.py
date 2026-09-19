from nonebot import on_command, on_message
from nonebot.adapters.onebot.v11 import MessageSegment, Message, Bot, GroupMessageEvent, PrivateMessageEvent
from nonebot.rule import Rule
from nonebot.typing import T_State
from nonebot.adapters.onebot.v11.event import Event, MessageEvent
from nonebot.exception import FinishedException
from utils.config_manager import config_manager
from utils.html_render import render_template
from utils.screenshot import screenshot_html, to_image_uri
from utils.bg_helper import find_and_load_bg, find_bg_image, get_image_size
from pathlib import Path
import re


def _is_tome_or_private(bot: Bot, event: MessageEvent) -> bool:
    """只有@Bot或私聊时才触发菜单命令，避免其他Bot消息误触发"""
    if isinstance(event, PrivateMessageEvent):
        return True
    if isinstance(event, GroupMessageEvent) and event.is_tome():
        return True
    return False

# ── 菜单插件配置模板 ──
_MENU_TEMPLATE = (
    "\n"
    "miku_menu:\n"
    "  # 是否启用菜单功能\n"
    "  enabled: true\n"
    "  # 响应风格：card（图片卡片）/ text（纯文本）\n"
    "  response_style: card\n"
    "  # 菜单卡片宽度（像素）\n"
    "  card_width: 1080\n"
    "  # 菜单卡片高度（像素）\n"
    "  card_height: 1500\n"
)

_cfg = config_manager.register_plugin(
    "miku_menu",
    defaults={
        "enabled": True,
        "response_style": "card",
        "card_width": 1080,
        "card_height": 1500,
    },
    template_str=_MENU_TEMPLATE,
    description="菜单插件配置",
)

# 是否启用（每次调用时动态读取，支持 WebUI 热更新）
def _is_enabled() -> bool:
    raw = config_manager.get("miku_menu", "enabled", True)
    return str(raw).strip().lower() not in ("false", "0", "no", "")


def _conf(key: str, default=None):
    """动态读取 miku_menu 配置，支持 WebUI 热更新"""
    return config_manager.get("miku_menu", key, default)

# ── 目录配置 ──
TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)

# ── 插件信息注册系统 ──
# 统一使用 utils.plugin_registry 作为全局注册中心
# 这里重新导出，保持向后兼容
from utils.plugin_registry import (
    register_plugin_info,
    get_plugin_info,
    get_all_plugins,
    get_plugin_by_index,
)


# ── 菜单指令 ──
menu_cmd = on_command(
    "菜单",
    aliases={"帮助", "功能", "功能列表", "指令列表", "help", "menu", "功能表"},
    priority=5,
    block=True,
    rule=Rule(_is_tome_or_private),
)
detail_cmd = on_command(
    "详细帮助",
    aliases={"详情", "详细帮助", "详细功能"},
    priority=5,
    block=True,
    rule=Rule(_is_tome_or_private),
)


def _html_escape(s: str) -> str:
    """简单 HTML 转义，防止注入和格式问题"""
    if s is None:
        return ""
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


@menu_cmd.handle()
async def _handle_menu():
    """处理菜单指令"""
    if not _is_enabled():
        return

    plugins = get_all_plugins()

    if _conf("response_style") == "text":
        # 纯文本模式
        lines = ["🎵 MikuBot 功能菜单"]
        lines.append("─" * 30)
        for p in plugins:
            lines.append(f"[{p['index']}] {p['icon']} {p['name']}")
        lines.append("─" * 30)
        lines.append("💡 发送「详细帮助+插件名/编号」获取详情，例如：详细帮助 天气 / 详细帮助 1")
        await menu_cmd.finish("\n".join(lines))
        return

    # 图片卡片模式
    try:
        bg_data_uri = find_and_load_bg("menu", TEMPLATES_DIR)

        # 从底图获取尺寸
        bg_path = find_bg_image("menu", TEMPLATES_DIR)
        card_width, card_height = 1080, 1500  # 默认尺寸
        if bg_path:
            size = get_image_size(bg_path)
            if size:
                card_width, card_height = size

        title_icon_uri = ""
        icon_path = TEMPLATES_DIR / "menu_icon.png"
        if icon_path.is_file():
            from utils.bg_helper import load_bg_as_data_uri as _load_icon
            title_icon_uri = _load_icon(icon_path)

        # 插件卡片 HTML：顶部是编号+图标+名称，下面是描述和指令标签
        parts = []
        for p in plugins:
            # 指令标签（最多显示 3 个，3列布局下保持紧凑）
            cmds = p.get("commands", []) or []
            tags_html = ""
            if cmds:
                shown = cmds[:3]
                tags_html = '<div class="plugin-tags">'
                tags_html += "".join(
                    f'<span class="plugin-tag">{_html_escape(c)}</span>' for c in shown
                )
                tags_html += "</div>"

            parts.append(
                f'<div class="plugin-card">'
                f'<div class="plugin-header">'
                f'<span class="plugin-badge">{p["index"]}</span>'
                f'<span class="plugin-icon">{_html_escape(p["icon"])}</span>'
                f'<span class="plugin-name">{_html_escape(p["name"])}</span>'
                f'</div>'
                f'<div class="plugin-desc">{_html_escape(p.get("description", "暂无描述"))}</div>'
                f'{tags_html}'
                f'</div>'
            )
        plugins_html = "\n".join(parts)

        html = render_template(
            "menu",
            TEMPLATES_DIR,
            plugins_html=plugins_html,
            bg_data_uri=bg_data_uri,
            title_icon_uri=title_icon_uri,
            card_width=card_width,
            card_height=card_height,
        )

        img_path = await screenshot_html(
            html,
            width=card_width,
            height=card_height,
        )

        await menu_cmd.finish(MessageSegment.image(to_image_uri(img_path)))
    except FinishedException:
        raise
    except Exception as e:
        # 降级到纯文本
        lines = ["🎵 MikuBot 功能菜单"]
        lines.append("─" * 30)
        for p in plugins:
            lines.append(f"[{p['index']}] {p['icon']} {p['name']}")
        lines.append("─" * 30)
        lines.append("💡 发送「详细帮助+插件名/编号」获取详情")
        await menu_cmd.finish("\n".join(lines))


@detail_cmd.handle()
async def _handle_detail_help(event: Event):
    """处理详细帮助指令。支持：详细帮助 天气 / 详细帮助 2 / 详情 ai"""
    if not _is_enabled():
        return

    msg = str(event.get_message()).strip()

    # 提取插件名
    plugin_name = ""
    if msg.startswith("详细帮助"):
        plugin_name = msg[4:].strip()
    elif msg.startswith("详情"):
        plugin_name = msg[2:].strip()
    elif msg.lower().startswith("help "):
        plugin_name = msg[5:].strip()

    if not plugin_name:
        await detail_cmd.finish(
            "❓ 请指定要查询的插件名或编号，例如：\n"
            "　　详细帮助 天气\n"
            "　　详细帮助 1"
        )
        return

    plugins = get_all_plugins()  # 带 index 字段

    # --- 策略1：纯数字 → 按编号查找 ---
    info = None
    if plugin_name.isdigit():
        idx = int(plugin_name)
        info = get_plugin_by_index(idx)
        if not info:
            await detail_cmd.finish(
                f"❌ 编号 {idx} 超出范围（当前共 {len(plugins)} 个插件）"
            )
            return
    else:
        # --- 策略2：名称匹配（忽略大小写/空格）+ 模糊匹配 ---
        def _normalize(s: str) -> str:
            return str(s).replace(" ", "").replace("\u3000", "").lower()

        query = _normalize(plugin_name)
        fallback = None

        for p in plugins:
            name_norm = _normalize(p["name"])
            key_norm = _normalize(p["key"])

            # 精确匹配显示名或 plugin key
            if name_norm == query or key_norm == query:
                info = p
                break

            # 模糊匹配：记录第一个候选
            if fallback is None:
                if query in name_norm or query in key_norm:
                    fallback = p
                    continue
                for cmd in p.get("commands", []):
                    if query in _normalize(cmd):
                        fallback = p
                        break

        # 没精确匹配时用模糊候选
        if not info and fallback is not None:
            info = fallback

    if not info:
        lines = [f"❌ 未找到插件「{plugin_name}」"]
        lines.append("可用插件（编号 | 名称）：")
        for p in plugins:
            lines.append(f"　[{p['index']}] {p['icon']} {p['name']}")
        await detail_cmd.finish("\n".join(lines))
        return

    # 生成帮助信息
    if _conf("response_style") == "text":
        lines = [f"🎵 {info['icon']} {info['name']}"]
        lines.append("─" * 30)
        if info["description"]:
            lines.append(f"📖 功能描述：{info['description']}")
        if info["commands"]:
            lines.append(f"🔧 可用指令：{' / '.join(info['commands'])}")
        if info["usage"]:
            lines.append("📝 使用方法：")
            lines.append(info["usage"])
        await detail_cmd.finish("\n".join(lines))
        return

    # 图片卡片模式
    try:
        bg_data_uri = find_and_load_bg("menu", TEMPLATES_DIR)

        # 从底图获取尺寸
        bg_path = find_bg_image("menu", TEMPLATES_DIR)
        card_width, card_height = 1080, 1500  # 默认尺寸
        if bg_path:
            size = get_image_size(bg_path)
            if size:
                card_width, card_height = size

        # 头部：编号徽章 + 图标
        idx_html = f'<span class="badge-index">{info["index"]}</span>' if "index" in info else ""
        header_icon_html = f'{idx_html}<span class="badge-icon">{_html_escape(info["icon"])}</span>'

        # 在 Python 端拼好各个 section 的 HTML，绕过 Jinja2 条件/循环
        sections = []
        if info.get("description"):
            sections.append(
                f'<div class="section">'
                f'<div class="section-title">📖 功能描述</div>'
                f'<div class="desc-content">{_html_escape(info["description"])}</div>'
                f'</div>'
            )
        if info.get("commands"):
            tags = "".join(
                f'<span class="command-tag">{_html_escape(cmd)}</span>'
                for cmd in info["commands"]
            )
            sections.append(
                f'<div class="section">'
                f'<div class="section-title">🔧 可用指令</div>'
                f'<div class="commands-list">{tags}</div>'
                f'</div>'
            )
        if info.get("usage"):
            sections.append(
                f'<div class="section">'
                f'<div class="section-title">📝 使用方法</div>'
                f'<div class="usage-box">{_html_escape(info["usage"])}</div>'
                f'</div>'
            )
        sections_html = "\n".join(sections)

        html = render_template(
            "menu_detail",
            TEMPLATES_DIR,
            plugin_name=_html_escape(info["name"]),
            header_icon_html=header_icon_html,
            sections_html=sections_html,
            bg_data_uri=bg_data_uri,
            card_width=card_width,
            card_height=card_height,
        )

        img_path = await screenshot_html(
            html,
            width=card_width,
            height=card_height,
        )

        await detail_cmd.finish(MessageSegment.image(to_image_uri(img_path)))
    except FinishedException:
        raise
    except Exception as e:
        lines = [f"🎵 {info['icon']} {info['name']}"]
        lines.append("─" * 30)
        if info["description"]:
            lines.append(f"📖 功能描述：{info['description']}")
        if info["commands"]:
            lines.append(f"🔧 可用指令：{' / '.join(info['commands'])}")
        if info["usage"]:
            lines.append("📝 使用方法：")
            lines.append(info["usage"])
        await detail_cmd.finish("\n".join(lines))


# ── 导出注册函数供其他插件使用 ──
__all__ = ["register_plugin_info", "get_plugin_info", "get_all_plugins"]


# ─── 菜单插件自身注册到菜单 ───
register_plugin_info(
    "miku_menu",
    name="菜单",
    icon="📋",
    order=2,
    description="查看所有功能插件的列表和详细用法",
    commands=["菜单", "帮助", "功能", "help", "menu", "详细帮助"],
    usage="""发送以下指令查看功能菜单：

  菜单 / 帮助 / 功能 / help / menu

返回内容：
  • 每个插件显示编号、图标、名称
  • 显示插件可用的指令标签

查看单个插件的详细用法：

  详细帮助 天气    （按插件名）
  详细帮助 1       （按编号）
  详情 AI           （别名）"""
)
