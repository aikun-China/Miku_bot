"""
MikuBot 基础信息插件
======================
- 从 config/bot.yaml 读取配置（首次加载自动注册配置区）
- 响应风格：card(图片卡片) / text(纯文本)
- 指令：信息 / ping / 状态 / info
- 自检：自检 / selfcheck / 系统状态（返回设备占用、API连通性、用户量等）
"""

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, MessageEvent, MessageSegment
from nonebot.log import logger
from nonebot.plugin import PluginMetadata
from nonebot.exception import FinishedException
import time
import sys
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 导入菜单注册
try:
    from plugins.miku_menu import register_plugin_info
except ImportError:
    register_plugin_info = lambda *args, **kwargs: None

# 插件自身的模板目录
TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

from utils.html_render import render_template
from utils.bg_helper import find_and_load_bg
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
    "  # 是否启用基础信息功能\n"
    "  enabled: true\n"
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
        "enabled": True,
        "bot_name": "MikuBot",
        "show_system_info": True,
        "response_style": "card",
    },
    template_str=_BASIC_TEMPLATE,
    description="基础信息插件配置",
)

# 是否启用（每次调用时动态读取，支持 WebUI 热更新）
def _is_enabled() -> bool:
    raw = config_manager.get("miku_basic", "enabled", True)
    return str(raw).strip().lower() not in ("false", "0", "no", "")

BOT_NAME = str(_cfg.get("bot_name", "MikuBot") or "MikuBot")
RESPONSE_STYLE = str(_cfg.get("response_style", "card") or "card")


# ============================================================
# 指令
# ============================================================
info_cmd = on_command("信息", aliases={"ping", "状态", "info"}, priority=5, block=True)


