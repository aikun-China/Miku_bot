"""
MikuBot 管理员插件
================
- 从 config/bot.yaml 读取配置（首次加载自动注册配置区）
- 指令：重启 / 配置检查 / 刷新配置
- 启动通知：Bot 连接后私聊通知超级用户（可在配置中关闭）
"""

from nonebot import on_command, get_driver
from nonebot.adapters.onebot.v11 import Bot, MessageEvent
from nonebot.permission import SUPERUSER
from nonebot.plugin import PluginMetadata
import os
import sys
import asyncio
from pathlib import Path

# 导入 config_manager（项目根路径必须已在 sys.path 中，nonebot 会自动加载 plugins/ 同级目录）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from utils.config_manager import config_manager


__plugin_meta__ = PluginMetadata(
    name="Miku管理员",
    description="管理员指令：重启、配置检查、刷新配置",
    usage="超级用户专用指令：重启 / 配置检查 / 刷新配置",
    type="application",
    supported_adapters={"~onebot.v11"},
)


# ============================================================
# 【核心】插件配置注册（模板字符串放在插件自身里）
# ============================================================
_ADMIN_TEMPLATE = (
    "\n"
    "miku_admin:\n"
    "  # Bot 上线时是否私聊通知超级用户（true/false）\n"
    "  notify_on_start: true\n"
    "  # 管理员指令触发词列表\n"
    "  admin_commands:\n"
    "  - 重启\n"
    "  - 配置检查\n"
    "  - 刷新配置\n"
)

_admin_cfg = config_manager.register_plugin(
    "miku_admin",
    defaults={
        "notify_on_start": True,
        "admin_commands": ["重启", "配置检查", "刷新配置"],
    },
    template_str=_ADMIN_TEMPLATE,
    description="管理员插件配置",
)
_notify_on_start = bool(_admin_cfg.get("notify_on_start", True))


# ─── 重启 Bot ───
restart_cmd = on_command("重启", aliases={"restart", "reload"}, priority=1, block=True, permission=SUPERUSER)

@restart_cmd.handle()
async def _(bot: Bot, event: MessageEvent):
    await restart_cmd.send("🎵 Miku 正在重启，请稍等...")
    await asyncio.sleep(1)
    python = sys.executable
    args = sys.argv
    os.execv(python, [python] + args)


# ─── 检查配置 ───
config_cmd = on_command("配置检查", aliases={"检查配置", "checkconfig"}, priority=5, block=True, permission=SUPERUSER)

@config_cmd.handle()
async def _(bot: Bot, event: MessageEvent):
    superusers = config_manager.superusers
    password = config_manager.webui_password

    has_default_superuser = any(
        u in ("123456789", "10000", "123456", "0", "")
        for u in superusers
    )
    has_default_password = password in ("", "miku8888", "123456", "admin")

    status = []
    if not superusers or has_default_superuser:
        status.append("⚠️ SUPERUSERS 未配置或使用了默认值")
    else:
        status.append(f"✅ SUPERUSERS: {', '.join(superusers)}")

    if not password or has_default_password:
        status.append("⚠️ WEBUI_PASSWORD 未配置或太简单")
    else:
        status.append("✅ WEBUI_PASSWORD 已设置")

    # 已注册的插件配置区
    registered = config_manager.registered_plugins() or []
    if registered:
        status.append(f"📦 已注册插件配置: {', '.join(registered)}")

    msg = (
        "🎵 MikuBot 配置检查\n"
        "━━━━━━━━━━━━\n"
        + "\n".join(status) + "\n"
        "━━━━━━━━━━━━\n"
        f"📡 监听地址：{config_manager.host}:{config_manager.port}\n"
        "━━━━━━━━━━━━\n"
        "配置文件：config/bot.yaml\n"
        "热更新：发送「刷新配置」重新加载"
    )
    await config_cmd.finish(msg)


# ─── 刷新配置（热加载） ───
reload_cmd = on_command("刷新配置", aliases={"reload_config", "reloadcfg"}, priority=5, block=True, permission=SUPERUSER)

@reload_cmd.handle()
async def _(bot: Bot, event: MessageEvent):
    config_manager.reload()
    registered = config_manager.registered_plugins() or []
    lines = [
        "🎵 配置已重新加载",
        "━━━━━━━━━━━━",
        f"主配置: bot (superusers/webui_password/host/port)",
    ]
    if registered:
        lines.append(f"插件配置: {', '.join(registered)}")
    lines.append("━━━━━━━━━━━━")
    lines.append("注意: 插件代码中的全局常量（如 API key）需重启 Bot 才会更新")
    await reload_cmd.finish("\n".join(lines))


# ─── Bot 启动通知 ───
driver = get_driver()

@driver.on_bot_connect
async def on_connect(bot: Bot):
    """Bot 连接 QQ 时通知超级用户"""
    if not _notify_on_start:
        return
    for su in config_manager.superusers:
        try:
            registered = config_manager.registered_plugins() or []
            plugin_list = "、".join(registered) if registered else "（无）"
            await bot.send_private_msg(
                user_id=int(su),
                message=(
                    "🎵 MikuBot 已启动！\n"
                    f"监听端口：{config_manager.port}\n"
                    f"已注册插件：{plugin_list}\n"
                    "发送「配置检查」查看状态"
                )
            )
        except Exception:
            pass
