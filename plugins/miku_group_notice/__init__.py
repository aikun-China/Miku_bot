"""
群助手插件（原群通知，升级版）
========================
- 加群欢迎：自动 @新人 发送欢迎消息（支持单群自定义文字+图片）
- 退群提醒：xxx 离开了我们
- 踢出提醒：xxx 被 xxx 发往了火星
- 进群审批：按年龄/性别/等级自动审批加群申请（on_request）
- 违禁词管控：检测违禁词 → 撤回 + 可选禁言/踢人（按群存储）
- 禁言/解禁/踢人：管理员指令（支持 1m/1h/1d 时长格式）
- 撤回消息：引用消息手动撤回
- 权限控制：仅群主/管理员/超级用户可使用管理指令，且不受功能约束
"""

from nonebot import on_notice, on_command, on_request, on_message
from nonebot.exception import FinishedException
from nonebot.adapters.onebot.v11 import (
    GroupIncreaseNoticeEvent,
    GroupDecreaseNoticeEvent,
    GroupBanNoticeEvent,
    GroupMessageEvent,
    PrivateMessageEvent,
    GroupRequestEvent,
    Bot,
)
from nonebot.adapters.onebot.v11.message import MessageSegment, Message
from nonebot.permission import SUPERUSER
from nonebot.params import CommandArg
from nonebot.rule import Rule
from nonebot.log import logger
from utils.config_manager import config_manager
from utils.screenshot import screenshot_html, to_image_uri
from pathlib import Path
import asyncio, json, httpx, hashlib, re, time

__plugin_name__ = "miku_group_notice"
__plugin_describe__ = "群助手"

# ─── 插件配置模板（带中文注释，配置前缀保持 miku_group_notice）───
_NOTICE_TEMPLATE = (
    "\n"
    "miku_group_notice:\n"
    "  # 是否启用群助手插件\n"
    "  enabled: true\n"
    "  # 是否使用图片模式发送通知\n"
    "  use_image: true\n"
    "  # 管理员指令触发词列表\n"
    "  admin_commands:\n"
    "  - 进群欢迎设置\n"
    "  - 违禁词添加\n"
    "  - 违禁词删除\n"
    "  - 违禁词列表\n"
    "  - 违禁词开关\n"
    "  - 违禁词处理\n"
    "  - 禁言\n"
    "  - 解禁\n"
    "  - 踢人\n"
    "  - 撤回\n"
    "  - 群助手审批\n"
    "  - 群助手审批开关\n"
)

_config = config_manager.register_plugin(
    "miku_group_notice",
    defaults={
        "enabled": True,
        "use_image": True,
        "admin_commands": [
            "进群欢迎设置",
            "违禁词添加",
            "违禁词删除",
            "违禁词列表",
            "违禁词开关",
            "违禁词处理",
            "禁言",
            "解禁",
            "踢人",
            "撤回",
            "群助手审批",
            "群助手审批开关",
        ],
    },
    template_str=_NOTICE_TEMPLATE,
    description="群助手插件配置",
)

PLUGIN_DIR = Path(__file__).parent
TEMPLATES_DIR = PLUGIN_DIR / "templates"

# 欢迎图片存储目录（放在 config 下，不会被定时清理）
WELCOME_IMG_DIR = PLUGIN_DIR.parent.parent / "config" / "welcome_imgs"
WELCOME_IMG_DIR.mkdir(parents=True, exist_ok=True)

# 群欢迎数据文件
WELCOME_DATA_FILE = PLUGIN_DIR.parent.parent / "config" / "group_welcome.json"
# 群助手数据文件（违禁词 + 进群审批配置，按群存储）
ASSISTANT_DATA_FILE = PLUGIN_DIR.parent.parent / "config" / "group_assistant.json"