@info_cmd.handle()
async def _(bot: Bot, event: MessageEvent):
    if not _is_enabled():
        return

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
    bg_uri = find_and_load_bg("basic", TEMPLATES_DIR)
    bg_style = f'style="background-image: url(\'{bg_uri}\')"' if bg_uri else ""
    html = render_template(
        "ping",
        TEMPLATES_DIR,
        nickname=user_nickname,
        user_id=event.user_id,
        status="运行正常",
        response_time=response_time,
        bg_style=bg_style,
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


# ─── 菜单注册 ───
register_plugin_info(
    "miku_basic",
    name="基础信息",
    icon="🎀",
    order=1,
    description="查看 Bot 在线状态、响应时间和系统自检",
    commands=["信息", "ping", "状态", "info", "自检", "selfcheck"],
    usage="""发送以下指令获取 Bot 状态：
信息 / ping / 状态 / info
返回包含您的昵称、QQ号、Bot状态和响应时间的卡片

自检 / selfcheck / 系统状态
返回设备硬件占用、API连通性、用户量等数据
普通用户冷却30分钟，超级用户无冷却"""
)


# ============================================================
# 自检功能
# ============================================================
_COOLDOWN_SECONDS = 1800  # 30分钟冷却
_last_check = {}  # {user_id: timestamp}

check_cmd = on_command("自检", aliases={"selfcheck", "系统状态", "自检状态"}, priority=5, block=True)


def _is_superuser(user_id) -> bool:
    try:
        superusers = config_manager.superusers or []
        return str(user_id) in [str(s) for s in superusers]
    except Exception:
        return False


def _count_users() -> int:
    """统计已注册用户数"""
    users_dir = Path(__file__).resolve().parent.parent / "data" / "users"
    if not users_dir.exists():
        return 0
    return len([f for f in users_dir.glob("*.json") if not f.name.startswith("_")])


async def _check_api_connectivity() -> dict:
    """检查AI API连通性"""
    result = {"text_api": False, "vision_api": False}
    try:
        import httpx
        # 检查文本AI
        base_url = str(config_manager.get("miku_ai", "cloud_base_url", "") or "")
        api_key = str(config_manager.get("miku_ai", "cloud_api_key", "") or "")
        if base_url and api_key:
            url = base_url.rstrip("/") + "/chat/completions"
            headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
            body = {"model": str(config_manager.get("miku_ai", "cloud_model", "glm-4-flash") or "glm-4-flash"),
                    "messages": [{"role": "user", "content": "hi"}], "max_tokens": 1}
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.post(url, headers=headers, json=body)
                result["text_api"] = r.status_code == 200

        # 检查识图AI（如果启用了独立配置）
        vision_separate = config_manager.get("miku_ai", "vision_separate", False)
        if vision_separate:
            v_key = str(config_manager.get("miku_ai", "vision_cloud_api_key", "") or "")
            if not v_key:
                v_key = api_key
            v_url = str(config_manager.get("miku_ai", "vision_cloud_base_url", "") or base_url)
            v_model = str(config_manager.get("miku_ai", "vision_cloud_model", "") or "")
            if v_url and v_key and v_model:
                url = v_url.rstrip("/") + "/chat/completions"
                headers = {"Content-Type": "application/json", "Authorization": f"Bearer {v_key}"}
                body = {"model": v_model, "messages": [{"role": "user", "content": "hi"}], "max_tokens": 1}
                async with httpx.AsyncClient(timeout=10.0) as client:
                    r = await client.post(url, headers=headers, json=body)
                    result["vision_api"] = r.status_code == 200
    except Exception as e:
        logger.debug(f"[miku_basic] API连通性检查失败: {e}")
    return result


@check_cmd.handle()
async def _handle_selfcheck(bot: Bot, event: MessageEvent):
    if not _is_enabled():
        return

    user_id = event.user_id
    now = time.time()

    # 冷却检查（超级用户无冷却）
    if not _is_superuser(user_id):
        last = _last_check.get(str(user_id), 0)
        remaining = _COOLDOWN_SECONDS - (now - last)
        if remaining > 0:
            mins = int(remaining // 60)
            secs = int(remaining % 60)
            await check_cmd.finish(f"⏳ 自检冷却中，请 {mins}分{secs}秒 后再试\n（超级用户无冷却限制）")
            return
    _last_check[str(user_id)] = now

    try:
        import psutil
    except ImportError:
        await check_cmd.finish("❌ 缺少 psutil 库，请运行 pip install psutil")
        return

    # 收集系统信息
    cpu_percent = psutil.cpu_percent(interval=1)
    memory = psutil.virtual_memory()
    disk = psutil.disk_usage(str(Path(__file__).resolve().parent.parent))

    # 运行时间
    try:
        import platform
        boot_time = psutil.boot_time()
        uptime_seconds = int(now - boot_time)
        uptime_str = f"{uptime_seconds // 86400}天{(uptime_seconds % 86400) // 3600}小时{(uptime_seconds % 3600) // 60}分"
    except Exception:
        uptime_str = "未知"

    # Python版本
    py_version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"

    # 用户量
    user_count = _count_users()

    # 好友/群数量
    friend_count = 0
    group_count = 0
    try:
        friend_list = await bot.get_friend_list()
        friend_count = len(friend_list)
    except Exception:
        pass
    try:
        group_list = await bot.get_group_list()
        group_count = len(group_list)
    except Exception:
        pass

    # API连通性
    api_status = await _check_api_connectivity()
    text_api_str = "✅ 正常" if api_status["text_api"] else "❌ 异常"
    vision_api_str = "✅ 正常" if api_status["vision_api"] else "❌ 异常" if config_manager.get("miku_ai", "vision_separate", False) else "未启用"

    # 构建消息
    lines = [
        f"🔍 MikuBot 自检报告",
        f"━━━━━━━━━━━━━━━━",
        f"📊 系统资源",
        f"  CPU占用：{cpu_percent}%",
        f"  内存占用：{memory.percent}% ({memory.used // 1024 // 1024}MB / {memory.total // 1024 // 1024}MB)",
        f"  磁盘占用：{disk.percent}% ({disk.used // 1024 // 1024 // 1024}GB / {disk.total // 1024 // 1024 // 1024}GB)",
        f"  运行时长：{uptime_str}",
        f"  Python：{py_version}",
        f"━━━━━━━━━━━━━━━━",
        f"🌐 API连通性",
        f"  文本AI：{text_api_str}",
        f"  识图AI：{vision_api_str}",
        f"━━━━━━━━━━━━━━━━",
        f"👥 用户数据",
        f"  注册用户：{user_count}",
        f"  好友数量：{friend_count}",
        f"  群聊数量：{group_count}",
        f"━━━━━━━━━━━━━━━━",
        f"✅ 自检完成",
    ]

    if not _is_superuser(user_id):
        lines.append(f"⏱ 下次可用：30分钟后")

    await check_cmd.finish("\n".join(lines))
