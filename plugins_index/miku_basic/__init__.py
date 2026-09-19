"""
MikuBot 基础信息插件
======================
- 从 config/bot.yaml 读取配置（首次加载自动注册配置区）
- 响应风格：card(图片卡片) / text(纯文本)
- 指令：信息 / ping / 状态 / info
"""

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, MessageEvent, MessageSegment
from nonebot.log import logger
from nonebot.plugin import PluginMetadata
from nonebot.exception import FinishedException
import time
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 插件自身的模板目录
TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

from utils.html_render import render_template
from utils.screenshot import screenshot_html, to_image_uri
from utils.config_manager import config_manager


__plugin_meta__ = PluginMetadata(
    name="Miku基础",
    description="基础测试：ping / 信息（图片卡片或纯文本）",
    usage="发送 信息 / ping / 状态 / info",
    type="application",
    supported_adapters={"~onebot.v11"},
)


# ============================================================
# 【核心】插件配置注册（模板字符串放在插件自身里）
# ============================================================
_BASIC_TEMPLATE = (
    "\n"
    "miku_basic:\n"
    "  # Bot 在「信息/ping」指令中显示的名称\n"
    "  bot_name: MikuBot\n"
    "  # 是否在信息卡片中显示系统运行状态（true/false）\n"
    "  show_system_info: true\n"
    "  # 响应风格：card（图片卡片）/ text（纯文本）\n"
    "  response_style: card\n"
)

_cfg = config_manager.register_plugin(
    "miku_basic",
    defaults={
        "bot_name": "MikuBot",
        "show_system_info": True,
        "response_style": "card",
    },
    template_str=_BASIC_TEMPLATE,
    description="基础信息插件配置",
)
BOT_NAME = str(_cfg.get("bot_name", "MikuBot") or "MikuBot")
RESPONSE_STYLE = str(_cfg.get("response_style", "card") or "card")


# ============================================================
# 指令
# ============================================================
info_cmd = on_command("信息", aliases={"ping", "状态", "info"}, priority=5, block=True)


@info_cmd.handle()
async def _(bot: Bot, event: MessageEvent):
    start_time = time.time()
    user_nickname = event.sender.nickname or str(event.user_id)
    response_time = round((time.time() - start_time) * 1000, 1)

    # --- 纯文本风格 ---
    if RESPONSE_STYLE == "text":
        lines = [
            f"🎵 {BOT_NAME} 在线！",
            f"👤 {user_nickname}",
            f"🆔 {event.user_id}",
            f"✅ 运行正常",
            f"⏱ 响应: {response_time}ms",
        ]
        try:
            await info_cmd.finish("\n".join(lines))
        except FinishedException:
            raise
        except Exception as e:
            logger.exception(e)
            return

    # --- 默认图片卡片风格 ---
    html = render_template(
        "ping",
        TEMPLATES_DIR,
        nickname=user_nickname,
        user_id=event.user_id,
        status="运行正常",
        response_time=response_time,
    )
    try:
        img_path = await screenshot_html(html, width=600, height=350)
        await info_cmd.finish(MessageSegment.image(to_image_uri(img_path)))
    except FinishedException:
        raise
    except Exception as e:
        # 图片生成失败，文本 fallback
        logger.warning(f"[miku_basic] 图片生成失败：{e}")
        await info_cmd.finish(
            f"🎵 {BOT_NAME} 在线！\n👤 {user_nickname}\n🆔 {event.user_id}\n✅ 运行正常"
        )