# ============================================================
# 数据持久化：群欢迎语
# ============================================================
def _load_welcome_data() -> dict:
    """加载群欢迎数据"""
    if WELCOME_DATA_FILE.exists():
        try:
            return json.loads(WELCOME_DATA_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_welcome_data(data: dict):
    """保存群欢迎数据"""
    WELCOME_DATA_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _get_group_welcome(group_id: str) -> dict:
    """获取某个群的欢迎配置"""
    return _load_welcome_data().get(str(group_id), {})


def _set_group_welcome(group_id: str, welcome: dict):
    """设置某个群的欢迎配置"""
    data = _load_welcome_data()
    data[str(group_id)] = welcome
    _save_welcome_data(data)


# ============================================================
# 数据持久化：群助手（违禁词 + 进群审批）
# ============================================================
def _load_assistant_data() -> dict:
    """加载群助手数据（违禁词 + 审批配置）"""
    if ASSISTANT_DATA_FILE.exists():
        try:
            return json.loads(ASSISTANT_DATA_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_assistant_data(data: dict):
    """保存群助手数据"""
    ASSISTANT_DATA_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _get_group_config(group_id: str) -> dict:
    """获取某个群的助手配置（含 banned_words 与 approval）"""
    return _load_assistant_data().get(str(group_id), {})


def _set_group_config(group_id: str, config: dict):
    """设置某个群的助手配置"""
    data = _load_assistant_data()
    data[str(group_id)] = config
    _save_assistant_data(data)


def _get_group_banned_words(group_id: str) -> dict:
    """获取某个群的违禁词配置（带默认值）"""
    cfg = _get_group_config(group_id)
    bw = cfg.get("banned_words", {})
    return {
        "words": list(bw.get("words", [])),
        "enabled": bool(bw.get("enabled", False)),
        "action": bw.get("action", "recall"),  # recall / mute / kick
        "mute_duration": int(bw.get("mute_duration", 600)),
    }


def _set_group_banned_words(group_id: str, bw: dict):
    """设置某个群的违禁词配置"""
    cfg = _get_group_config(group_id)
    cfg["banned_words"] = bw
    _set_group_config(group_id, cfg)


def _get_group_approval(group_id: str) -> dict:
    """获取某个群的进群审批配置（带默认值）"""
    cfg = _get_group_config(group_id)
    ap = cfg.get("approval", {})
    return {
        "auto_approve": bool(ap.get("auto_approve", False)),
        "conditions": ap.get(
            "conditions",
            {"age_max": 0, "age_min": 0, "gender": "", "level_min": 0},
        ),
    }


def _set_group_approval(group_id: str, ap: dict):
    """设置某个群的进群审批配置"""
    cfg = _get_group_config(group_id)
    cfg["approval"] = ap
    _set_group_config(group_id, cfg)


# ============================================================
# 配置读取（带短缓存，支持热更新）
# ============================================================
_config_cache = {}
_cache_time = 0
_CACHE_TTL = 10


def _load_conf() -> dict:
    """加载插件配置"""
    global _config_cache, _cache_time
    if time.time() - _cache_time > _CACHE_TTL:
        _config_cache = config_manager.get_plugin_config("miku_group_notice")
        _cache_time = time.time()
    return _config_cache


def _conf(key: str, default=None):
    """读取配置项"""
    return _load_conf().get(key, default)


def _is_enabled() -> bool:
    """插件是否启用"""
    raw = config_manager.get("miku_group_notice", "enabled", True)
    return str(raw).strip().lower() not in ("false", "0", "no", "")


# ============================================================
# 权限检查辅助函数
# ============================================================
async def _check_admin(bot: Bot, event) -> bool:
    """检查用户是否为群主/管理员/超级用户"""
    user_id = str(event.user_id)
    # 超级用户
    try:
        superusers = config_manager.superusers or []
        if user_id in [str(s) for s in superusers]:
            return True
    except Exception:
        pass
    # 私聊非超管无权限
    if not hasattr(event, "group_id") or event.group_id is None:
        return False
    # 群主/管理员
    try:
        member_info = await bot.get_group_member_info(
            group_id=event.group_id, user_id=event.user_id
        )
        role = member_info.get("role", "member")
        return role in ("admin", "owner")
    except Exception:
        return False


async def _is_bot_admin(bot: Bot, group_id: int) -> bool:
    """检查机器人本身是否为该群管理员/群主"""
    try:
        bot_id = int(bot.self_id)
        info = await bot.get_group_member_info(group_id=group_id, user_id=bot_id)
        return info.get("role", "member") in ("admin", "owner")
    except Exception:
        return False


async def _get_member_display_name(bot: Bot, group_id, user_id) -> str:
    """获取群成员显示名（群名片优先，失败回退 QQ 号）"""
    try:
        info = await bot.get_group_member_info(
            group_id=int(group_id), user_id=int(user_id)
        )
        return info.get("card", "") or info.get("nickname", "") or f"QQ{user_id}"
    except Exception:
        return f"QQ{user_id}"


# ============================================================
# 通用工具函数
# ============================================================
async def _download_image(url: str, group_id: str) -> str | None:
    """
    下载群欢迎图片，以群号命名保存到 config/welcome_imgs/
    返回保存后的本地路径
    """
    try:
        url_hash = hashlib.md5(url.encode()).hexdigest()[:8]
        filename = f"welcome_{group_id}_{url_hash}.jpg"
        save_path = WELCOME_IMG_DIR / filename

        if save_path.exists():
            return str(save_path)

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                data = resp.content
                save_path.write_bytes(data)
                return str(save_path)
    except Exception:
        pass
    return None


def _parse_duration(text: str) -> int:
    """解析时长字符串为秒，支持 1m / 1h / 1d / 1s 格式，纯数字按秒处理"""
    text = (text or "").strip().lower()
    if not text:
        return 0
    m = re.match(r"^(\d+)\s*([smhd]?)$", text)
    if not m:
        return 0
    num = int(m.group(1))
    unit = m.group(2) or "s"
    multipliers = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    return num * multipliers[unit]


def _extract_at_users(event) -> list:
    """从消息中提取被 @ 的用户 QQ 号（过滤 all；机器人自身在调用方过滤）"""
    users = []
    for seg in event.message:
        if seg.type == "at":
            qq = str(seg.data.get("qq", ""))
            if qq and qq != "all":
                users.append(qq)
    return users


def _parse_approval_conditions(text: str):
    """
    解析审批条件字符串，返回 (group_id, conditions)
    conditions: {age_max, age_min, gender, level_min}
    group_id 可能为 None（群聊中使用当前群）
    示例：年龄<30 性别=男 等级>5
    """
    conditions = {"age_max": 0, "age_min": 0, "gender": "", "level_min": 0}
    group_id = None
    parts = (text or "").split()
    for part in parts:
        part = part.strip()
        if not part:
            continue
        # 群号（纯数字，5~12 位）
        if part.isdigit() and 5 <= len(part) <= 12 and group_id is None:
            group_id = part
            continue
        # 年龄条件：年龄<30 / 年龄>30 / 年龄=30 / 年龄<=30
        m = re.match(r"^年龄\s*([<>=]+)\s*(\d+)$", part)
        if m:
            op = m.group(1)
            val = int(m.group(2))
            if op in ("<", "<="):
                conditions["age_max"] = val
            elif op in (">", ">="):
                conditions["age_min"] = val
            elif op == "=":
                conditions["age_max"] = val
                conditions["age_min"] = val
            continue
        # 等级条件：等级>5 / 等级<5 / 等级=5
        m = re.match(r"^等级\s*([<>=]+)\s*(\d+)$", part)
        if m:
            op = m.group(1)
            val = int(m.group(2))
            if op in (">", ">=", "="):
                conditions["level_min"] = val
            continue
        # 性别条件：性别=男 / 性别=女 / 性别=male / 性别=female
        m = re.match(r"^性别\s*=\s*(男|女|male|female)$", part, re.IGNORECASE)
        if m:
            g = m.group(1).lower()
            if g in ("男", "male"):
                conditions["gender"] = "male"
            elif g in ("女", "female"):
                conditions["gender"] = "female"
            continue
    return group_id, conditions


async def _check_approval_conditions(bot: Bot, conditions: dict, user_id) -> tuple:
    """检查用户是否符合进群审批条件，返回 (是否通过, 原因)"""
    age_max = int(conditions.get("age_max", 0) or 0)
    age_min = int(conditions.get("age_min", 0) or 0)
    gender_req = str(conditions.get("gender", "") or "")
    level_min = int(conditions.get("level_min", 0) or 0)

    # 未配置任何条件时默认同意
    if not any([age_max, age_min, gender_req, level_min]):
        return True, "无条件限制，默认通过"

    # 获取陌生人信息（用户尚未入群）
    try:
        info = await bot.get_stranger_info(user_id=int(user_id))
    except Exception as e:
        logger.warning(f"[群助手] 获取陌生人信息失败: {e}")
        return False, "无法获取用户信息"

    age = int(info.get("age", 0) or 0)
    sex = str(info.get("sex", "") or "")
    level = int(info.get("level", 0) or 0)

    if age_min > 0 and age < age_min:
        return False, f"年龄 {age} < 最低 {age_min}"
    if age_max > 0 and age > age_max:
        return False, f"年龄 {age} > 最高 {age_max}"
    if gender_req and sex != gender_req:
        return False, f"性别 {sex or '未知'} 不符合 {gender_req}"
    if level_min > 0 and level < level_min:
        return False, f"等级 {level} < 最低 {level_min}"
    return True, "符合审批条件"


# ============================================================
# 健壮的事件类型匹配（修复 Task 11：兼容 NapCat / Lagrange 等协议端）
# ============================================================
def _is_group_increase_event(event) -> bool:
    """判断是否为群成员增加事件"""
    if isinstance(event, GroupIncreaseNoticeEvent):
        return True
    if getattr(event, "notice_type", "") == "group_increase":
        return True
    raw = getattr(event, "_raw_event", None)
    if isinstance(raw, dict) and raw.get("notice_type") == "group_increase":
        return True
    return False


def _is_group_decrease_event(event) -> bool:
    """判断是否为群成员减少事件"""
    if isinstance(event, GroupDecreaseNoticeEvent):
        return True
    if getattr(event, "notice_type", "") == "group_decrease":
        return True
    raw = getattr(event, "_raw_event", None)
    if isinstance(raw, dict) and raw.get("notice_type") == "group_decrease":
        return True
    return False


def _is_group_request_event(event) -> bool:
    """判断是否为群请求事件"""
    if isinstance(event, GroupRequestEvent):
        return True
    if getattr(event, "request_type", "") == "group":
        return True
    raw = getattr(event, "_raw_event", None)
    if isinstance(raw, dict) and raw.get("request_type") == "group":
        return True
    return False


# ============================================================
# 图片渲染（磨砂玻璃风格，保持原有实现）
# ============================================================
async def _render_welcome_image(
    text: str, bg_uri: str, group_name: str = "", title: str = "欢迎新成员", icon: str = "👋"
) -> str:
    """渲染群助手图片（欢迎/退群/踢人共用）"""
    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{
    width: 720px;
    font-family: "Microsoft YaHei", "PingFang SC", sans-serif;
    position: relative;
    color: #1a2a4a;
    background: #f0faff url("{bg_uri}") center top / cover no-repeat;
    min-height: 500px;
}}
body::before {{
    content: "";
    position: absolute;
    inset: 0;
    background: linear-gradient(180deg, rgba(0, 229, 232, 0.15) 0%, rgba(0, 184, 186, 0.35) 70%, rgba(0, 184, 186, 0.55) 100%);
    z-index: -1;
}}
.container {{
    position: relative;
    z-index: 2;
    padding: 60px 50px;
    text-align: center;
}}
.icon {{ font-size: 100px; margin-bottom: 24px; }}
.title {{
    font-size: 48px;
    font-weight: bold;
    color: #00a8cc;
    letter-spacing: 6px;
    margin-bottom: 16px;
}}
.subtitle {{
    font-size: 20px;
    color: #4a5a7a;
    margin-bottom: 36px;
}}
.content {{
    background: rgba(255, 255, 255, 0.65);
    backdrop-filter: blur(18px) saturate(150%);
    -webkit-backdrop-filter: blur(18px) saturate(150%);
    border-radius: 28px;
    padding: 40px 50px;
    border: 2px solid rgba(255, 255, 255, 0.75);
    box-shadow: 0 10px 40px rgba(0, 122, 124, 0.25);
}}
.welcome-text {{
    font-size: 32px;
    color: #1a2a4a;
    line-height: 2;
}}
.footer {{
    margin-top: 28px;
    font-size: 16px;
    color: #4a5a7a;
}}
</style>
</head>
<body>
<div class="container">
    <div class="icon">{icon}</div>
    <div class="title">{title}</div>
    <div class="subtitle">{group_name}</div>
    <div class="content">
        <div class="welcome-text">{text}</div>
    </div>
    <div class="footer">MikuBot · 群助手</div>
</div>
</body>
</html>"""

    img_path = await screenshot_html(html, width=720, height=550)
    return to_image_uri(str(img_path))


# ============================================================
# 注册插件信息（name 改为「群助手」）
# ============================================================
def _register_info():
    from utils.plugin_registry import register_plugin_info

    register_plugin_info(
        "miku_group_notice",
        name="群助手",
        icon="🛡️",
        order=15,
        description="加群欢迎、退群提醒、进群审批、违禁词管控、禁言踢人撤回等群管功能",
        commands=[
            "进群欢迎设置",
            "违禁词添加",
            "违禁词删除",
            "违禁词列表",
            "违禁词开关",
            "违禁词处理",
            "禁言",
            "解禁",
            "踢人",
            "撤回",
            "群助手审批",
            "群助手审批开关",
        ],
        usage="""加群欢迎 / 退群提醒 / 踢人通知（自动触发）

管理员指令（仅群主/管理员/超级用户）：
进群欢迎设置 [文字内容]    设置本群欢迎语（附带图片会保存为欢迎图）
违禁词添加 词语             添加违禁词
违禁词删除 词语             删除违禁词
违禁词列表                  查看违禁词列表
违禁词开关                  开启/关闭违禁词检测
违禁词处理 撤回|禁言|踢人   设置触发处理方式（禁言可跟时长，如 禁言 10m）
禁言 @用户 时长             禁言（时长：1m/1h/1d）
解禁 @用户                  解除禁言
踢人 @用户                  踢出群聊
撤回（引用消息）             撤回被引用的消息
群助手审批 [条件]           设置自动审批条件并开启（年龄<30 性别=男 等级>5）
群助手审批开关 开|关        开启/关闭自动审批""",
    )


try:
    _register_info()
except Exception:
    pass


# ============================================================
# 加群欢迎（修复 Task 11：增强事件匹配）
# ============================================================
group_increase = on_notice(block=False, priority=10)


@group_increase.handle()
async def handle_group_increase(bot: Bot, event):
    """处理加群事件"""
    if not _is_enabled():
        return

    event_type = type(event).__name__
    notice_type = getattr(event, "notice_type", "N/A")
    logger.info(f"[群助手] 收到通知事件: {event_type}, notice_type={notice_type}")

    # 健壮匹配：兼容各协议端
    if not _is_group_increase_event(event):
        return
    logger.info("[群助手] 匹配到群成员增加事件")

    user_id = getattr(event, "user_id", "")
    group_id = str(getattr(event, "group_id", ""))

    if not user_id or not group_id:
        logger.warning(f"[群助手] 加群事件缺少字段: user_id={user_id}, group_id={group_id}")
        return

    logger.info(f"[群助手] 加群事件: user={user_id}, group={group_id}")

    try:
        display_name = await _get_member_display_name(bot, group_id, user_id)

        # 读取该群的自定义欢迎配置
        group_welcome = _get_group_welcome(group_id)
        custom_text = group_welcome.get("text", "")
        custom_img = group_welcome.get("image", "")

        def _fmt_text(text):
            return text.replace("{name}", display_name)

        # 自定义图片优先
        if custom_img and Path(custom_img).exists():
            msg = Message()
            msg.append(MessageSegment.at(user_id))
            msg.append(MessageSegment.image(f"file:///{custom_img}"))
            if custom_text:
                msg.append(MessageSegment.text(f"\n{_fmt_text(custom_text)}"))
            logger.info(f"[群助手] 发送自定义图片欢迎: group={group_id}")
            await group_increase.finish(msg)
            return

        # 自定义文字
        if custom_text:
            msg = Message()
            msg.append(MessageSegment.at(user_id))
            msg.append(MessageSegment.text(_fmt_text(custom_text)))
            logger.info(f"[群助手] 发送自定义文字欢迎: group={group_id}")
            await group_increase.finish(msg)
            return

        # 默认欢迎：图片模式
        use_image = _conf("use_image", True)
        if use_image:
            try:
                from utils.bg_helper import find_and_load_bg

                bg_uri = find_and_load_bg("notice", TEMPLATES_DIR)
                img_uri = await _render_welcome_image(
                    "欢迎新人！",
                    bg_uri,
                    group_name=f"群号 {group_id}",
                    title="欢迎新成员",
                    icon="👋",
                )
                msg = Message()
                msg.append(MessageSegment.at(user_id))
                msg.append(MessageSegment.image(img_uri))
                logger.info(f"[群助手] 发送默认图片欢迎: group={group_id}")
                await group_increase.finish(msg)
                return
            except FinishedException:
                return
            except Exception as e:
                logger.warning(f"[群助手] 渲染欢迎图失败，回退到纯文字: {e}")

        # 纯文字默认
        msg = Message()
        msg.append(MessageSegment.at(user_id))
        msg.append(MessageSegment.text(" 欢迎新人！"))
        logger.info(f"[群助手] 发送默认文字欢迎: group={group_id}")
        await group_increase.finish(msg)
    except Exception as e:
        logger.error(f"[群助手] 加群欢迎处理失败: {e}")


# ============================================================
# 退群/踢人通知（修复 Task 11：增强事件匹配）
# ============================================================
group_decrease = on_notice(block=False, priority=10)


@group_decrease.handle()
async def handle_group_decrease(bot: Bot, event):
    """处理退群/踢人事件"""
    if not _is_enabled():
        return

    # 健壮匹配：兼容各协议端
    if not _is_group_decrease_event(event):
        return
    logger.info("[群助手] 匹配到群成员减少事件")

    user_id = getattr(event, "user_id", "")
    group_id = str(getattr(event, "group_id", ""))
    sub_type = getattr(event, "sub_type", "leave")  # "leave" / "kick" / "kick_me"

    if not user_id or not group_id:
        logger.warning(f"[群助手] 退群事件缺少字段: user_id={user_id}, group_id={group_id}")
        return

    logger.info(f"[群助手] 退群事件: user={user_id}, group={group_id}, sub_type={sub_type}")

    try:
        # 退群后可能查不到，用 QQ 号代替
        display_name = f"QQ{user_id}"
        try:
            display_name = await _get_member_display_name(bot, group_id, user_id)
        except Exception:
            pass

        # 判断是退群还是被踢
        if sub_type == "kick":
            operator_id = getattr(event, "operator_id", None)
            op_name = "管理员"
            if operator_id:
                try:
                    op_name = await _get_member_display_name(bot, group_id, operator_id)
                except Exception:
                    pass
            leave_msg = f"{display_name} 被 {op_name} 发往了火星 🛸"
        else:
            leave_msg = f"{display_name} 离开了我们 😢"

        use_image = _conf("use_image", True)
        if use_image:
            try:
                from utils.bg_helper import find_and_load_bg

                bg_uri = find_and_load_bg("notice", TEMPLATES_DIR)
                if sub_type == "kick":
                    img_uri = await _render_welcome_image(
                        f"{display_name}\n被发往火星",
                        bg_uri,
                        group_name="成员被踢出",
                        title="踢出通知",
                        icon="🛸",
                    )
                else:
                    img_uri = await _render_welcome_image(
                        f"{display_name}\n离开了我们",
                        bg_uri,
                        group_name="成员退群",
                        title="退群通知",
                        icon="😢",
                    )
                logger.info(f"[群助手] 发送退群图片通知: group={group_id}, sub_type={sub_type}")
                await group_decrease.finish(MessageSegment.image(img_uri))
                return
            except FinishedException:
                return
            except Exception as e:
                logger.warning(f"[群助手] 渲染退群图失败，回退到纯文字: {e}")

        logger.info(f"[群助手] 发送退群文字通知: group={group_id}, sub_type={sub_type}")
        await group_decrease.finish(Message(leave_msg))
    except Exception as e:
        logger.error(f"[群助手] 退群通知处理失败: {e}")


# ============================================================
# 进群审批（on_request 自动审批）
# ============================================================
group_request = on_request(priority=5, block=False)


@group_request.handle()
async def handle_group_request(bot: Bot, event):
    """处理加群请求：按配置自动审批"""
    if not _is_enabled():
        return

    event_type = type(event).__name__
    logger.info(f"[群助手] 收到请求事件: {event_type}")

    # 仅处理群请求
    if not _is_group_request_event(event):
        return

    # 仅处理加群申请（sub_type=add），不处理群邀请（invite）
    sub_type = getattr(event, "sub_type", "")
    if sub_type != "add":
        return

    group_id = str(getattr(event, "group_id", ""))
    user_id = getattr(event, "user_id", "")
    flag = getattr(event, "flag", "")

    if not group_id or not user_id or not flag:
        logger.warning(
            f"[群助手] 加群请求缺少字段: group={group_id}, user={user_id}, flag={flag}"
        )
        return

    approval = _get_group_approval(group_id)
    if not approval.get("auto_approve", False):
        logger.info(f"[群助手] 群 {group_id} 未开启自动审批，跳过")
        return

    conditions = approval.get("conditions", {})
    passed, reason = await _check_approval_conditions(bot, conditions, user_id)
    logger.info(f"[群助手] 加群审批: group={group_id}, user={user_id}, 通过={passed}, 原因={reason}")

    try:
        if passed:
            await bot.set_group_add_request(flag=str(flag), sub_type="add", approve=True)
            logger.info(f"[群助手] 已自动同意加群请求: group={group_id}, user={user_id}")
        else:
            await bot.set_group_add_request(
                flag=str(flag), sub_type="add", approve=False, reason=f"不符合入群条件：{reason}"
            )
            logger.info(f"[群助手] 已自动拒绝加群请求: group={group_id}, user={user_id}, 原因={reason}")
    except Exception as e:
        logger.error(f"[群助手] 处理加群请求失败: {e}")


# ============================================================
# 违禁词检测（on_message，触发后撤回 + 可选禁言/踢人）
# ============================================================
async def _has_banned_word(bot: Bot, event: GroupMessageEvent) -> bool:
    """规则：消息中是否包含违禁词（管理员/超级用户豁免）"""
    if not _is_enabled():
        return False
    group_id = str(event.group_id)
    bw = _get_group_banned_words(group_id)
    if not bw.get("enabled", False):
        return False
    # 群主/管理员/超级用户不受违禁词约束
    if await _check_admin(bot, event):
        return False
    text = event.get_plaintext()
    if not text:
        return False
    for w in bw.get("words", []):
        if w and w in text:
            return True
    return False


banned_words_matcher = on_message(rule=Rule(_has_banned_word), priority=5, block=True)


@banned_words_matcher.handle()
async def handle_banned_words(bot: Bot, event: GroupMessageEvent):
    """检测到违禁词后的处理：撤回 + 可选禁言/踢人"""
    group_id = event.group_id
    user_id = event.user_id
    message_id = getattr(event, "message_id", None)
    bw = _get_group_banned_words(str(group_id))
    action = bw.get("action", "recall")
    mute_duration = int(bw.get("mute_duration", 600))

    logger.info(
        f"[群助手] 触发违禁词: group={group_id}, user={user_id}, action={action}, msg_id={message_id}"
    )

    # 撤回消息（需要机器人是管理员）
    if message_id:
        if await _is_bot_admin(bot, group_id):
            try:
                await bot.delete_msg(message_id=message_id)
                logger.info(f"[群助手] 已撤回违禁消息: msg_id={message_id}")
            except Exception as e:
                logger.warning(f"[群助手] 撤回消息失败: {e}")
        else:
            logger.warning("[群助手] 机器人非管理员，无法撤回消息")

    # 额外处理：禁言 / 踢人
    if action == "mute":
        if await _is_bot_admin(bot, group_id):
            try:
                await bot.set_group_ban(
                    group_id=group_id, user_id=user_id, duration=mute_duration
                )
                logger.info(f"[群助手] 已禁言 {user_id} {mute_duration}秒")
            except Exception as e:
                logger.warning(f"[群助手] 禁言失败: {e}")
        else:
            logger.warning("[群助手] 机器人非管理员，无法禁言")
    elif action == "kick":
        if await _is_bot_admin(bot, group_id):
            try:
                await bot.set_group_kick(
                    group_id=group_id, user_id=user_id, reject_add_request=False
                )
                logger.info(f"[群助手] 已踢出 {user_id}")
            except Exception as e:
                logger.warning(f"[群助手] 踢人失败: {e}")
        else:
            logger.warning("[群助手] 机器人非管理员，无法踢人")

    # 不再回复，避免二次刷屏（消息已撤回）


# ============================================================
# 管理员指令：设置欢迎语
# ============================================================
welcome_set = on_command(
    "进群欢迎设置",
    aliases={"设置进群欢迎", "欢迎设置"},
    priority=10,
    block=True,
)


@welcome_set.handle()
async def handle_welcome_set(bot: Bot, event, args: Message = CommandArg()):
    """设置群欢迎语（支持文字 + 图片，群聊/私聊均可）"""
    if not _is_enabled():
        return

    is_private = isinstance(event, PrivateMessageEvent)
    is_group = isinstance(event, GroupMessageEvent)

    # 权限检查
    if not await _check_admin(bot, event):
        await welcome_set.finish("❌ 你没有权限使用此指令")
        return

    if is_group:
        group_id = str(event.group_id)
        text_content = args.extract_plain_text().strip()
    else:
        # 私聊：解析群号
        raw_text = args.extract_plain_text().strip()
        match = re.match(r"^(\d{5,12})\s*(.*)$", raw_text)
        if not match:
            await welcome_set.finish(
                "❓ 私聊用法：\n"
                "「进群欢迎设置 群号 文字内容」\n"
                "附带图片会自动保存为欢迎图\n"
                "可用 {name} 代表新成员昵称\n"
                "例：进群欢迎设置 123456789 欢迎 {name}"
            )
            return
        group_id = match.group(1)
        text_content = match.group(2).strip()

    # 提取消息中的图片
    image_urls = []
    for seg in event.message:
        if seg.type == "image":
            url = seg.data.get("url", "")
            if url:
                image_urls.append(url)

    if not text_content and not image_urls:
        if is_group:
            await welcome_set.finish(
                "❓ 用法：\n"
                "「进群欢迎设置 欢迎你来到本群！」\n"
                "附带图片会自动保存为欢迎图\n"
                "可用 {name} 代表新成员昵称"
            )
        else:
            await welcome_set.finish(
                "❓ 私聊用法：\n"
                "「进群欢迎设置 群号 文字内容」\n"
                "附带图片会自动保存为欢迎图\n"
                "可用 {name} 代表新成员昵称\n"
                "例：进群欢迎设置 123456789 欢迎 {name}"
            )
        return

    # 下载图片（只保存第一张）
    saved_img_path = None
    if image_urls:
        saved_img_path = await _download_image(image_urls[0], group_id)

    # 保存配置
    current = _get_group_welcome(group_id)
    if text_content:
        current["text"] = text_content
    if saved_img_path:
        current["image"] = saved_img_path
    _set_group_welcome(group_id, current)

    reply = f"✅ 群 {group_id} 进群欢迎已更新\n"
    if text_content:
        reply += f"📝 文字：{text_content[:50]}{'...' if len(text_content) > 50 else ''}\n"
    if saved_img_path:
        reply += "🖼️ 图片：已保存\n"
    reply += f"（共 {len(_load_welcome_data())} 个群已设置）"

    await welcome_set.finish(reply)


# ============================================================
# 违禁词系列指令
# ============================================================
banned_word_add = on_command("违禁词添加", aliases={"添加违禁词"}, priority=10, block=True)


@banned_word_add.handle()
async def handle_banned_word_add(bot: Bot, event, args: Message = CommandArg()):
    """添加违禁词"""
    if not _is_enabled():
        return
    if not isinstance(event, GroupMessageEvent):
        await banned_word_add.finish("❌ 该指令仅限群聊使用")
        return
    if not await _check_admin(bot, event):
        await banned_word_add.finish("❌ 你没有权限使用此指令")
        return

    word = args.extract_plain_text().strip()
    if not word:
        await banned_word_add.finish("❓ 用法：违禁词添加 词语")
        return

    group_id = str(event.group_id)
    bw = _get_group_banned_words(group_id)
    if word in bw["words"]:
        await banned_word_add.finish(f"⚠️ 违禁词「{word}」已存在")
        return
    bw["words"].append(word)
    _set_group_banned_words(group_id, bw)
    logger.info(f"[群助手] 群 {group_id} 添加违禁词: {word}")
    await banned_word_add.finish(f"✅ 已添加违禁词「{word}」\n当前共 {len(bw['words'])} 个违禁词")


banned_word_del = on_command("违禁词删除", aliases={"删除违禁词"}, priority=10, block=True)


@banned_word_del.handle()
async def handle_banned_word_del(bot: Bot, event, args: Message = CommandArg()):
    """删除违禁词"""
    if not _is_enabled():
        return
    if not isinstance(event, GroupMessageEvent):
        await banned_word_del.finish("❌ 该指令仅限群聊使用")
        return
    if not await _check_admin(bot, event):
        await banned_word_del.finish("❌ 你没有权限使用此指令")
        return

    word = args.extract_plain_text().strip()
    if not word:
        await banned_word_del.finish("❓ 用法：违禁词删除 词语")
        return

    group_id = str(event.group_id)
    bw = _get_group_banned_words(group_id)
    if word not in bw["words"]:
        await banned_word_del.finish(f"⚠️ 违禁词「{word}」不存在")
        return
    bw["words"].remove(word)
    _set_group_banned_words(group_id, bw)
    logger.info(f"[群助手] 群 {group_id} 删除违禁词: {word}")
    await banned_word_del.finish(f"✅ 已删除违禁词「{word}」\n当前共 {len(bw['words'])} 个违禁词")


banned_word_list = on_command("违禁词列表", aliases={"违禁词查看"}, priority=10, block=True)


@banned_word_list.handle()
async def handle_banned_word_list(bot: Bot, event):
    """查看违禁词列表"""
    if not _is_enabled():
        return
    if not isinstance(event, GroupMessageEvent):
        await banned_word_list.finish("❌ 该指令仅限群聊使用")
        return
    if not await _check_admin(bot, event):
        await banned_word_list.finish("❌ 你没有权限使用此指令")
        return

    group_id = str(event.group_id)
    bw = _get_group_banned_words(group_id)
    status = "开启" if bw.get("enabled") else "关闭"
    action = bw.get("action", "recall")
    mute_dur = bw.get("mute_duration", 600)
    words = bw.get("words", [])

    lines = [
        f"📋 群 {group_id} 违禁词配置",
        "━━━━━━━━━━━━",
        f"状态：{status}",
        f"处理方式：{action}" + (f"（{mute_dur}秒）" if action == "mute" else ""),
        f"违禁词数量：{len(words)}",
        "━━━━━━━━━━━━",
    ]
    if words:
        for i, w in enumerate(words, 1):
            lines.append(f"{i}. {w}")
    else:
        lines.append("（暂无违禁词）")

    await banned_word_list.finish("\n".join(lines))


banned_word_switch = on_command("违禁词开关", aliases={"违禁词切换"}, priority=10, block=True)


@banned_word_switch.handle()
async def handle_banned_word_switch(bot: Bot, event, args: Message = CommandArg()):
    """开启/关闭违禁词检测"""
    if not _is_enabled():
        return
    if not isinstance(event, GroupMessageEvent):
        await banned_word_switch.finish("❌ 该指令仅限群聊使用")
        return
    if not await _check_admin(bot, event):
        await banned_word_switch.finish("❌ 你没有权限使用此指令")
        return

    group_id = str(event.group_id)
    bw = _get_group_banned_words(group_id)
    arg = args.extract_plain_text().strip().lower()
    if arg in ("开", "on", "1", "true"):
        bw["enabled"] = True
    elif arg in ("关", "off", "0", "false"):
        bw["enabled"] = False
    else:
        # 无参数则切换
        bw["enabled"] = not bw.get("enabled", False)
    _set_group_banned_words(group_id, bw)
    status = "开启" if bw["enabled"] else "关闭"
    logger.info(f"[群助手] 群 {group_id} 违禁词检测已{status}")
    await banned_word_switch.finish(f"✅ 违禁词检测已{status}")


banned_word_action = on_command("违禁词处理", aliases={"违禁词动作"}, priority=10, block=True)


@banned_word_action.handle()
async def handle_banned_word_action(bot: Bot, event, args: Message = CommandArg()):
    """设置触发违禁词后的处理方式：撤回 / 禁言 [时长] / 踢人"""
    if not _is_enabled():
        return
    if not isinstance(event, GroupMessageEvent):
        await banned_word_action.finish("❌ 该指令仅限群聊使用")
        return
    if not await _check_admin(bot, event):
        await banned_word_action.finish("❌ 你没有权限使用此指令")
        return

    group_id = str(event.group_id)
    bw = _get_group_banned_words(group_id)
    raw = args.extract_plain_text().strip().split()
    if not raw:
        await banned_word_action.finish(
            "❓ 用法：\n"
            "违禁词处理 撤回\n"
            "违禁词处理 禁言 [时长，如 10m/1h/1d]\n"
            "违禁词处理 踢人"
        )
        return

    mode = raw[0].lower()
    if mode in ("撤回", "recall"):
        bw["action"] = "recall"
    elif mode in ("禁言", "mute"):
        bw["action"] = "mute"
        if len(raw) >= 2:
            dur = _parse_duration(raw[1])
            if dur > 0:
                bw["mute_duration"] = dur
    elif mode in ("踢人", "kick"):
        bw["action"] = "kick"
    else:
        await banned_word_action.finish(
            "❌ 未知处理方式，可选：撤回 / 禁言 / 踢人"
        )
        return
    _set_group_banned_words(group_id, bw)
    action = bw["action"]
    extra = f"（{bw['mute_duration']}秒）" if action == "mute" else ""
    logger.info(f"[群助手] 群 {group_id} 违禁词处理方式: {action}{extra}")
    await banned_word_action.finish(f"✅ 违禁词处理方式已设为「{action}」{extra}")


# ============================================================
# 禁言 / 解禁 / 踢人 / 撤回 指令
# ============================================================
mute_cmd = on_command("禁言", aliases={"mute"}, priority=10, block=True)


@mute_cmd.handle()
async def handle_mute(bot: Bot, event, args: Message = CommandArg()):
    """禁言 @用户 时长（1m/1h/1d）"""
    if not _is_enabled():
        return
    if not isinstance(event, GroupMessageEvent):
        await mute_cmd.finish("❌ 该指令仅限群聊使用")
        return
    if not await _check_admin(bot, event):
        await mute_cmd.finish("❌ 你没有权限使用此指令")
        return

    group_id = event.group_id
    if not await _is_bot_admin(bot, group_id):
        await mute_cmd.finish("❌ 机器人非群管理员，无法执行禁言操作")
        return

    # 提取 @ 用户（排除机器人自身）
    at_users = [u for u in _extract_at_users(event) if u != str(bot.self_id)]
    if not at_users:
        await mute_cmd.finish("❓ 用法：禁言 @用户 时长\n（时长支持 1m/1h/1d 格式）")
        return

    raw = args.extract_plain_text().strip().split()
    duration = 600
    if raw:
        duration = _parse_duration(raw[0])
        if duration <= 0:
            duration = 600
    if duration > 30 * 86400:
        duration = 30 * 86400  # 上限 30 天

    success, failed = 0, 0
    for uid in at_users:
        try:
            await bot.set_group_ban(group_id=group_id, user_id=int(uid), duration=duration)
            success += 1
        except Exception as e:
            logger.warning(f"[群助手] 禁言 {uid} 失败: {e}")
            failed += 1

    logger.info(f"[群助手] 禁言完成: group={group_id}, 成功={success}, 失败={failed}, 时长={duration}秒")
    await mute_cmd.finish(f"✅ 禁言完成（成功 {success}，失败 {failed}），时长 {duration} 秒")


unmute_cmd = on_command("解禁", aliases={"unmute", "解除禁言"}, priority=10, block=True)


@unmute_cmd.handle()
async def handle_unmute(bot: Bot, event):
    """解除 @用户 的禁言"""
    if not _is_enabled():
        return
    if not isinstance(event, GroupMessageEvent):
        await unmute_cmd.finish("❌ 该指令仅限群聊使用")
        return
    if not await _check_admin(bot, event):
        await unmute_cmd.finish("❌ 你没有权限使用此指令")
        return

    group_id = event.group_id
    if not await _is_bot_admin(bot, group_id):
        await unmute_cmd.finish("❌ 机器人非群管理员，无法执行解禁操作")
        return

    at_users = [u for u in _extract_at_users(event) if u != str(bot.self_id)]
    if not at_users:
        await unmute_cmd.finish("❓ 用法：解禁 @用户")
        return

    success, failed = 0, 0
    for uid in at_users:
        try:
            await bot.set_group_ban(group_id=group_id, user_id=int(uid), duration=0)
            success += 1
        except Exception as e:
            logger.warning(f"[群助手] 解禁 {uid} 失败: {e}")
            failed += 1

    logger.info(f"[群助手] 解禁完成: group={group_id}, 成功={success}, 失败={failed}")
    await unmute_cmd.finish(f"✅ 解禁完成（成功 {success}，失败 {failed}）")


kick_cmd = on_command("踢人", aliases={"kick"}, priority=10, block=True)


@kick_cmd.handle()
async def handle_kick(bot: Bot, event):
    """踢出 @用户"""
    if not _is_enabled():
        return
    if not isinstance(event, GroupMessageEvent):
        await kick_cmd.finish("❌ 该指令仅限群聊使用")
        return
    if not await _check_admin(bot, event):
        await kick_cmd.finish("❌ 你没有权限使用此指令")
        return

    group_id = event.group_id
    if not await _is_bot_admin(bot, group_id):
        await kick_cmd.finish("❌ 机器人非群管理员，无法执行踢人操作")
        return

    at_users = [u for u in _extract_at_users(event) if u != str(bot.self_id)]
    if not at_users:
        await kick_cmd.finish("❓ 用法：踢人 @用户")
        return

    success, failed = 0, 0
    for uid in at_users:
        try:
            await bot.set_group_kick(
                group_id=group_id, user_id=int(uid), reject_add_request=False
            )
            success += 1
        except Exception as e:
            logger.warning(f"[群助手] 踢人 {uid} 失败: {e}")
            failed += 1

    logger.info(f"[群助手] 踢人完成: group={group_id}, 成功={success}, 失败={failed}")
    await kick_cmd.finish(f"✅ 踢人完成（成功 {success}，失败 {failed}）")


recall_cmd = on_command("撤回", aliases={"recall"}, priority=10, block=True)


@recall_cmd.handle()
async def handle_recall(bot: Bot, event):
    """撤回被引用的消息"""
    if not _is_enabled():
        return
    if not isinstance(event, GroupMessageEvent):
        await recall_cmd.finish("❌ 该指令仅限群聊使用")
        return
    if not await _check_admin(bot, event):
        await recall_cmd.finish("❌ 你没有权限使用此指令")
        return

    group_id = event.group_id
    if not await _is_bot_admin(bot, group_id):
        await recall_cmd.finish("❌ 机器人非群管理员，无法执行撤回操作")
        return

    # 优先从 event.reply 取被引用消息 ID
    target_msg_id = None
    reply = getattr(event, "reply", None)
    if reply is not None:
        target_msg_id = getattr(reply, "message_id", None)

    # 回退：从消息 reply 段解析
    if not target_msg_id:
        for seg in event.message:
            if seg.type == "reply":
                target_msg_id = seg.data.get("id")
                if target_msg_id:
                    break

    if not target_msg_id:
        await recall_cmd.finish("❓ 请引用要撤回的消息后发送「撤回」")
        return

    try:
        await bot.delete_msg(message_id=int(target_msg_id))
        logger.info(f"[群助手] 已撤回消息: msg_id={target_msg_id}")
        await recall_cmd.finish("✅ 已撤回该消息")
    except Exception as e:
        logger.error(f"[群助手] 撤回消息失败: {e}")
        await recall_cmd.finish(f"❌ 撤回失败：{e}")


# ============================================================
# 进群审批配置指令
# ============================================================
approval_set = on_command("群助手审批", aliases={"进群审批设置"}, priority=10, block=True)


@approval_set.handle()
async def handle_approval_set(bot: Bot, event, args: Message = CommandArg()):
    """设置自动审批条件并开启 / 查看 / 清除"""
    if not _is_enabled():
        return
    if not await _check_admin(bot, event):
        await approval_set.finish("❌ 你没有权限使用此指令")
        return

    raw = args.extract_plain_text().strip()

    # 解析群号
    is_group = isinstance(event, GroupMessageEvent)
    if is_group:
        group_id = str(event.group_id)
    else:
        # 私聊：第一个数字串为群号
        match = re.match(r"^(\d{5,12})\s*(.*)$", raw)
        if not match:
            await approval_set.finish(
                "❓ 私聊用法：\n"
                "群助手审批 群号 年龄<30 性别=男 等级>5\n"
                "群助手审批 群号 查看\n"
                "群助手审批 群号 清除"
            )
            return
        group_id = match.group(1)
        raw = match.group(2).strip()

    arg_lower = raw.lower()

    # 查看当前配置
    if not raw or arg_lower in ("查看", "view", "状态"):
        ap = _get_group_approval(group_id)
        conds = ap.get("conditions", {})
        status = "开启" if ap.get("auto_approve") else "关闭"
        lines = [
            f"📋 群 {group_id} 进群审批配置",
            "━━━━━━━━━━━━",
            f"自动审批：{status}",
            f"年龄限制：最小 {conds.get('age_min', 0)} / 最大 {conds.get('age_max', 0)}（0=不限制）",
            f"性别限制：{conds.get('gender', '') or '不限'}",
            f"等级限制：最低 {conds.get('level_min', 0)}（0=不限制）",
            "━━━━━━━━━━━━",
            "设置示例：群助手审批 年龄<30 性别=男 等级>5",
        ]
        await approval_set.finish("\n".join(lines))
        return

    # 清除条件
    if arg_lower in ("清除", "清空", "clear", "reset"):
        ap = {
            "auto_approve": False,
            "conditions": {"age_max": 0, "age_min": 0, "gender": "", "level_min": 0},
        }
        _set_group_approval(group_id, ap)
        logger.info(f"[群助手] 群 {group_id} 审批配置已清除")
        await approval_set.finish("✅ 已清除审批条件并关闭自动审批")
        return

    # 关闭
    if arg_lower in ("关", "off", "关闭"):
        ap = _get_group_approval(group_id)
        ap["auto_approve"] = False
        _set_group_approval(group_id, ap)
        logger.info(f"[群助手] 群 {group_id} 自动审批已关闭")
        await approval_set.finish("✅ 自动审批已关闭")
        return

    # 解析条件并开启
    _, conditions = _parse_approval_conditions(raw)
    ap = {"auto_approve": True, "conditions": conditions}
    _set_group_approval(group_id, ap)
    logger.info(f"[群助手] 群 {group_id} 审批条件已设置并开启: {conditions}")

    cond_desc = []
    if conditions.get("age_min", 0):
        cond_desc.append(f"年龄>{conditions['age_min']}")
    if conditions.get("age_max", 0):
        cond_desc.append(f"年龄<{conditions['age_max']}")
    if conditions.get("gender", ""):
        cond_desc.append(f"性别={conditions['gender']}")
    if conditions.get("level_min", 0):
        cond_desc.append(f"等级>{conditions['level_min']}")

    desc = "、".join(cond_desc) if cond_desc else "无条件限制（默认同意所有申请）"
    await approval_set.finish(f"✅ 群 {group_id} 自动审批已开启\n条件：{desc}")


approval_switch = on_command("群助手审批开关", aliases={"审批开关"}, priority=10, block=True)


@approval_switch.handle()
async def handle_approval_switch(bot: Bot, event, args: Message = CommandArg()):
    """开启/关闭自动审批"""
    if not _is_enabled():
        return
    if not await _check_admin(bot, event):
        await approval_switch.finish("❌ 你没有权限使用此指令")
        return

    raw = args.extract_plain_text().strip().split()
    is_group = isinstance(event, GroupMessageEvent)

    if is_group:
        group_id = str(event.group_id)
        toggle = raw[0] if raw else ""
    else:
        # 私聊：群号 开/关
        if len(raw) < 2:
            await approval_switch.finish("❓ 私聊用法：群助手审批开关 群号 开|关")
            return
        group_id = raw[0]
        toggle = raw[1]

    toggle = toggle.lower()
    ap = _get_group_approval(group_id)
    if toggle in ("开", "on", "1", "true"):
        ap["auto_approve"] = True
    elif toggle in ("关", "off", "0", "false"):
        ap["auto_approve"] = False
    else:
        # 无明确参数则切换
        ap["auto_approve"] = not ap.get("auto_approve", False)
    _set_group_approval(group_id, ap)
    status = "开启" if ap["auto_approve"] else "关闭"
    logger.info(f"[群助手] 群 {group_id} 自动审批已{status}")
    await approval_switch.finish(f"✅ 群 {group_id} 自动审批已{status}")


logger.info(
    f"[群助手] 插件已加载 (enabled={_is_enabled()}, use_image={_conf('use_image', True)})"
)
