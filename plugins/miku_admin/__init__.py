"""
MikuBot 管理员插件
================
- 从 config/bot.yaml 读取配置（首次加载自动注册配置区）
- 指令：重启 / 配置检查 / 刷新配置 / 检查更新 / 立即更新
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

# 导入菜单注册
try:
    from plugins.miku_menu import register_plugin_info
except ImportError:
    register_plugin_info = lambda *args, **kwargs: None


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
    "  # 是否启用管理员功能\n"
    "  enabled: true\n"
    "  # Bot 上线时是否私聊通知超级用户（true/false）\n"
    "  notify_on_start: true\n"
    "  # 管理员指令触发词列表\n"
    "  admin_commands:\n"
    "  - 重启\n"
    "  - 配置检查\n"
    "  - 刷新配置\n"
    "  - 检查更新\n"
    "  - 立即更新\n"
)

_admin_cfg = config_manager.register_plugin(
    "miku_admin",
    defaults={
        "enabled": True,
        "notify_on_start": True,
        "admin_commands": ["重启", "配置检查", "刷新配置", "检查更新", "立即更新"],
    },
    template_str=_ADMIN_TEMPLATE,
    description="管理员插件配置",
)
_notify_on_start = bool(_admin_cfg.get("notify_on_start", True))

# 是否启用（每次调用时动态读取，支持 WebUI 热更新）
def _is_enabled() -> bool:
    raw = config_manager.get("miku_admin", "enabled", True)
    return str(raw).strip().lower() not in ("false", "0", "no", "")

def _notify_enabled() -> bool:
    raw = config_manager.get("miku_admin", "notify_on_start", True)
    return str(raw).strip().lower() not in ("false", "0", "no", "")


# ─── 重启 Bot ───
restart_cmd = on_command("重启", aliases={"restart", "reload"}, priority=1, block=True, permission=SUPERUSER)

@restart_cmd.handle()
async def _(bot: Bot, event: MessageEvent):
    if not _is_enabled():
        return
    await restart_cmd.send("🎵 Miku 正在重启，请稍等...")
    await asyncio.sleep(1)
    python = sys.executable
    args = sys.argv
    os.execv(python, [python] + args)


# ─── 检查配置 ───
config_cmd = on_command("配置检查", aliases={"检查配置", "checkconfig"}, priority=5, block=True, permission=SUPERUSER)

@config_cmd.handle()
async def _(bot: Bot, event: MessageEvent):
    if not _is_enabled():
        return
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
    if not _is_enabled():
        return
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


# ─── 检查更新 ───
check_update_cmd = on_command("检查更新", aliases={"checkupdate", "updatecheck"}, priority=5, block=True, permission=SUPERUSER)

@check_update_cmd.handle()
async def _(bot: Bot, event: MessageEvent):
    if not _is_enabled():
        return
    from utils.version_manager import check_update, format_version_info, get_local_version
    
    await check_update_cmd.send("🎵 Miku 正在检查更新...")
    
    try:
        result = await check_update()
        
        if result.get("error"):
            await check_update_cmd.finish(
                f"🎵 更新检查失败\n"
                f"━━━━━━━━━━━━\n"
                f"{result['error']}\n"
                f"━━━━━━━━━━━━\n"
                f"仓库: https://github.com/{get_local_version().get('github_repo', 'aikun-China/Miku_bot')}"
            )
            return
        
        local_info = get_local_version()
        
        if result["has_update"]:
            msg_lines = [
                "🎵 发现新版本！",
                "━━━━━━━━━━━━",
                f"当前版本: {result['local_version']}",
                f"最新版本: {result['remote_version']}",
            ]
            if result.get("update_time"):
                msg_lines.append(f"更新时间: {result['update_time']}")
            if result.get("changelog"):
                changelog = result['changelog'].replace('\n', ' ')[:100]
                msg_lines.append(f"更新说明: {changelog}...")
            msg_lines.append("━━━━━━━━━━━━")
            msg_lines.append("发送「立即更新」开始下载更新")
            await check_update_cmd.finish("\n".join(msg_lines))
        else:
            await check_update_cmd.finish(
                f"🎵 MikuBot 已是最新版\n"
                f"━━━━━━━━━━━━\n"
                f"当前版本: {result['local_version']}\n"
                f"━━━━━━━━━━━━\n"
                f"{format_version_info(local_info)}"
            )
    
    except Exception as e:
        await check_update_cmd.finish(f"🎵 检查更新出错: {str(e)}")


# ─── 立即更新 ───
do_update_cmd = on_command("立即更新", aliases={"update", "upgrade"}, priority=1, block=True, permission=SUPERUSER)

@do_update_cmd.handle()
async def _(bot: Bot, event: MessageEvent):
    if not _is_enabled():
        return
    from utils.version_manager import check_update
    from utils.updater import do_update, restart_bot
    
    await do_update_cmd.send("🎵 Miku 正在检查更新并下载...")
    
    try:
        # 先检查是否有更新
        check_result = await check_update()
        
        if not check_result["has_update"] and not check_result.get("error"):
            await do_update_cmd.finish("🎵 当前已是最新版，无需更新")
            return
        
        # 执行更新
        update_result = await do_update()
        
        if update_result["success"]:
            files = update_result.get("updated_files", [])
            dirs = update_result.get("updated_dirs", [])
            
            msg_lines = [
                "🎵 更新完成！",
                "━━━━━━━━━━━━",
            ]
            if files:
                msg_lines.append(f"更新文件: {', '.join(files)}")
            if dirs:
                msg_lines.append(f"更新目录: {', '.join(dirs)}")
            msg_lines.append("━━━━━━━━━━━━")
            msg_lines.append("🎵 Miku 正在重启应用更新...")
            
            await do_update_cmd.send("\n".join(msg_lines))
            await asyncio.sleep(2)
            
            # 重启 Bot
            restart_bot()
        else:
            await do_update_cmd.finish(
                f"🎵 更新失败\n"
                f"━━━━━━━━━━━━\n"
                f"{update_result.get('error', '未知错误')}\n"
                f"━━━━━━━━━━━━\n"
                f"可手动访问仓库下载: https://github.com/aikun-China/Miku_bot"
            )
    
    except Exception as e:
        await do_update_cmd.finish(f"🎵 更新出错: {str(e)}")


# ─── Bot 启动通知 ───
driver = get_driver()

@driver.on_bot_connect
async def on_connect(bot: Bot):
    """Bot 连接 QQ 时通知超级用户"""
    from nonebot.log import logger
    if not _is_enabled() or not _notify_enabled():
        return
    superusers = config_manager.superusers
    if not superusers:
        logger.warning("[miku_admin] 启动通知跳过：未配置超级用户")
        return
    logger.info(f"[miku_admin] Bot 已连接，准备发送启动通知给 {len(superusers)} 个超级用户")
    await asyncio.sleep(2)
    for su in superusers:
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
            logger.info(f"[miku_admin] 启动通知已发送: user={su}")
        except Exception as e:
            logger.error(f"[miku_admin] 启动通知发送失败: user={su}, error={e}")


# ─── 菜单注册 ───
register_plugin_info(
    "miku_admin",
    name="管理员",
    icon="👑",
    order=90,
    description="超级用户专用管理功能",
    commands=["重启", "配置检查", "刷新配置", "检查更新", "立即更新"],
    usage="""超级用户专用指令：
重启        - 重启整个 Bot
配置检查    - 查看当前配置状态
刷新配置    - 热重新加载配置文件
检查更新    - 检查是否有新版本
立即更新    - 下载并应用更新

⚠️ 注意：所有指令仅限超级用户使用"""
)
