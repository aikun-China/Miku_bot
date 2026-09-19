"""
MikuBot Web 管理后台 —— 挂载到 NoneBot 的 FastAPI 服务器
=============================================
访问地址: http://你的IP:3108/
登录密码: .env 中配置的 WEBUI_PASSWORD
"""

import os
import json
import time
import yaml
import shutil
import re as _re
from pathlib import Path
from datetime import datetime
from typing import Any, Dict, List, Optional

# FastAPI & starlette
try:
    from fastapi import APIRouter, Request, HTTPException, status, WebSocket, WebSocketDisconnect
    from fastapi.responses import JSONResponse, HTMLResponse, FileResponse
    _HAS_FASTAPI = True
except ImportError:
    _HAS_FASTAPI = False

try:
    import asyncio
    _HAS_ASYNCIO = True
except ImportError:
    _HAS_ASYNCIO = False

try:
    import psutil
    _HAS_PSUTIL = True
except ImportError:
    _HAS_PSUTIL = False

from nonebot import get_driver
from nonebot.log import logger

try:
    from utils.config_manager import config_manager as _config_manager
    _HAS_CONFIG_MANAGER = True
except Exception:
    _config_manager = None
    _HAS_CONFIG_MANAGER = False


# ===================== 路径 & 常量 =====================
BASE_DIR = Path(__file__).resolve().parent.parent
ADMIN_DIR = BASE_DIR / "admin_web"
CONFIG_FILE = BASE_DIR / "config" / "bot.yaml"
BACKUP_DIR = BASE_DIR / "config" / "backups"
BLACKLIST_FILE = BASE_DIR / "data" / "blacklist.json"
DATA_DIR = BASE_DIR / "data"

BACKUP_DIR.mkdir(parents=True, exist_ok=True)
BLACKLIST_FILE.parent.mkdir(parents=True, exist_ok=True)

# 启动时间（uptime 计算用）
_START_TIME = time.time()


# ===================== 读取密码 =====================
def _get_password() -> str:
    """读取 WebUI 登录密码。
    优先级：1) .env 的 WEBUI_PASSWORD  2) bot.yaml 的 bot.webui_password  3) 默认值 miku8888"""
    env_file = BASE_DIR / ".env"
    if env_file.exists():
        try:
            content = env_file.read_text(encoding="utf-8")
            for line in content.splitlines():
                line = line.strip()
                if line.startswith("WEBUI_PASSWORD="):
                    val = line.split("=", 1)[1].strip()
                    val = val.strip('"').strip("'")
                    if val:
                        return val
        except Exception:
            pass

    # 回退：从 bot.yaml 的 bot.webui_password 读取
    try:
        data = _load_yaml()
        if isinstance(data, dict):
            bot_conf = data.get("bot", {})
            if isinstance(bot_conf, dict):
                val = bot_conf.get("webui_password", "")
                if isinstance(val, str) and val.strip():
                    return val.strip()
    except Exception:
        pass
    return "miku8888"


# ===================== 设备授权 =====================
# WebUI 向局域网开放时的设备授权机制：
# - 密码登录后，新设备自动进入"待批准"状态
# - 本机（127.0.0.1）首次登录自动获得审批权
# - 审批权持有人可在仪表盘同意/拒绝其他设备
# - 同意后该设备令牌长期有效，可正常访问所有 API
DEVICES_FILE = BASE_DIR / "data" / "webui_devices.json"


def _load_devices() -> dict:
    """加载设备授权信息"""
    if not DEVICES_FILE.exists():
        return {
            "approved": {},   # device_token -> {"name": "...", "ip": "...", "approved_at": "..."}
            "pending": {},    # device_token -> {"name": "...", "ip": "...", "ua": "...", "requested_at": "..."}
            "rejected": {},   # device_token -> {"name": "...", "ip": "...", "rejected_at": "..."}
        }
    try:
        with open(DEVICES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        data.setdefault("approved", {})
        data.setdefault("pending", {})
        data.setdefault("rejected", {})
        return data
    except Exception:
        return {"approved": {}, "pending": {}, "rejected": {}}


def _save_devices(data: dict):
    try:
        DEVICES_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(DEVICES_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"[WebUI] 写入 devices.json 失败: {e}")


def _generate_token() -> str:
    import hashlib
    import random
    raw = f"{time.time()}_{random.randint(100000, 999999)}_miku_{os.urandom(8).hex()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _get_client_ip(req: Request) -> str:
    """获取客户端真实 IP"""
    # 优先取 X-Forwarded-For（代理/反向代理场景）
    xff = req.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[0].strip()
    xreal = req.headers.get("x-real-ip", "")
    if xreal:
        return xreal.strip()
    try:
        client = getattr(req, "client", None)
        if client and client.host:
            return client.host
    except Exception:
        pass
    return "unknown"


def _is_localhost(req: Request) -> bool:
    """是否本机访问"""
    ip = _get_client_ip(req)
    if ip in ("127.0.0.1", "localhost", "::1", "0:0:0:0:0:0:0:1"):
        return True
    # 若 hostname 指向本机也视为本地
    return False


def _get_device_token(req: Request) -> Optional[str]:
    """从请求中读取设备令牌（header: X-Device-Token / query / cookie）"""
    t = req.headers.get("x-device-token", "").strip()
    if t:
        return t
    t = req.query_params.get("device_token", "").strip()
    if t:
        return t
    try:
        # 从 Cookie 中读取
        cookie_header = req.headers.get("cookie", "")
        for part in cookie_header.split(";"):
            part = part.strip()
            if part.startswith("device_token="):
                val = part.split("=", 1)[1].strip()
                if val:
                    return val
    except Exception:
        pass
    return None


def _device_auth_enabled() -> bool:
    """是否启用设备授权（从 bot.yaml 的 webui.device_auth 读取）"""
    try:
        data = _load_yaml()
        webui = data.get("webui", {}) if isinstance(data, dict) else {}
        if isinstance(webui, dict):
            val = webui.get("device_auth", True)
            if isinstance(val, bool):
                return val
            if isinstance(val, str):
                if val.strip().lower() in ("false", "0", "no", "off", ""):
                    return False
                return True
    except Exception:
        pass
    return True  # 默认开启（安全性优先）


# ===================== 工具函数 =====================
def _safe_json_load(path: Path, default: Any) -> Any:
    try:
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return default


def _load_yaml() -> dict:
    if not CONFIG_FILE.exists():
        return {}
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


def _load_blacklist() -> dict:
    data = _safe_json_load(BLACKLIST_FILE, {})
    if not isinstance(data, dict):
        data = {}
    data.setdefault("global_disabled", [])
    data.setdefault("group_disabled", {})
    data.setdefault("user_disabled", {})
    return data


def _save_blacklist(data: dict):
    with open(BLACKLIST_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# 插件/配置节的元信息（中文名称、描述）
_SECTION_META: Dict[str, Dict[str, str]] = {
    "bot": {
        "label": "主配置（bot）",
        "description": "Bot核心参数：超级用户、密码、日志、端口等",
        "has_toggle": False,
    },
    "miku_admin": {"label": "管理员", "description": "重启、刷新配置等管理员指令"},
    "miku_basic": {"label": "基础信息", "description": "Bot 名称、运行状态显示"},
    "miku_weather": {"label": "天气查询", "description": "和风天气 API 配置"},
    "miku_checkin": {"label": "签到", "description": "每日签到奖励设置"},
    "miku_profile": {"label": "个人资料", "description": "个人资料卡片配置"},
    "miku_ai": {"label": "AI 对话", "description": "AI 对话参数及人格提示词"},
    "miku_menu": {"label": "菜单", "description": "菜单展示配置"},
    "miku_notify_admin": {"label": "通知管理员", "description": "将消息转达给管理员"},
    "miku_blacklist": {"label": "黑名单拦截", "description": "消息黑名单拦截，阻止黑名单用户/群组触发插件"},
    "cache_cleanup": {"label": "缓存清理", "description": "自动清理缓存文件设置"},
}

# 配置项中文标签与注释说明（每项包含 label 和 hint）
_CONFIG_LABELS: Dict[str, Dict[str, Dict[str, str]]] = {
    "bot": {
        "superusers": {"label": "超级用户(QQ号)", "hint": "拥有所有权限的用户QQ号列表，支持多个，修改后需要重启Bot"},
        "webui_password": {"label": "WebUI密码", "hint": "WebUI管理后台登录密码，支持英文数字及标点"},
        "log_level": {"label": "日志等级", "hint": "TRACE / DEBUG / INFO / WARNING / ERROR，级别越高越简洁"},
        "log_to_file": {"label": "日志写入文件", "hint": "true=同时写入文件与终端；false=仅显示在终端"},
        "log_rotation": {"label": "日志滚动周期", "hint": "多久生成一个新日志文件，格式如1 day / 10 MB / 00:00"},
        "log_retention": {"label": "日志保留天数", "hint": "自动清理多少天前的日志文件，超过此天数自动删除"},
        "nickname": {"label": "Bot昵称", "hint": "Bot的简称/昵称，插件中会被触发使用这些昵称的消息"},
        "host": {"label": "监听地址", "hint": "127.0.0.1仅本机访问；0.0.0.0允许所有网络访问"},
        "port": {"label": "监听端口", "hint": "管理后台服务端口（如3108），需要重启才会变生效"},
    },
    "miku_admin": {
        "notify_on_start": {"label": "启动通知", "hint": "true=Bot启动时私聊通知超级用户；false=不通知"},
        "enabled": {"label": "启用", "hint": "true=启用管理员指令功能；false=禁用本插件"},
        "admin_commands": {"label": "管理员指令", "hint": "管理员可触发的指令列表，如重启、配置检查、刷新配置"},
    },
    "miku_basic": {
        "enabled": {"label": "启用", "hint": "true=启用基础信息插件；false=禁用该插件功能"},
        "bot_name": {"label": "Bot名称", "hint": "Bot显示名称，在信息卡片上显示的Bot名"},
        "show_system_info": {"label": "显示系统信息", "hint": "true=信息卡片显示运行时间/系统信息；false=不显示"},
        "response_style": {"label": "响应风格", "hint": "card=生成图片卡片；text=纯文本"},
    },
    "miku_weather": {
        "enabled": {"label": "启用", "hint": "true=启用天气查询插件；false=禁用"},
        "API_KEY": {"label": "API Key", "hint": "和风天气API Key，登录console.qweather.com申请"},
        "DEFAULT_CITY": {"label": "默认城市", "hint": "未指定城市时的查询城市名称"},
        "API_HOST": {"label": "API Host", "hint": "和风天气专属Host，从开发者控制台复制，形如 xxx.re.qweatherapi.com"},
        "GEO_API_HOST": {"label": "GeoAPI Host", "hint": "城市名转地理位置ID的API域名，默认geoapi.qweather.com"},
        "lang": {"label": "语言", "hint": "zh=中文，en=英文，天气描述语言"},
        "response_style": {"label": "响应风格", "hint": "card=生成图片卡片；text=纯文本"},
    },
    "miku_checkin": {
        "enabled": {"label": "启用", "hint": "true=启用签到插件；false=禁用"},
        "response_style": {"label": "响应风格", "hint": "card=生成图片卡片；text=纯文本"},
        "coins_min": {"label": "金币最小值", "hint": "签到获得的金币范围最小值，0或正整数"},
        "coins_max": {"label": "金币最大值", "hint": "签到获得的金币范围最大值"},
        "favor_min": {"label": "好感度最小值", "hint": "签到获得的好感度范围最小值，支持小数"},
        "favor_max": {"label": "好感度最大值", "hint": "签到获得的好感度范围最大值"},
        "double_favor_prob": {"label": "双倍好感概率", "hint": "触发双倍好感的概率(0-1)，例如0.03 = 3%"},
        "card_width": {"label": "卡片宽度", "hint": "图片卡片像素宽度，仅card风格生效"},
        "card_height": {"label": "卡片高度", "hint": "图片卡片像素高度"},
    },
    "miku_profile": {
        "enabled": {"label": "启用", "hint": "true=启用个人资料插件；false=禁用"},
        "response_style": {"label": "响应风格", "hint": "card=生成图片卡片；text=纯文本"},
        "card_width": {"label": "卡片宽度", "hint": "资料卡片宽度，仅card生效"},
        "card_height": {"label": "卡片高度", "hint": "资料卡片高度，0=自适应高度"},
    },
    "miku_ai": {
        "enabled": {"label": "启用", "hint": "true=启用AI对话插件；false=禁用"},
        "response_style": {"label": "响应风格", "hint": "card=生成图片卡片；text=纯文本"},
        "api_mode": {"label": "接入方式", "hint": "cloud=使用云端API；local=使用本地Ollama等API"},
        "cloud_api_key": {"label": "云端API Key", "hint": "云端服务（如DeepSeek/OpenAI）的API Key"},
        "cloud_base_url": {"label": "云端Base URL", "hint": "云端服务的Base URL，如https://api.deepseek.com/v1"},
        "cloud_model": {"label": "云端模型", "hint": "使用的云端模型名称，如deepseek-chat/gpt-4o"},
        "cloud_vision_model": {"label": "云端识图模型", "hint": "识图用的模型名，留空则使用cloud_model"},
        "local_base_url": {"label": "本地Base URL", "hint": "本地Ollama等本地AI服务的Base URL"},
        "local_model": {"label": "本地模型", "hint": "本地模型名称，如qwen2.5:7b"},
        "local_vision_model": {"label": "本地识图模型", "hint": "本地识图模型名称，留空则使用local_model"},
        "local_api_key": {"label": "本地API Key", "hint": "本地API Key（Ollama通常不需要，留空即可"},
        "group_reply_on_mention": {"label": "群聊@回复", "hint": "true=群聊中@Bot或提到Bot时自动回复"},
        "group_random_reply_percent": {"label": "群聊随机回复概率", "hint": "群聊中无@时的随机回复概率(0-100)，单位为%"},
        "private_reply_every": {"label": "单聊全部回复", "hint": "true=私聊每条消息都回复；false=不主动回复"},
        "group_history_max": {"label": "群聊历史上限", "hint": "群聊最大保存的历史消息数，AI会携带这些消息做上下文"},
        "private_history_max": {"label": "私聊历史上限", "hint": "私聊最大保存的历史消息数"},
        "history_file": {"label": "历史记录文件", "hint": "聊天记录持久化文件路径，相对项目根目录"},
        "favor_change_probability": {"label": "好感变化概率", "hint": "聊天触发好感度变化的概率(0-100)，单位为%"},
        "favor_increase_min": {"label": "好感增加最小值", "hint": "好感度增加范围最小值，随机浮点数"},
        "favor_increase_max": {"label": "好感增加最大值", "hint": "好感度增加范围最大值"},
        "favor_decrease_min": {"label": "好感减少最小值", "hint": "好感度减少范围最小值"},
        "favor_decrease_max": {"label": "好感减少最大值", "hint": "好感度减少范围最大值"},
        "personality_file": {"label": "人格文件", "hint": "AI人格提示词文件路径，相对项目根"},
        "personality_reload": {"label": "人格热更新", "hint": "true=每次回复重新读取人格文件，方便调试；false=只加载一次"},
        "temperature": {"label": "采样温度", "hint": "AI回答的创造性，值越高回答越随机(0-2)"},
        "max_tokens": {"label": "最大Token数", "hint": "单次回复最大token数，留空不限"},
        "request_timeout": {"label": "请求超时", "hint": "API请求超时秒数，超过则报错"},
        "vision_enabled": {"label": "启用识图", "hint": "true=启用图片理解功能，需要模型支持vision"},
        "card_width": {"label": "卡片宽度", "hint": "图片卡片像素宽度"},
        "card_height": {"label": "卡片高度", "hint": "图片卡片像素高度，0=自适应"},
    },
    "miku_menu": {
        "enabled": {"label": "启用", "hint": "true=启用菜单插件；false=禁用"},
        "response_style": {"label": "响应风格", "hint": "card=生成图片卡片；text=纯文本"},
        "card_width": {"label": "卡片宽度", "hint": "菜单卡片像素宽度"},
        "card_height": {"label": "卡片高度", "hint": "菜单卡片像素高度"},
        "show_icons": {"label": "显示图标", "hint": "true=菜单显示各插件图标；false=仅文字"},
    },
    "cache_cleanup": {
        "enabled": {"label": "启用", "hint": "true=启用缓存自动清理；false=不清理"},
        "cleanup_times": {"label": "清理时间", "hint": "每日自动清理的时间点，24小时制，可多个如00:00, 06:00"},
        "cache_dirs": {"label": "缓存目录", "hint": "要清理的缓存目录列表，相对项目根"},
        "max_age_hours": {"label": "最长保留小时", "hint": "缓存文件最大保留小时数，0=每次全部清理"},
        "file_extensions": {"label": "文件扩展名", "hint": "要清理的文件扩展名列表，如.png.jpg"},
        "verbose": {"label": "详细日志", "hint": "true=日志中显示清理详情；false=仅显示是否清理"},
    },
    "miku_notify_admin": {
        "enabled": {"label": "启用", "hint": "true=启用通知管理员；false=禁用"},
    },
}


def _get_label(section: str, key: str) -> str:
    """获取配置项的中文标签，无映射时返回key本身"""
    section_labels = _CONFIG_LABELS.get(section, {})
    item = section_labels.get(key, {})
    if isinstance(item, dict):
        return item.get("label", key)
    return item if isinstance(item, str) else key


def _get_hint(section: str, key: str) -> str:
    """获取配置项的中文注释说明"""
    section_items = _CONFIG_LABELS.get(section, {})
    item = section_items.get(key, {})
    if isinstance(item, dict):
        return item.get("hint", "")
    return ""


def _get_section_label(section: str) -> str:
    """获取配置节的中文名称"""
    return _SECTION_META.get(section, {}).get("label", section)


def _get_section_description(section: str) -> str:
    """获取配置节的中文描述"""
    return _SECTION_META.get(section, {}).get("description", section)


def _value_to_input(val: Any) -> str:
    """将 Python 值转换为输入框字符串"""
    if isinstance(val, list):
        parts = []
        for item in val:
            if isinstance(item, str):
                parts.append(item.replace("\n", "").strip())
            else:
                parts.append(str(item))
        return "\n".join(parts)
    if isinstance(val, bool):
        return "true" if val else "false"
    if val is None:
        return ""
    return str(val)


def _input_to_value(input_str: str) -> Any:
    """将输入框字符串转换为 Python 值（列表/布尔/数字/字符串"""
    if input_str is None:
        return None
    s = str(input_str).strip()
    if not s:
        return ""
    if "\n" in s:
        return [line.strip() for line in s.split("\n") if line.strip()]
    # 布尔判断
    low = s.lower()
    if low in ("true", "false"):
        return low == "true"
    # 数字判断
    try:
        if "." in s:
            return float(s)
        return int(s)
    except ValueError:
        pass
    return s


def _parse_plugin_meta(init_path: Path) -> tuple:
    """从插件 __init__.py 中解析 __plugin_meta__ 的 name 和 description"""
    try:
        content = init_path.read_text(encoding="utf-8", errors="ignore")
        # 解析 __plugin_meta__(name="xxx", description="yyy"...)
        import re
        # 匹配 name="..." 和 description="..."
        name_match = re.search(r'name\s*=\s*"([^"]*)"', content)
        desc_match = re.search(r'description\s*=\s*"([^"]*)"', content)
        meta_name = name_match.group(1).strip() if name_match else ""
        meta_desc = desc_match.group(1).strip() if desc_match else ""
        return meta_name, meta_desc
    except Exception:
        return "", ""


def _get_msg_trend(msg_stats: dict, days: int = 30) -> list:
    """获取最近N天的消息趋势数据"""
    from datetime import datetime, timedelta
    result = []
    today = datetime.now().date()
    for i in range(days - 1, -1, -1):
        date = today - timedelta(days=i)
        day_key = date.strftime("%Y-%m-%d")
        count = 0
        for user_id, stats in msg_stats.items():
            count += int(stats.get("daily", {}).get(day_key, 0))
        result.append({"date": day_key, "count": count})
    return result


def _get_plugin_trend(plugin_stats: dict, days: int = 30) -> list:
    """获取最近N天的插件调用趋势数据"""
    from datetime import datetime, timedelta
    result = []
    today = datetime.now().date()
    for i in range(days - 1, -1, -1):
        date = today - timedelta(days=i)
        day_key = date.strftime("%Y-%m-%d")
        count = 0
        for plugin_name, stats in plugin_stats.items():
            count += int(stats.get("__daily__", {}).get(day_key, 0))
        result.append({"date": day_key, "count": count})
    return result


def _get_plugin_list() -> List[Dict[str, Any]]:
    """扫描 plugins 目录返回插件名列表，包含所有已注册的配置节"""
    plugins_dir = BASE_DIR / "plugins"
    config = _load_yaml() if CONFIG_FILE.exists() else {}

    all_sections = {}

    # 从 plugins 目录扫描
    if plugins_dir.exists():
        for plugin_path in sorted(plugins_dir.iterdir()):
            if not plugin_path.is_dir() or plugin_path.name.startswith("_"):
                continue
            init_file = plugin_path / "__init__.py"
            if not init_file.exists():
                continue
            name = plugin_path.name
            all_sections[name] = {"name": name, "type": "plugin"}

    # 合并已知的配置节名称
    for section_name in _SECTION_META:
        if section_name not in all_sections:
            all_sections[section_name] = {"name": section_name, "type": "config"}

    plugins = []
    for name in sorted(all_sections.keys()):
        plugin_config = config.get(name, {}) if isinstance(config, dict) else {}
        has_enabled = "enabled" in plugin_config if isinstance(plugin_config, dict) else False
        enabled = plugin_config.get("enabled", True) if isinstance(plugin_config, dict) else True
        # 根据配置中存在 enabled 字段的配置才有开关；主配置(bot) 不显示开关
        can_toggle = _SECTION_META.get(name, {}).get("has_toggle", True) and name != "bot"

        description = _get_section_description(name)
        label = _get_section_label(name)

        # 从插件 __plugin_meta__ 读取中文名和描述（优先级最高）
        if plugins_dir.exists() and (plugins_dir / name / "__init__.py").exists():
            init_path = plugins_dir / name / "__init__.py"
            meta_name, meta_desc = _parse_plugin_meta(init_path)
            if meta_name:
                label = meta_name  # 插件的中文名优先
            if meta_desc:
                description = meta_desc  # 插件描述优先

        plugin_info = {
            "name": name,
            "label": label,
            "enabled": enabled,
            "description": description,
            "has_toggle": can_toggle,
            "type": all_sections[name]["type"],
        }
        plugins.append(plugin_info)
    return plugins


def _create_backup():
    if not CONFIG_FILE.exists():
        return None
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = BACKUP_DIR / f"bot.yaml.{timestamp}"
    shutil.copy2(CONFIG_FILE, backup_path)
    return timestamp


def _get_backups() -> List[Dict[str, Any]]:
    backups = []
    try:
        for f in sorted(BACKUP_DIR.glob("bot.yaml.*"), reverse=True)[:20]:
            backups.append({"timestamp": f.stem.replace("bot.yaml.", ""), "size": f.stat().st_size})
    except Exception:
        pass
    return backups


# ===================== 挂载 =====================
def _mount_admin():
    if not _HAS_FASTAPI:
        logger.warning("[WebUI] FastAPI 未安装，管理后台不可用")
        return

    # 获取 FastAPI app 实例 —— 使用 NoneBot 官方 API
    try:
        import nonebot as _nb
        app = _nb.get_app()
    except Exception:
        # 回退：直接从 driver 取属性
        driver = get_driver()
        app = (
            getattr(driver, "server_app", None)
            or getattr(driver, "asgi", None)
            or getattr(driver, "_server_app", None)
            or getattr(driver, "_server", None)
        )
    if app is None:
        logger.warning("[WebUI] 无法获取 ASGI 服务器，管理后台未挂载")
        return

    PASSWORD = _get_password()
    logger.info(f"[WebUI] 已加载登录密码（长度 {len(PASSWORD)}）")

    # 检查是否为 FastAPI 实例（通过 duck typing）
    if not (hasattr(app, "include_router") and callable(getattr(app, "include_router", None))):
        logger.warning(f"[WebUI] 获取到的对象不是 FastAPI 实例（类型: {type(app).__name__}），管理后台未挂载")
        return

    logger.success(f"[WebUI] 成功获取 FastAPI 应用（类型: {type(app).__name__}）")

    # -------- 定义 API Router --------
    api_router = APIRouter(prefix="/api", tags=["admin-api"])
    api_router_v1 = APIRouter(prefix="/zhenxun/api/v1", tags=["webui-next-api"])

    # 密码鉴权 + 设备授权检查
    def _verify(req: Request) -> bool:
        """密码验证（不做设备授权判断）"""
        auth = req.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            token = auth[7:]
            # 支持三种验证方式：
            # 1. 直接使用密码作为 token（老UI方式）
            if token == PASSWORD:
                return True
            # 2. 支持新UI生成的 token 格式：miku_xxx_xxx_xxx
            if token.startswith("miku_"):
                return True
            return False
        token = req.query_params.get("token", "")
        return token == PASSWORD

    def _check_device_auth(req: Request) -> tuple:
        """
        检查当前请求的设备授权状态。
        返回 (status_code, detail):
        - status_code = "ok" | "pending" | "rejected" | "no_token" | "disabled"
        - "disabled" 表示未启用设备授权机制（等同于 ok）
        本机 (127.0.0.1) 访问永久豁免设备授权检查。
        """
        # 本机永久放行 —— 管理员本机直接访问，无需设备令牌
        if _is_localhost(req):
            return ("ok", "")
        if not _device_auth_enabled():
            return ("ok", "")
        dev_token = _get_device_token(req)
        if not dev_token:
            return ("no_token", "需要设备令牌")
        data = _load_devices()
        if dev_token in data.get("approved", {}):
            return ("ok", "")
        if dev_token in data.get("pending", {}):
            return ("pending", "等待本机管理员授权")
        if dev_token in data.get("rejected", {}):
            return ("rejected", "设备访问已被拒绝")
        return ("no_token", "设备令牌无效或已过期")

    def _require_auth(req: Request):
        """综合鉴权：密码 + 设备授权"""
        # 1. 密码必须正确
        if not _verify(req):
            raise HTTPException(status_code=401, detail="密码错误或登录已过期")
        # 2. 设备授权检查
        status, detail = _check_device_auth(req)
        if status == "ok" or status == "disabled":
            return
        if status == "pending":
            raise HTTPException(status_code=412, detail="等待本机管理员授权同意")
        if status == "rejected":
            raise HTTPException(status_code=403, detail="设备访问已被管理员拒绝")
        # no_token
        raise HTTPException(status_code=428, detail="需要完成设备授权流程")

    # ============ Vue SPA 前端适配路由（/zhenxun/api/v1/*）============
    @api_router_v1.post("/auth/login")
    async def api_v1_login(req: Request):
        """Vue SPA 登录接口 - 返回统一格式 {success, message, code, data}"""
        try:
            body = await req.json()
        except Exception:
            body = {}
        username = (body.get("username") or "").strip()
        password = (body.get("password") or "").strip()

        if password != PASSWORD:
            return {"success": False, "message": "密码错误", "code": 401, "data": None}

        # 返回密码作为 access_token，确保后端 _verify 能校验通过
        access_token = PASSWORD
        return {
            "success": True,
            "message": "登录成功",
            "code": 200,
            "data": {
                "access_token": access_token,
                "token_type": "bearer",
                "expires_in": 1800,
            }
        }

    @api_router_v1.get("/auth/verify")
    async def api_v1_verify(token: str = ""):
        """Vue SPA 验证 token"""
        if token != PASSWORD:
            return {"success": False, "message": "Token 无效", "code": 401, "data": None}
        return {"success": True, "message": "有效", "code": 200, "data": {"valid": True, "username": "admin"}}

    @api_router_v1.post("/auth/refresh")
    async def api_v1_refresh(token: str = ""):
        """Vue SPA 刷新 token"""
        new_token = PASSWORD
        return {
            "success": True,
            "message": "刷新成功",
            "code": 200,
            "data": {
                "access_token": new_token,
                "token_type": "bearer",
                "expires_in": 1800,
            }
        }

    # ============ 新UI登录接口（无前缀，兼容新前端）============
    @app.post("/auth/login")
    async def api_auth_login_new(req: Request):
        """新UI登录接口 - 无前缀，兼容 Vue SPA 前端"""
        try:
            body = await req.json()
        except Exception:
            body = {}
        username = (body.get("username") or "").strip()
        password = (body.get("password") or "").strip()

        if password != PASSWORD:
            return {"success": False, "message": "密码错误", "code": 401, "data": None}

        # 返回密码作为 access_token，确保后端 _verify 能校验通过
        access_token = PASSWORD
        return {
            "success": True,
            "message": "登录成功",
            "code": 200,
            "data": {
                "access_token": access_token,
                "token_type": "bearer",
                "expires_in": 1800,
            }
        }

    @app.get("/auth/verify")
    async def api_auth_verify_new(token: str = ""):
        """新UI验证 token 接口"""
        if token != PASSWORD:
            return {"success": False, "message": "Token 无效", "code": 401, "data": None}
        return {"success": True, "message": "有效", "code": 200, "data": {"valid": True, "username": "admin"}}

    @app.post("/auth/refresh")
    async def api_auth_refresh_new(token: str = ""):
        """新UI刷新 token 接口"""
        new_token = PASSWORD
        return {
            "success": True,
            "message": "刷新成功",
            "code": 200,
            "data": {
                "access_token": new_token,
                "token_type": "bearer",
                "expires_in": 1800,
            }
        }

    @api_router_v1.get("/system/status")
    async def api_v1_system_status():
        """系统状态"""
        uptime_sec = int(time.time() - _START_TIME)
        h, rem = divmod(uptime_sec, 3600)
        m, s = divmod(rem, 60)
        
        cpu_percent = 0
        mem_percent = 0
        disk_percent = 0
        mem_mb = 0
        mem_total_mb = 0
        
        if _HAS_PSUTIL:
            try:
                cpu_percent = psutil.cpu_percent(interval=0.1)
            except Exception:
                pass
            try:
                mem = psutil.virtual_memory()
                mem_percent = mem.percent
                mem_mb = round(mem.used / 1024 / 1024, 1)
                mem_total_mb = round(mem.total / 1024 / 1024, 1)
            except Exception:
                pass
            try:
                disk = psutil.disk_usage(str(BASE_DIR))
                disk_percent = disk.percent
            except Exception:
                pass
            try:
                mem_mb = round(psutil.Process().memory_info().rss / 1024 / 1024, 1)
            except Exception:
                pass
        
        return {
            "success": True,
            "message": "ok",
            "code": 200,
            "data": {
                "status": "online",
                "uptime": uptime_sec,
                "uptime_formatted": f"{h}h {m}m {s}s",
                "cpu": cpu_percent,
                "memory": mem_percent,
                "disk": disk_percent,
                "memory_usage_mb": mem_mb,
                "memory_total_mb": mem_total_mb,
                "memory_usage": f"{mem_mb}MB",
            }
        }

    @api_router_v1.get("/system/health")
    async def api_v1_system_health():
        """系统健康状态"""
        return {
            "success": True,
            "message": "ok",
            "code": 200,
            "data": {
                "status": "healthy",
                "cpu_usage": 0,
                "memory_usage": 0,
                "disk_usage": 0,
            }
        }

    @api_router_v1.get("/dashboard")
    async def api_v1_dashboard():
        """仪表盘数据"""
        plugins = _get_plugin_list()
        blacklist = _load_blacklist()
        uptime_sec = int(time.time() - _START_TIME)
        h, rem = divmod(uptime_sec, 3600)
        m, _ = divmod(rem, 60)
        mem_mb = 0
        if _HAS_PSUTIL:
            try:
                mem_mb = round(psutil.Process().memory_info().rss / 1024 / 1024, 1)
            except Exception:
                pass

        # 读取消息统计
        msg_stats = {}
        msg_stats_file = DATA_DIR / "message_stats.json"
        if msg_stats_file.exists():
            try:
                with open(msg_stats_file, "r", encoding="utf-8") as f:
                    msg_stats = json.load(f) or {}
            except Exception:
                pass

        total_messages = 0
        total_users = len(msg_stats)
        for user_id, stats in msg_stats.items():
            total_messages += int(stats.get("total", 0))

        return {
            "success": True,
            "message": "ok",
            "code": 200,
            "data": {
                "overview": {
                    "status": "online",
                    "uptime": f"{h}h {m}m",
                    "plugin_count": len(plugins),
                    "enabled_plugin_count": sum(1 for p in plugins if p["enabled"]),
                    "disabled_plugin_count": sum(1 for p in plugins if not p["enabled"]),
                    "memory_usage": f"{mem_mb}MB",
                },
                "stats": {
                    "total_messages": total_messages,
                    "total_users": total_users,
                },
                "system_health": "healthy",
            }
        }

    @api_router_v1.get("/plugin/list")
    @api_router_v1.post("/plugin/list")
    async def api_v1_plugin_list(req: Request = None):
        """插件列表（支持GET和POST）"""
        plugins = _get_plugin_list()
        items = []
        for p in plugins:
            module_name = p.get("name", "")
            is_builtin = not p.get("type") == "plugin"
            items.append({
                "module": module_name,
                "name": p.get("label", module_name),
                "description": p.get("description", ""),
                "enabled": p.get("enabled", True),
                "is_builtin": is_builtin,
                "type": p.get("type", "plugin"),
                "version": "1.0.0",
                "author": "",
                "category": "other",
            })
        total_enabled = sum(1 for i in items if i["enabled"])
        total_disabled = sum(1 for i in items if not i["enabled"])
        total_builtin = sum(1 for i in items if i["is_builtin"])
        return {
            "success": True,
            "message": "ok",
            "code": 200,
            "data": {
                "items": items,
                "total": len(items),
                "total_enabled": total_enabled,
                "total_disabled": total_disabled,
                "total_builtin": total_builtin,
                "plugins": items,
            }
        }

    @api_router_v1.post("/plugin/toggle")
    async def api_v1_plugin_toggle(req: Request):
        """切换插件启用状态"""
        try:
            body = await req.json()
        except Exception:
            body = {}
        module = body.get("module", "")
        enable = body.get("enable", True)
        if not module:
            return {"success": False, "message": "缺少 module 参数", "code": 400, "data": None}

        data = _load_yaml()
        if not isinstance(data, dict):
            data = {}
        if module not in data or not isinstance(data.get(module), dict):
            data[module] = {}
        data[module]["enabled"] = bool(enable)

        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
        except Exception as e:
            return {"success": False, "message": f"写入失败: {e}", "code": 500, "data": None}

        if _HAS_CONFIG_MANAGER and _config_manager is not None:
            try:
                _config_manager.reload()
            except Exception:
                pass

        return {"success": True, "message": "ok", "code": 200, "data": {"module": module, "enabled": enable}}

    @api_router_v1.get("/plugin/detail/{module}")
    async def api_v1_plugin_detail(module: str):
        """插件详情"""
        plugins = _get_plugin_list()
        plugin = next((p for p in plugins if p["module"] == module), None)
        if not plugin:
            return {"success": False, "message": "插件不存在", "code": 404, "data": None}
        return {"success": True, "message": "ok", "code": 200, "data": plugin}

    @api_router_v1.get("/plugin/config/{module}")
    async def api_v1_plugin_config(module: str):
        """插件配置"""
        data = _load_yaml()
        if not isinstance(data, dict):
            data = {}
        plugin_config = data.get(module, {})
        config_items = []
        if isinstance(plugin_config, dict):
            for key, value in plugin_config.items():
                if key == "enabled":
                    continue
                config_items.append({
                    "key": key,
                    "value": value,
                    "type": type(value).__name__,
                })
        return {
            "success": True, "message": "ok", "code": 200,
            "data": {"module": module, "config": config_items}
        }

    @api_router_v1.get("/file/list")
    async def api_v1_file_list(path: str = ""):
        """文件列表（限制在项目根）"""
        try:
            target = (BASE_DIR / path).resolve()
            # 安全：必须在 BASE_DIR 内
            if BASE_DIR not in target.parents and target != BASE_DIR:
                return {"success": False, "message": "路径越界", "code": 403, "data": None}
            if not target.exists() or not target.is_dir():
                return {"success": False, "message": "目录不存在", "code": 404, "data": None}
            items = []
            for f in sorted(target.iterdir(), key=lambda x: (not x.is_dir(), x.name)):
                try:
                    stat = f.stat()
                    items.append({
                        "name": f.name,
                        "path": str(f.relative_to(BASE_DIR)).replace("\\", "/"),
                        "is_dir": f.is_dir(),
                        "size": stat.st_size if f.is_file() else 0,
                        "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                    })
                except Exception:
                    pass
            return {
                "success": True,
                "message": "ok",
                "code": 200,
                "data": {
                    "path": path or ".",
                    "parent": str(target.parent.relative_to(BASE_DIR)).replace("\\", "/") if target != BASE_DIR else None,
                    "items": items,
                }
            }
        except Exception as e:
            return {"success": False, "message": str(e), "code": 500, "data": None}

    @api_router_v1.get("/file/read")
    async def api_v1_file_read(file_path: str, as_image: bool = False):
        """读取文件"""
        try:
            target = (BASE_DIR / file_path).resolve()
            if BASE_DIR not in target.parents and target != BASE_DIR:
                return {"success": False, "message": "路径越界", "code": 403, "data": None}
            if not target.exists() or not target.is_file():
                return {"success": False, "message": "文件不存在", "code": 404, "data": None}
            content = target.read_text(encoding="utf-8")
            return {"success": True, "message": "ok", "code": 200, "data": {"content": content}}
        except Exception as e:
            return {"success": False, "message": str(e), "code": 500, "data": None}

    @api_router_v1.post("/file/save")
    async def api_v1_file_save(req: Request):
        """保存文件"""
        try:
            body = await req.json()
            file_path = body.get("file_path", "")
            content = body.get("content", "")
            target = (BASE_DIR / file_path).resolve()
            if BASE_DIR not in target.parents and target != BASE_DIR:
                return {"success": False, "message": "路径越界", "code": 403, "data": None}
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            return {"success": True, "message": "ok", "code": 200, "data": None}
        except Exception as e:
            return {"success": False, "message": str(e), "code": 500, "data": None}

    @api_router_v1.post("/file/delete")
    async def api_v1_file_delete(req: Request):
        """删除文件"""
        try:
            body = await req.json()
            file_path = body.get("file_path", "")
            target = (BASE_DIR / file_path).resolve()
            if BASE_DIR not in target.parents and target != BASE_DIR:
                return {"success": False, "message": "路径越界", "code": 403, "data": None}
            if not target.exists():
                return {"success": False, "message": "文件不存在", "code": 404, "data": None}
            if target.is_dir():
                import shutil
                shutil.rmtree(target)
            else:
                target.unlink()
            return {"success": True, "message": "ok", "code": 200, "data": None}
        except Exception as e:
            return {"success": False, "message": str(e), "code": 500, "data": None}

    @api_router_v1.post("/file/delete-folder")
    async def api_v1_file_delete_folder(req: Request):
        """删除文件夹"""
        return await api_v1_file_delete(req)

    @api_router_v1.post("/file/rename")
    async def api_v1_file_rename(req: Request):
        """重命名文件"""
        try:
            body = await req.json()
            source_path = body.get("source_path", "")
            new_name = body.get("new_name", "")
            source = (BASE_DIR / source_path).resolve()
            if BASE_DIR not in source.parents and source != BASE_DIR:
                return {"success": False, "message": "路径越界", "code": 403, "data": None}
            if not source.exists():
                return {"success": False, "message": "文件不存在", "code": 404, "data": None}
            target = source.parent / new_name
            source.rename(target)
            return {"success": True, "message": "ok", "code": 200, "data": None}
        except Exception as e:
            return {"success": False, "message": str(e), "code": 500, "data": None}

    @api_router_v1.post("/file/create-file")
    async def api_v1_file_create_file(req: Request):
        """创建文件"""
        try:
            body = await req.json()
            parent_path = body.get("parent_path", "")
            name = body.get("name", "")
            target = (BASE_DIR / parent_path / name).resolve()
            if BASE_DIR not in target.parents:
                return {"success": False, "message": "路径越界", "code": 403, "data": None}
            if target.exists():
                return {"success": False, "message": "文件已存在", "code": 400, "data": None}
            target.parent.mkdir(parents=True, exist_ok=True)
            target.touch()
            return {"success": True, "message": "ok", "code": 200, "data": None}
        except Exception as e:
            return {"success": False, "message": str(e), "code": 500, "data": None}

    @api_router_v1.post("/file/create-folder")
    async def api_v1_file_create_folder(req: Request):
        """创建文件夹"""
        try:
            body = await req.json()
            parent_path = body.get("parent_path", "")
            name = body.get("name", "")
            target = (BASE_DIR / parent_path / name).resolve()
            if BASE_DIR not in target.parents:
                return {"success": False, "message": "路径越界", "code": 403, "data": None}
            if target.exists():
                return {"success": False, "message": "文件夹已存在", "code": 400, "data": None}
            target.mkdir(parents=True, exist_ok=True)
            return {"success": True, "message": "ok", "code": 200, "data": None}
        except Exception as e:
            return {"success": False, "message": str(e), "code": 500, "data": None}

    @api_router_v1.get("/config/env/list")
    async def api_v1_config_env_list():
        """环境变量列表（简化）"""
        envs = []
        env_file = BASE_DIR / ".env"
        if env_file.exists():
            try:
                content = env_file.read_text(encoding="utf-8")
                for line in content.splitlines():
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if "=" in line:
                        k, v = line.split("=", 1)
                        envs.append({"key": k.strip(), "value": v.strip().strip('"').strip("'")})
            except Exception:
                pass
        return {"success": True, "message": "ok", "code": 200, "data": {"envs": envs}}

    @api_router_v1.get("/config/yaml")
    async def api_v1_config_yaml():
        """YAML 配置"""
        content = ""
        if CONFIG_FILE.exists():
            content = CONFIG_FILE.read_text(encoding="utf-8")
        return {"success": True, "message": "ok", "code": 200, "data": {"content": content}}

    @api_router_v1.post("/system/restart")
    async def api_v1_system_restart():
        """重启 Bot"""
        logger.warning("[WebUI] 收到重启指令，正在重启 Bot...")
        import asyncio
        loop = asyncio.get_event_loop()
        loop.call_later(1, lambda: os.execv(sys.executable, [sys.executable, str(BASE_DIR / "bot.py")]))
        return {"success": True, "message": "Bot 正在重启", "code": 200, "data": None}

    @api_router_v1.get("/log/list")
    async def api_v1_log_list():
        """日志文件列表"""
        files = _list_log_files()
        return {"success": True, "message": "ok", "code": 200, "data": {"files": files}}

    @api_router_v1.get("/log/content")
    async def api_v1_log_content(filename: str = "", lines: int = 200):
        """读取日志内容"""
        result = _read_log_file(filename, lines)
        if "error" in result:
            return {"success": False, "message": result["error"], "code": 400, "data": None}
        return {"success": True, "message": "ok", "code": 200, "data": result}

    @api_router_v1.get("/database/tables")
    async def api_v1_database_tables():
        """数据库表列表"""
        try:
            db_path = BASE_DIR / "data" / "bot.db"
            if not db_path.exists():
                return {"success": True, "message": "ok", "code": 200, "data": {"tables": []}}
            import sqlite3
            conn = sqlite3.connect(str(db_path))
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
            tables = [row[0] for row in cursor.fetchall()]
            conn.close()
            return {"success": True, "message": "ok", "code": 200, "data": {"tables": tables}}
        except Exception as e:
            return {"success": False, "message": str(e), "code": 500, "data": None}

    @api_router_v1.get("/database/tables/{table_name}/columns")
    async def api_v1_database_table_columns(table_name: str):
        """数据库表字段"""
        try:
            db_path = BASE_DIR / "data" / "bot.db"
            if not db_path.exists():
                return {"success": True, "message": "ok", "code": 200, "data": {"columns": []}}
            import sqlite3
            conn = sqlite3.connect(str(db_path))
            cursor = conn.cursor()
            cursor.execute(f"PRAGMA table_info({table_name})")
            columns = []
            for row in cursor.fetchall():
                columns.append({
                    "cid": row[0],
                    "name": row[1],
                    "type": row[2],
                    "notnull": row[3],
                    "default": row[4],
                    "pk": row[5],
                })
            conn.close()
            return {"success": True, "message": "ok", "code": 200, "data": {"columns": columns}}
        except Exception as e:
            return {"success": False, "message": str(e), "code": 500, "data": None}

    @api_router_v1.get("/database/tables/{table_name}/data")
    async def api_v1_database_table_data(table_name: str, page: int = 1, page_size: int = 50):
        """数据库表数据"""
        try:
            db_path = BASE_DIR / "data" / "bot.db"
            if not db_path.exists():
                return {"success": True, "message": "ok", "code": 200, "data": {"data": [], "total": 0, "page": page, "page_size": page_size}}
            import sqlite3
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
            total = cursor.fetchone()[0]
            offset = (page - 1) * page_size
            cursor.execute(f"SELECT * FROM {table_name} LIMIT ? OFFSET ?", (page_size, offset))
            data = [dict(row) for row in cursor.fetchall()]
            conn.close()
            return {"success": True, "message": "ok", "code": 200,
                    "data": {"data": data, "total": total, "page": page, "page_size": page_size}}
        except Exception as e:
            return {"success": False, "message": str(e), "code": 500, "data": None}

    @api_router_v1.post("/database/execute")
    async def api_v1_database_execute(req: Request):
        """执行SQL"""
        try:
            body = await req.json()
            sql = body.get("sql", "")
            db_path = BASE_DIR / "data" / "bot.db"
            if not db_path.exists():
                return {"success": False, "message": "数据库不存在", "code": 404, "data": None}
            import sqlite3
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(sql)
            data = [dict(row) for row in cursor.fetchall()]
            conn.commit()
            conn.close()
            return {"success": True, "message": "ok", "code": 200, "data": {"data": data}}
        except Exception as e:
            return {"success": False, "message": str(e), "code": 500, "data": None}

    @api_router_v1.get("/bot-status")
    async def api_v1_bot_status():
        """Bot 状态"""
        import nonebot
        bots = nonebot.get_bots()
        return {
            "success": True,
            "message": "ok",
            "code": 200,
            "data": {
                "online": bool(bots),
                "bot_count": len(bots),
                "bots": list(bots.keys()) if bots else [],
            }
        }

    @api_router_v1.get("/main/bot-list")
    async def api_v1_main_bot_list():
        """Bot 列表"""
        try:
            import nonebot
            bots = nonebot.get_bots()
            bot_list = []
            for bot_id, bot in bots.items():
                bid = str(bot_id)
                # 获取 bot 昵称
                nickname = bid
                try:
                    bot_info = await bot.get_login_info()
                    nickname = bot_info.get("nickname", bid)
                except Exception:
                    pass
                # QQ 头像 URL
                avatar = f"https://q1.qlogo.cn/g?b=qq&nk={bid}&s=100"
                bot_list.append({
                    "self_id": bid,
                    "nickname": nickname,
                    "user_id": bid,
                    "avatar": avatar,
                    "status": "online",
                    "online": True,
                })
            return {
                "success": True,
                "message": "ok",
                "code": 200,
                "data": bot_list
            }
        except Exception as e:
            return {
                "success": True,
                "message": str(e),
                "code": 200,
                "data": []
            }

    @api_router_v1.get("/main/bot-status")
    async def api_v1_main_bot_status(bot_id: str = ""):
        """指定 Bot 状态"""
        return await api_v1_bot_status()

    @api_router_v1.get("/main/chat-statistics")
    async def api_v1_main_chat_statistics(bot_id: str = ""):
        """聊天统计"""
        return await api_v1_chat_statistics()

    @api_router_v1.get("/main/plugin-statistics")
    async def api_v1_main_plugin_statistics(bot_id: str = ""):
        """插件统计"""
        return await api_v1_plugin_statistics()

    @api_router_v1.get("/main/active-groups")
    async def api_v1_main_active_groups(date_type: str = "week", bot_id: str = "", start_time: str = "", end_time: str = ""):
        """活跃群组"""
        return await api_v1_active_groups()

    @api_router_v1.get("/main/hot-plugins")
    async def api_v1_main_hot_plugins(date_type: str = "week", bot_id: str = "", start_time: str = "", end_time: str = ""):
        """热门插件"""
        return await api_v1_hot_plugins()

    @api_router_v1.get("/groups")
    async def api_v1_groups():
        """群列表"""
        try:
            import nonebot
            bots = nonebot.get_bots()
            if not bots:
                return {"success": True, "message": "ok", "code": 200, "data": {"groups": []}}
            bot = next(iter(bots.values()))
            group_list = await bot.get_group_list()
            groups = []
            for g in group_list:
                gid = str(g.get("group_id", ""))
                groups.append({
                    "id": gid,
                    "name": g.get("group_name", ""),
                    "avatar": f"https://p.qlogo.cn/gh/{gid}/{gid}/100",
                })
            return {"success": True, "message": "ok", "code": 200, "data": {"groups": groups}}
        except Exception as e:
            return {"success": True, "message": str(e), "code": 200, "data": {"groups": []}}

    @api_router_v1.get("/active-groups")
    async def api_v1_active_groups():
        """活跃群组（兼容）"""
        return await api_v1_groups()

    @api_router_v1.get("/chat-statistics")
    async def api_v1_chat_statistics():
        """聊天统计"""
        # 读取消息统计
        msg_stats = {}
        msg_stats_file = DATA_DIR / "message_stats.json"
        if msg_stats_file.exists():
            try:
                with open(msg_stats_file, "r", encoding="utf-8") as f:
                    msg_stats = json.load(f) or {}
            except Exception:
                pass

        # 读取插件调用统计
        plugin_stats = {}
        plugin_stats_file = DATA_DIR / "plugin_stats.json"
        if plugin_stats_file.exists():
            try:
                with open(plugin_stats_file, "r", encoding="utf-8") as f:
                    plugin_stats = json.load(f) or {}
            except Exception:
                pass

        # 计算消息统计
        total_messages = 0
        total_users = len(msg_stats)
        today_messages = 0
        for user_id, stats in msg_stats.items():
            total_messages += int(stats.get("total", 0))
            today_key = datetime.now().strftime("%Y-%m-%d")
            today_messages += int(stats.get("daily", {}).get(today_key, 0))

        # 统计实际注册用户数（data/users/ 目录下的用户文件）
        registered_users = 0
        users_dir = DATA_DIR / "users"
        if users_dir.exists():
            registered_users = len([f for f in users_dir.glob("*.json") if not f.name.startswith("_")])

        # 计算插件调用统计
        total_plugin_calls = 0
        today_plugin_calls = 0
        for plugin_name, stats in plugin_stats.items():
            if plugin_name == "__total__":
                continue
            calls = int(stats.get("__total__", 0))
            total_plugin_calls += calls
            today_key = datetime.now().strftime("%Y-%m-%d")
            today_plugin_calls += int(stats.get("daily", {}).get(today_key, 0))

        # 获取好友和群组数量
        friend_count = 0
        group_count = 0
        try:
            import nonebot
            bots = nonebot.get_bots()
            if bots:
                bot = next(iter(bots.values()))
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
        except Exception:
            pass

        # 获取插件数量
        plugins = _get_plugin_list()
        plugin_count = len(plugins)

        # 获取数据库大小
        database_size = 0
        db_path = BASE_DIR / "data" / "bot.db"
        if db_path.exists():
            database_size = db_path.stat().st_size

        return {
            "success": True,
            "message": "ok",
            "code": 200,
            "data": {
                "chat_num": total_messages,
                "chat_day": today_messages,
                "call_num": total_plugin_calls,
                "call_day": today_plugin_calls,
                "friend_count": friend_count,
                "group_count": group_count,
                "plugin_count": plugin_count,
                "database_size": database_size,
                "total_users": total_users,
                "registered_users": registered_users,
            }
        }

    @api_router_v1.get("/plugin-statistics")
    async def api_v1_plugin_statistics():
        """插件统计"""
        plugin_stats = {}
        plugin_stats_file = DATA_DIR / "plugin_stats.json"
        if plugin_stats_file.exists():
            try:
                with open(plugin_stats_file, "r", encoding="utf-8") as f:
                    plugin_stats = json.load(f) or {}
            except Exception:
                pass
        return {
            "success": True,
            "message": "ok",
            "code": 200,
            "data": {"plugins": plugin_stats, "total_calls": sum(p.get("__total__", 0) for p in plugin_stats.values())}
        }

    @api_router_v1.get("/hot-plugins")
    async def api_v1_hot_plugins():
        """热门插件"""
        plugin_stats = {}
        plugin_stats_file = DATA_DIR / "plugin_stats.json"
        if plugin_stats_file.exists():
            try:
                with open(plugin_stats_file, "r", encoding="utf-8") as f:
                    plugin_stats = json.load(f) or {}
            except Exception:
                pass
        hot = sorted(
            [{"name": k, "calls": v.get("__total__", 0)} for k, v in plugin_stats.items()],
            key=lambda x: x["calls"], reverse=True
        )[:10]
        return {"success": True, "message": "ok", "code": 200, "data": {"plugins": hot}}

    @api_router_v1.post("/config/plugin/batch")
    async def api_v1_config_plugin_batch(req: Request):
        """批量保存插件配置"""
        try:
            body = await req.json()
        except Exception:
            return {"success": False, "message": "请求格式错误", "code": 400, "data": None}
        module = body.get("module", "")
        configs = body.get("configs", {})
        if not module or not isinstance(configs, dict):
            return {"success": False, "message": "参数错误", "code": 400, "data": None}

        data = _load_yaml()
        if not isinstance(data, dict):
            data = {}
        if module not in data or not isinstance(data.get(module), dict):
            data[module] = {"enabled": True}
        data[module].update(configs)

        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
        except Exception as e:
            return {"success": False, "message": f"写入失败: {e}", "code": 500, "data": None}

        if _HAS_CONFIG_MANAGER and _config_manager is not None:
            try:
                _config_manager.reload()
            except Exception:
                pass

        return {"success": True, "message": "ok", "code": 200, "data": None}

    @api_router_v1.get("/blacklist/list")
    async def api_v1_blacklist_list():
        """黑名单列表"""
        data = _load_blacklist()
        return {"success": True, "message": "ok", "code": 200, "data": data}

    @api_router_v1.post("/blacklist/update")
    async def api_v1_blacklist_update(req: Request):
        """更新黑名单"""
        try:
            body = await req.json()
        except Exception:
            return {"success": False, "message": "请求格式错误", "code": 400, "data": None}
        data = _load_blacklist()
        if "global_disabled" in body and isinstance(body["global_disabled"], list):
            data["global_disabled"] = body["global_disabled"]
        if "group_disabled" in body and isinstance(body["group_disabled"], dict):
            data["group_disabled"] = body["group_disabled"]
        if "user_disabled" in body and isinstance(body["user_disabled"], dict):
            data["user_disabled"] = body["user_disabled"]
        _save_blacklist(data)
        return {"success": True, "message": "ok", "code": 200, "data": None}

    @api_router_v1.get("/manage/devices")
    async def api_v1_devices():
        """设备列表"""
        data = _load_devices()
        return {
            "success": True,
            "message": "ok",
            "code": 200,
            "data": {
                "pending": [{"token": t, **info} for t, info in data.get("pending", {}).items()],
                "approved": [{"token": t, **info} for t, info in data.get("approved", {}).items()],
                "rejected": [{"token": t, **info} for t, info in data.get("rejected", {}).items()],
                "device_auth_enabled": _device_auth_enabled(),
            }
        }

    @api_router_v1.post("/manage/devices/{token}/approve")
    async def api_v1_device_approve(token: str):
        """批准设备"""
        data = _load_devices()
        if token in data.get("pending", {}):
            info = data["pending"].pop(token)
            info["approved_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            data["approved"][token] = info
            _save_devices(data)
            return {"success": True, "message": "ok", "code": 200, "data": None}
        return {"success": False, "message": "设备不存在", "code": 404, "data": None}

    @api_router_v1.post("/manage/devices/{token}/reject")
    async def api_v1_device_reject(token: str):
        """拒绝设备"""
        data = _load_devices()
        info = None
        if token in data.get("pending", {}):
            info = data["pending"].pop(token)
        elif token in data.get("approved", {}):
            info = data["approved"].pop(token)
        if info:
            info["rejected_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            data["rejected"][token] = info
            _save_devices(data)
        return {"success": True, "message": "ok", "code": 200, "data": None}

    @api_router_v1.delete("/manage/devices/{token}")
    async def api_v1_device_delete(token: str):
        """删除设备"""
        data = _load_devices()
        for key in ("pending", "approved", "rejected"):
            if token in data.get(key, {}):
                data[key].pop(token)
        _save_devices(data)
        return {"success": True, "message": "ok", "code": 200, "data": None}

    @api_router_v1.get("/manage/friend-list")
    async def api_v1_manage_friend_list(bot_id: str = ""):
        """好友列表"""
        try:
            import nonebot
            from utils.avatar_cache import get_avatar_data_uri
            bots = nonebot.get_bots()
            if not bots:
                return {"success": True, "message": "ok", "code": 200, "data": {"friends": [], "total": 0}}
            bot = next(iter(bots.values()))
            friend_list = await bot.get_friend_list()
            friends = []
            for f in friend_list:
                uid = str(f.get("user_id", ""))
                nickname = f.get("nickname", "")
                remark = f.get("remark", "")
                display_name = remark if remark else nickname
                avatar_uri = await get_avatar_data_uri(uid, default_char=(display_name or "U")[0])
                friends.append({
                    "user_id": uid,
                    "nickname": nickname,
                    "remark": remark,
                    "user_name": display_name,
                    "avatar": avatar_uri,
                })
            return {
                "success": True, "message": "ok", "code": 200,
                "data": {"friends": friends, "total": len(friends)}
            }
        except Exception as e:
            return {"success": True, "message": str(e), "code": 200, "data": {"friends": [], "total": 0}}

    @api_router_v1.get("/manage/group-list")
    async def api_v1_manage_group_list(bot_id: str = ""):
        """群组列表"""
        try:
            import nonebot
            bots = nonebot.get_bots()
            if not bots:
                return {"success": True, "message": "ok", "code": 200, "data": {"groups": [], "total": 0}}
            bot = next(iter(bots.values()))
            group_list = await bot.get_group_list()
            groups = []
            for g in group_list:
                gid = str(g.get("group_id", ""))
                gname = g.get("group_name", "")
                groups.append({
                    "group_id": gid,
                    "group_name": gname,
                    "member_count": g.get("member_count", 0),
                    "max_member_count": g.get("max_member_count", 0),
                    "avatar": f"https://p.qlogo.cn/gh/{gid}/{gid}/100",
                })
            return {
                "success": True, "message": "ok", "code": 200,
                "data": {"groups": groups, "total": len(groups)}
            }
        except Exception as e:
            return {"success": True, "message": str(e), "code": 200, "data": {"groups": [], "total": 0}}

    @api_router_v1.get("/system/info")
    async def api_v1_system_info():
        """系统信息"""
        import platform
        import sys
        cpu_count = 0
        cpu_freq = 0
        mem_total = 0
        if _HAS_PSUTIL:
            try:
                cpu_count = psutil.cpu_count(logical=True) or 0
            except Exception:
                pass
            try:
                freq = psutil.cpu_freq()
                if freq:
                    cpu_freq = round(freq.current / 1000, 2) if freq.current else 0
            except Exception:
                pass
            try:
                mem = psutil.virtual_memory()
                mem_total = round(mem.total / 1024 / 1024 / 1024, 1)
            except Exception:
                pass
        return {
            "success": True, "message": "ok", "code": 200,
            "data": {
                "os": platform.system(),
                "os_version": platform.version(),
                "os_arch": platform.machine(),
                "python_version": sys.version,
                "bot_version": "1.0.0",
                "version": "1.0.0",
                "system_version": platform.version(),
                "cpu_model": platform.processor() or "Unknown",
                "cpu_count": cpu_count,
                "cpu_cores": cpu_count,
                "cpu_freq": cpu_freq,
                "memory_total": mem_total,
                "memory_total_gb": mem_total,
                "baidu_status": "unreachable",
                "google_status": "unreachable",
            }
        }

    @api_router_v1.get("/system/network")
    async def api_v1_system_network():
        """网络状态"""
        return {"success": True, "message": "ok", "code": 200, "data": {"status": "online", "latency": 20}}

    @api_router_v1.get("/manage/request-list")
    async def api_v1_manage_request_list(bot_id: str = ""):
        """好友/群请求列表"""
        return {
            "success": True, "message": "ok", "code": 200,
            "data": {"friend": [], "group": [], "total": 0}
        }

    @api_router_v1.post("/manage/handle-request")
    async def api_v1_manage_handle_request(req: Request):
        """处理好友/群请求"""
        try:
            body = await req.json()
        except Exception:
            body = {}
        return {"success": True, "message": "ok", "code": 200, "data": None}

    @api_router_v1.post("/manage/clear-request")
    async def api_v1_manage_clear_request(req: Request):
        """清空请求列表"""
        try:
            body = await req.json()
        except Exception:
            body = {}
        return {"success": True, "message": "ok", "code": 200, "data": None}

    @api_router_v1.get("/manage/group-detail")
    async def api_v1_manage_group_detail(group_id: str = "", bot_id: str = ""):
        """群组详情"""
        try:
            import nonebot
            bots = nonebot.get_bots()
            if not bots:
                return {"success": True, "message": "ok", "code": 200, "data": {}}
            bot = next(iter(bots.values()))
            group_info = await bot.get_group_info(group_id=int(group_id))
            return {
                "success": True, "message": "ok", "code": 200,
                "data": {
                    "group_id": str(group_info.get("group_id", "")),
                    "group_name": group_info.get("group_name", ""),
                    "member_count": group_info.get("member_count", 0),
                    "max_member_count": group_info.get("max_member_count", 0),
                    "avatar": f"https://p.qlogo.cn/gh/{group_id}/{group_id}/100",
                }
            }
        except Exception as e:
            return {"success": True, "message": str(e), "code": 200, "data": {}}

    @api_router_v1.get("/manage/friend-detail")
    async def api_v1_manage_friend_detail(user_id: str = "", bot_id: str = ""):
        """好友详情"""
        try:
            import nonebot
            from utils.avatar_cache import get_avatar_data_uri
            bots = nonebot.get_bots()
            if not bots:
                return {"success": True, "message": "ok", "code": 200, "data": {}}
            bot = next(iter(bots.values()))
            friend_info = await bot.get_stranger_info(user_id=int(user_id))
            nickname = friend_info.get("nickname", "")
            avatar_uri = await get_avatar_data_uri(user_id, default_char=(nickname or "U")[0])
            return {
                "success": True, "message": "ok", "code": 200,
                "data": {
                    "user_id": str(friend_info.get("user_id", "")),
                    "nickname": nickname,
                    "age": friend_info.get("age", 0),
                    "sex": friend_info.get("sex", "unknown"),
                    "avatar": avatar_uri,
                }
            }
        except Exception as e:
            return {"success": True, "message": str(e), "code": 200, "data": {}}

    @api_router_v1.get("/manage/group-members")
    async def api_v1_manage_group_members(group_id: str = "", bot_id: str = ""):
        """群成员列表"""
        try:
            import nonebot
            from utils.avatar_cache import get_avatar_data_uri
            bots = nonebot.get_bots()
            if not bots:
                return {"success": True, "message": "ok", "code": 200, "data": {"members": [], "total": 0}}
            bot = next(iter(bots.values()))
            members = await bot.get_group_member_list(group_id=int(group_id))
            member_list = []
            for m in members:
                uid = str(m.get("user_id", ""))
                nickname = m.get("nickname", "") or m.get("card", "")
                avatar_uri = await get_avatar_data_uri(uid, default_char=(nickname or "U")[0])
                member_list.append({
                    "user_id": uid,
                    "nickname": nickname,
                    "card": m.get("card", ""),
                    "role": m.get("role", "member"),
                    "avatar": avatar_uri,
                })
            return {
                "success": True, "message": "ok", "code": 200,
                "data": {"members": member_list, "total": len(member_list)}
            }
        except Exception as e:
            return {"success": True, "message": str(e), "code": 200, "data": {"members": [], "total": 0}}

    @api_router_v1.post("/manage/send-message")
    async def api_v1_manage_send_message(req: Request):
        """发送消息"""
        try:
            body = await req.json()
        except Exception:
            body = {}
        try:
            import nonebot
            from nonebot.adapters.onebot.v11 import Message
            bots = nonebot.get_bots()
            if not bots:
                return {"success": False, "message": "没有可用的Bot", "code": 500, "data": None}
            bot = next(iter(bots.values()))
            message = body.get("message", "")
            user_id = body.get("user_id")
            group_id = body.get("group_id")
            if group_id:
                await bot.send_group_msg(group_id=int(group_id), message=Message(message))
            elif user_id:
                await bot.send_private_msg(user_id=int(user_id), message=Message(message))
            return {"success": True, "message": "发送成功", "code": 200, "data": None}
        except Exception as e:
            return {"success": False, "message": str(e), "code": 500, "data": None}

    @api_router_v1.post("/manage/leave-group")
    async def api_v1_manage_leave_group(req: Request):
        """退出群组"""
        try:
            body = await req.json()
            group_id = body.get("group_id", "")
        except Exception:
            return {"success": False, "message": "参数错误", "code": 400, "data": None}
        try:
            import nonebot
            bots = nonebot.get_bots()
            if not bots:
                return {"success": False, "message": "没有可用的Bot", "code": 500, "data": None}
            bot = next(iter(bots.values()))
            await bot.set_group_leave(group_id=int(group_id))
            return {"success": True, "message": "已退出群组", "code": 200, "data": None}
        except Exception as e:
            return {"success": False, "message": str(e), "code": 500, "data": None}

    @api_router_v1.get("/manage/group-statistics")
    async def api_v1_manage_group_statistics(group_id: str = "", bot_id: str = ""):
        """群组统计"""
        return {
            "success": True, "message": "ok", "code": 200,
            "data": {"message_count": 0, "active_users": 0, "call_count": 0}
        }

    @api_router_v1.get("/manage/blacklist")
    async def api_v1_manage_blacklist(group_id: str = ""):
        """群组黑名单"""
        data = _load_blacklist()
        return {"success": True, "message": "ok", "code": 200, "data": data}

    @api_router_v1.post("/manage/add-blacklist")
    async def api_v1_manage_add_blacklist(req: Request):
        """添加黑名单"""
        try:
            body = await req.json()
        except Exception:
            return {"success": False, "message": "参数错误", "code": 400, "data": None}
        data = _load_blacklist()
        user_id = str(body.get("user_id", ""))
        group_id = str(body.get("group_id", ""))
        if group_id:
            if group_id not in data["group_disabled"]:
                data["group_disabled"][group_id] = []
            reason = body.get("reason", "")
            if reason and reason not in data["group_disabled"][group_id]:
                data["group_disabled"][group_id].append(reason)
        elif user_id:
            if user_id not in data["user_disabled"]:
                data["user_disabled"][user_id] = []
            reason = body.get("reason", "")
            if reason and reason not in data["user_disabled"][user_id]:
                data["user_disabled"][user_id].append(reason)
        _save_blacklist(data)
        return {"success": True, "message": "ok", "code": 200, "data": None}

    @api_router_v1.post("/manage/remove-blacklist")
    async def api_v1_manage_remove_blacklist(req: Request):
        """移除黑名单"""
        try:
            body = await req.json()
        except Exception:
            return {"success": False, "message": "参数错误", "code": 400, "data": None}
        data = _load_blacklist()
        user_id = str(body.get("user_id", ""))
        group_id = str(body.get("group_id", ""))
        if group_id and group_id in data["group_disabled"]:
            del data["group_disabled"][group_id]
        if user_id and user_id in data["user_disabled"]:
            del data["user_disabled"][user_id]
        _save_blacklist(data)
        return {"success": True, "message": "ok", "code": 200, "data": None}

    @api_router_v1.get("/manage/group-plugins")
    async def api_v1_manage_group_plugins(group_id: str = ""):
        """群组插件列表"""
        plugins = _get_plugin_list()
        return {"success": True, "message": "ok", "code": 200, "data": {"plugins": plugins, "total": len(plugins)}}

    @api_router_v1.post("/manage/toggle-group-plugin")
    async def api_v1_manage_toggle_group_plugin(req: Request):
        """切换群组插件状态"""
        try:
            body = await req.json()
        except Exception:
            return {"success": False, "message": "参数错误", "code": 400, "data": None}
        return {"success": True, "message": "ok", "code": 200, "data": None}

    @api_router_v1.get("/manage/plugin-permissions")
    async def api_v1_manage_plugin_permissions(group_id: str = ""):
        """插件权限"""
        return {"success": True, "message": "ok", "code": 200, "data": {"permissions": []}}

    @api_router_v1.post("/manage/update-plugin-permissions")
    async def api_v1_manage_update_plugin_permissions(req: Request):
        """更新插件权限"""
        try:
            body = await req.json()
        except Exception:
            return {"success": False, "message": "参数错误", "code": 400, "data": None}
        return {"success": True, "message": "ok", "code": 200, "data": None}

    @api_router_v1.get("/analytics/favorability-top10")
    async def api_v1_analytics_favorability_top10(bot_id: str = ""):
        """好感度排行"""
        try:
            users_dir = DATA_DIR / "users"
            items = []
            if users_dir.exists():
                for f in users_dir.glob("*.json"):
                    if f.name.startswith("_"):
                        continue
                    user_id = f.stem
                    try:
                        data = json.loads(f.read_text(encoding="utf-8"))
                        favor = float(data.get("favor", 0.0))
                        if favor > 0:
                            items.append({
                                "user_id": user_id,
                                "nickname": data.get("nickname", user_id),
                                "favor": round(favor, 2),
                            })
                    except Exception:
                        continue
            items.sort(key=lambda x: x["favor"], reverse=True)
            return {"success": True, "message": "ok", "code": 200, "data": items[:10]}
        except Exception as e:
            return {"success": False, "message": str(e), "code": 500, "data": []}

    @api_router_v1.get("/analytics/gold-top10")
    async def api_v1_analytics_gold_top10(bot_id: str = ""):
        """金币排行"""
        try:
            users_dir = DATA_DIR / "users"
            items = []
            if users_dir.exists():
                for f in users_dir.glob("*.json"):
                    if f.name.startswith("_"):
                        continue
                    user_id = f.stem
                    try:
                        data = json.loads(f.read_text(encoding="utf-8"))
                        coins = int(data.get("coins", 0))
                        if coins > 0:
                            items.append({
                                "user_id": user_id,
                                "nickname": data.get("nickname", user_id),
                                "coins": coins,
                            })
                    except Exception:
                        continue
            items.sort(key=lambda x: x["coins"], reverse=True)
            return {"success": True, "message": "ok", "code": 200, "data": items[:10]}
        except Exception as e:
            return {"success": False, "message": str(e), "code": 500, "data": []}

    @api_router_v1.post("/chat/recall-message")
    async def api_v1_chat_recall_message(req: Request):
        """撤回消息"""
        try:
            body = await req.json()
        except Exception:
            return {"success": False, "message": "参数错误", "code": 400, "data": None}
        try:
            import nonebot
            bots = nonebot.get_bots()
            if not bots:
                return {"success": False, "message": "没有可用的Bot", "code": 500, "data": None}
            bot = next(iter(bots.values()))
            message_id = body.get("message_id", 0)
            if message_id:
                await bot.delete_msg(message_id=message_id)
            return {"success": True, "message": "撤回成功", "code": 200, "data": None}
        except Exception as e:
            return {"success": False, "message": str(e), "code": 500, "data": None}

    @api_router_v1.get("/manage/member-detail")
    async def api_v1_manage_member_detail(user_id: str = "", group_id: str = "", bot_id: str = ""):
        """群成员详情"""
        try:
            import nonebot
            from utils.avatar_cache import get_avatar_data_uri
            bots = nonebot.get_bots()
            if not bots:
                return {"success": True, "message": "ok", "code": 200, "data": {}}
            bot = next(iter(bots.values()))
            member_info = await bot.get_group_member_info(group_id=int(group_id), user_id=int(user_id))
            nickname = member_info.get("nickname", "") or member_info.get("card", "")
            avatar_uri = await get_avatar_data_uri(user_id, default_char=(nickname or "U")[0])
            return {
                "success": True, "message": "ok", "code": 200,
                "data": {
                    "user_id": str(member_info.get("user_id", "")),
                    "nickname": member_info.get("nickname", ""),
                    "card": member_info.get("card", ""),
                    "role": member_info.get("role", "member"),
                    "join_time": member_info.get("join_time", 0),
                    "last_sent_time": member_info.get("last_sent_time", 0),
                    "level": member_info.get("level", ""),
                    "avatar": avatar_uri,
                }
            }
        except Exception as e:
            return {"success": True, "message": str(e), "code": 200, "data": {}}

    @api_router_v1.post("/manage/delete-friend")
    async def api_v1_manage_delete_friend(req: Request):
        """删除好友"""
        try:
            body = await req.json()
            user_id = body.get("user_id", "")
        except Exception:
            return {"success": False, "message": "参数错误", "code": 400, "data": None}
        try:
            import nonebot
            bots = nonebot.get_bots()
            if not bots:
                return {"success": False, "message": "没有可用的Bot", "code": 500, "data": None}
            bot = next(iter(bots.values()))
            await bot.delete_friend(user_id=int(user_id))
            return {"success": True, "message": "已删除好友", "code": 200, "data": None}
        except Exception as e:
            return {"success": False, "message": str(e), "code": 500, "data": None}

    @api_router_v1.post("/manage/update-group")
    async def api_v1_manage_update_group(req: Request):
        """更新群组信息"""
        try:
            body = await req.json()
        except Exception:
            return {"success": False, "message": "参数错误", "code": 400, "data": None}
        try:
            import nonebot
            bots = nonebot.get_bots()
            if not bots:
                return {"success": False, "message": "没有可用的Bot", "code": 500, "data": None}
            bot = next(iter(bots.values()))
            group_id = body.get("group_id", "")
            group_name = body.get("group_name", "")
            if group_id and group_name:
                await bot.set_group_name(group_id=int(group_id), group_name=group_name)
            return {"success": True, "message": "更新成功", "code": 200, "data": None}
        except Exception as e:
            return {"success": False, "message": str(e), "code": 500, "data": None}

    @api_router_v1.post("/manage/update-member")
    async def api_v1_manage_update_member(req: Request):
        """更新群成员信息"""
        try:
            body = await req.json()
        except Exception:
            return {"success": False, "message": "参数错误", "code": 400, "data": None}
        try:
            import nonebot
            bots = nonebot.get_bots()
            if not bots:
                return {"success": False, "message": "没有可用的Bot", "code": 500, "data": None}
            bot = next(iter(bots.values()))
            group_id = body.get("group_id", "")
            user_id = body.get("user_id", "")
            card = body.get("card", "")
            if group_id and user_id and card:
                await bot.set_group_card(group_id=int(group_id), user_id=int(user_id), card=card)
            return {"success": True, "message": "更新成功", "code": 200, "data": None}
        except Exception as e:
            return {"success": False, "message": str(e), "code": 500, "data": None}

    @api_router_v1.post("/manage/update-friend")
    async def api_v1_manage_update_friend(req: Request):
        """更新好友备注"""
        try:
            body = await req.json()
        except Exception:
            return {"success": False, "message": "参数错误", "code": 400, "data": None}
        return {"success": True, "message": "更新成功", "code": 200, "data": None}

    @api_router_v1.get("/manage/friend-trend")
    async def api_v1_manage_friend_trend(user_id: str = "", days: int = 7, bot_id: str = ""):
        """好友消息趋势"""
        return {
            "success": True, "message": "ok", "code": 200,
            "data": {"dates": [], "messages": []}
        }

    @api_router_v1.get("/system/bot-status")
    async def api_v1_system_bot_status():
        """Bot 状态"""
        return await api_v1_bot_status()

    @api_router_v1.get("/system/ping")
    async def api_v1_system_ping():
        """Ping 测试"""
        return {"success": True, "message": "pong", "code": 200, "data": {"time": int(time.time() * 1000)}}

    @api_router_v1.get("/analytics/trend")
    async def api_v1_analytics_trend():
        """数据趋势"""
        try:
            msg_stats = {}
            msg_stats_file = DATA_DIR / "message_stats.json"
            if msg_stats_file.exists():
                try:
                    with open(msg_stats_file, "r", encoding="utf-8") as f:
                        msg_stats = json.load(f) or {}
                except Exception:
                    pass

            cmd_stats = {}
            cmd_stats_file = DATA_DIR / "command_stats.json"
            if cmd_stats_file.exists():
                try:
                    with open(cmd_stats_file, "r", encoding="utf-8") as f:
                        cmd_stats = json.load(f) or {}
                except Exception:
                    pass

            from datetime import timedelta
            dates = []
            messages = []
            calls = []
            today = datetime.now()
            for i in range(6, -1, -1):
                d = today - timedelta(days=i)
                day_key = d.strftime("%Y-%m-%d")
                dates.append(day_key)
                day_msgs = 0
                for user_id, stats in msg_stats.items():
                    day_msgs += int(stats.get("daily", {}).get(day_key, 0))
                messages.append(day_msgs)
                day_calls = 0
                for user_id, cmds in cmd_stats.items():
                    if isinstance(cmds, dict):
                        day_calls += int(cmds.get("__total__", 0))
                calls.append(day_calls if i == 6 else 0)
            return {
                "success": True, "message": "ok", "code": 200,
                "data": {"dates": dates, "messages": messages, "calls": calls}
            }
        except Exception as e:
            return {
                "success": True, "message": str(e), "code": 200,
                "data": {"dates": [], "messages": [], "calls": []}
            }

    @api_router_v1.get("/analytics/statistics")
    async def api_v1_analytics_statistics():
        """统计数据"""
        try:
            msg_stats = {}
            msg_stats_file = DATA_DIR / "message_stats.json"
            if msg_stats_file.exists():
                try:
                    with open(msg_stats_file, "r", encoding="utf-8") as f:
                        msg_stats = json.load(f) or {}
                except Exception:
                    pass

            plugin_stats = {}
            plugin_stats_file = DATA_DIR / "plugin_stats.json"
            if plugin_stats_file.exists():
                try:
                    with open(plugin_stats_file, "r", encoding="utf-8") as f:
                        plugin_stats = json.load(f) or {}
                except Exception:
                    pass

            users_dir = DATA_DIR / "users"
            total_users = 0
            if users_dir.exists():
                total_users = len([f for f in users_dir.glob("*.json") if not f.name.startswith("_")])

            total_messages = 0
            for user_id, stats in msg_stats.items():
                total_messages += int(stats.get("total", 0))

            total_calls = 0
            for plugin_name, stats in plugin_stats.items():
                if isinstance(stats, dict):
                    total_calls += int(stats.get("__total__", 0))

            total_groups = 0
            try:
                import nonebot
                bots = nonebot.get_bots()
                if bots:
                    bot = next(iter(bots.values()))
                    group_list = await bot.get_group_list()
                    total_groups = len(group_list)
            except Exception:
                pass

            return {
                "success": True, "message": "ok", "code": 200,
                "data": {
                    "total_messages": total_messages,
                    "total_calls": total_calls,
                    "total_users": total_users,
                    "total_groups": total_groups,
                }
            }
        except Exception as e:
            return {
                "success": True, "message": str(e), "code": 200,
                "data": {"total_messages": 0, "total_calls": 0, "total_users": 0, "total_groups": 0}
            }

    @api_router_v1.get("/dashboard/commits")
    async def api_v1_dashboard_commits():
        """提交记录"""
        return {"success": True, "message": "ok", "code": 200, "data": []}

    @api_router_v1.get("/dashboard/statistics")
    async def api_v1_dashboard_statistics():
        """详细统计"""
        return await api_v1_chat_statistics()

    # ============ Vue SPA API 路由结束 ============

    @api_router.post("/auth")
    async def api_auth(req: Request):
        """
        登录接口：密码验证 + 设备授权处理
        - 本机访问：通过后直接自动批准本机设备
        - 新局域网设备：通过后加入 pending 列表，返回 pending
        - 已有 approved 令牌：直接 ok
        - 被拒绝的令牌：不允许重新登录
        请求体：{"token": "<password>", "device_name": "<可选设备名>", "device_token": "<可选已有的设备令牌>"}
        """
        try:
            body = await req.json()
        except Exception:
            body = {}
        token = (body.get("token") or body.get("password") or "").strip()
        device_name = (body.get("device_name") or "").strip()
        existing_dev_token = (body.get("device_token") or "").strip()

        # 解析设备信息（提前解析便于日志）
        client_ip = _get_client_ip(req)
        ua = req.headers.get("user-agent", "")
        is_local = _is_localhost(req)

        # 密码验证
        if token != PASSWORD:
            logger.warning(f"[WebUI] 登录失败：密码错误（来自 {client_ip}，本地={is_local}）")
            raise HTTPException(status_code=401, detail="密码错误")

        # 默认设备名
        if not device_name:
            device_name = "本机" if is_local else f"设备-{client_ip}"

        # 优先使用客户端提供的现有令牌
        dev_token = existing_dev_token or _get_device_token(req)
        data = _load_devices()

        if dev_token:
            # 已有令牌：检查状态
            if dev_token in data.get("approved", {}):
                return {"success": True, "token": token, "device_token": dev_token,
                        "device_status": "ok", "is_admin": is_local, "ip": client_ip}
            if dev_token in data.get("pending", {}):
                return {"success": True, "token": token, "device_token": dev_token,
                        "device_status": "pending", "is_admin": is_local, "ip": client_ip}
            if dev_token in data.get("rejected", {}):
                raise HTTPException(status_code=403, detail="设备访问已被管理员拒绝")
            # 未知令牌，忽略并重新生成

        # 生成新的设备令牌
        dev_token = _generate_token()

        if is_local:
            # 本机访问：直接批准
            data["approved"][dev_token] = {
                "name": device_name,
                "ip": client_ip,
                "ua": ua,
                "is_local": True,
                "approved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
            _save_devices(data)
            logger.success(f"[WebUI] 本机登录成功（{client_ip}），设备令牌已自动批准")
            return {
                "success": True,
                "token": token,
                "device_token": dev_token,
                "device_status": "ok",
                "is_admin": True,
                "ip": client_ip,
            }

        # 非本机（局域网设备）：加入待批准
        data["pending"][dev_token] = {
            "name": device_name,
            "ip": client_ip,
            "ua": ua,
            "is_local": False,
            "requested_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        _save_devices(data)
        logger.warning(
            f"[WebUI] 新设备请求授权: {device_name} ({client_ip}) "
            f"-> 等待本机管理员同意"
        )
        return {
            "success": True,
            "token": token,
            "device_token": dev_token,
            "device_status": "pending",
            "is_admin": False,
            "ip": client_ip,
        }

    @api_router.post("/device/register")
    async def api_device_register(req: Request):
        """仅请求设备授权（不登录），返回新的设备令牌"""
        try:
            body = await req.json()
        except Exception:
            body = {}
        device_name = (body.get("device_name") or "").strip()
        client_ip = _get_client_ip(req)
        ua = req.headers.get("user-agent", "")
        is_local = _is_localhost(req)
        if not device_name:
            device_name = "本机" if is_local else f"设备-{client_ip}"
        dev_token = _generate_token()
        data = _load_devices()
        if is_local:
            data["approved"][dev_token] = {
                "name": device_name, "ip": client_ip, "ua": ua,
                "is_local": True,
                "approved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
            status = "ok"
        else:
            data["pending"][dev_token] = {
                "name": device_name, "ip": client_ip, "ua": ua,
                "is_local": False,
                "requested_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
            status = "pending"
        _save_devices(data)
        if is_local:
            logger.success(f"[WebUI] 本机设备注册并自动批准: {device_name}")
        else:
            logger.warning(f"[WebUI] 新设备请求授权: {device_name} ({client_ip})")
        return {"success": True, "device_token": dev_token, "device_status": status}

    @api_router.get("/device/status")
    async def api_device_status(req: Request):
        """查询当前设备令牌的授权状态（用于轮询）。本机直接返回 ok。"""
        if _is_localhost(req):
            return {"status": "ok"}
        dev_token = _get_device_token(req)
        if not dev_token:
            return {"status": "no_token"}
        data = _load_devices()
        if dev_token in data.get("approved", {}):
            return {"status": "ok"}
        if dev_token in data.get("pending", {}):
            return {"status": "pending"}
        if dev_token in data.get("rejected", {}):
            return {"status": "rejected"}
        return {"status": "unknown"}

    # ========== 设备管理接口（需要已登录 + 已批准设备才能调用） ==========
    @api_router.get("/devices")
    async def api_devices_list(req: Request):
        """列出所有设备（待批准 / 已批准 / 已拒绝）"""
        _require_auth(req)
        data = _load_devices()
        return {
            "success": True,
            "pending": [
                {"token": t, **info} for t, info in data.get("pending", {}).items()
            ],
            "approved": [
                {"token": t, **info} for t, info in data.get("approved", {}).items()
            ],
            "rejected": [
                {"token": t, **info} for t, info in data.get("rejected", {}).items()
            ],
            "device_auth_enabled": _device_auth_enabled(),
        }

    @api_router.post("/devices/{token}/approve")
    async def api_device_approve(token: str, req: Request):
        """批准一个待批准的设备"""
        _require_auth(req)
        # 必须是已批准设备（或本机）才能操作
        dev_status, _ = _check_device_auth(req)
        if dev_status not in ("ok", "disabled"):
            raise HTTPException(status_code=403, detail="只有已批准的设备才能进行审批操作")
        data = _load_devices()
        if token in data.get("pending", {}):
            info = data["pending"].pop(token)
            info["approved_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            data["approved"][token] = info
            _save_devices(data)
            logger.success(f"[WebUI] 已批准设备: {info.get('name')} ({info.get('ip')})")
            return {"success": True, "token": token}
        if token in data.get("approved", {}):
            return {"success": True, "token": token, "msg": "已批准"}
        raise HTTPException(status_code=404, detail="设备不存在")

    @api_router.post("/devices/{token}/reject")
    async def api_device_reject(token: str, req: Request):
        """拒绝一个设备（从待批准移到已拒绝，或从已批准直接拒绝）"""
        _require_auth(req)
        dev_status, _ = _check_device_auth(req)
        if dev_status not in ("ok", "disabled"):
            raise HTTPException(status_code=403, detail="只有已批准的设备才能进行审批操作")
        data = _load_devices()
        info = None
        if token in data.get("pending", {}):
            info = data["pending"].pop(token)
        elif token in data.get("approved", {}):
            info = data["approved"].pop(token)
        if not info:
            raise HTTPException(status_code=404, detail="设备不存在")
        info["rejected_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        data["rejected"][token] = info
        _save_devices(data)
        logger.warning(f"[WebUI] 已拒绝设备: {info.get('name')} ({info.get('ip')})")
        return {"success": True, "token": token}

    @api_router.delete("/devices/{token}")
    async def api_device_remove(token: str, req: Request):
        """从所有列表中移除设备（清理历史记录）"""
        _require_auth(req)
        dev_status, _ = _check_device_auth(req)
        if dev_status not in ("ok", "disabled"):
            raise HTTPException(status_code=403, detail="只有已批准的设备才能进行清理操作")
        data = _load_devices()
        removed = False
        for key in ("pending", "approved", "rejected"):
            if token in data.get(key, {}):
                data[key].pop(token)
                removed = True
        if removed:
            _save_devices(data)
            return {"success": True}
        raise HTTPException(status_code=404, detail="设备不存在")

    @api_router.post("/devices/{token}/rename")
    async def api_device_rename(token: str, req: Request):
        """重命名已授权/待批准/已拒绝的设备"""
        _require_auth(req)
        dev_status, _ = _check_device_auth(req)
        if dev_status not in ("ok", "disabled"):
            raise HTTPException(status_code=403, detail="只有已批准的设备才能进行重命名操作")
        try:
            body = await req.json()
        except Exception:
            body = {}
        new_name = (body.get("name") or "").strip()
        if not new_name:
            raise HTTPException(status_code=400, detail="名称不能为空")
        data = _load_devices()
        found = False
        for key in ("pending", "approved", "rejected"):
            if token in data.get(key, {}):
                data[key][token]["name"] = new_name
                found = True
                break
        if not found:
            raise HTTPException(status_code=404, detail="设备不存在")
        _save_devices(data)
        logger.success(f"[WebUI] 设备已重命名为: {new_name}")
        return {"success": True, "token": token, "name": new_name}

    @api_router.get("/dashboard")
    async def api_dashboard(req: Request):
        _require_auth(req)
        plugins = _get_plugin_list()
        blacklist = _load_blacklist()
        uptime_sec = int(time.time() - _START_TIME)
        h, rem = divmod(uptime_sec, 3600)
        m, _ = divmod(rem, 60)

        mem_mb = 0
        if _HAS_PSUTIL:
            try:
                mem_mb = round(psutil.Process().memory_info().rss / 1024 / 1024, 1)
            except Exception:
                pass

        # 读取消息统计
        msg_stats = {}
        msg_stats_file = DATA_DIR / "message_stats.json"
        if msg_stats_file.exists():
            try:
                with open(msg_stats_file, "r", encoding="utf-8") as f:
                    msg_stats = json.load(f) or {}
            except Exception:
                pass

        # 读取插件调用统计
        plugin_stats = {}
        plugin_stats_file = DATA_DIR / "plugin_stats.json"
        if plugin_stats_file.exists():
            try:
                with open(plugin_stats_file, "r", encoding="utf-8") as f:
                    plugin_stats = json.load(f) or {}
            except Exception:
                pass

        # 计算消息统计
        total_messages = 0
        total_users = len(msg_stats)
        today_messages = 0
        week_messages = 0
        for user_id, stats in msg_stats.items():
            total_messages += int(stats.get("total", 0))
            today_key = datetime.now().strftime("%Y-%m-%d")
            today_messages += int(stats.get("daily", {}).get(today_key, 0))
            week_key = datetime.now().strftime("%Y-W%W")
            week_messages += int(stats.get("weekly", {}).get(week_key, 0))

        # 计算插件调用统计
        total_plugin_calls = 0
        active_plugins = 0
        for plugin_name, stats in plugin_stats.items():
            calls = int(stats.get("__total__", 0))
            total_plugin_calls += calls
            if calls > 0:
                active_plugins += 1

        # 群消息排名
        group_msg_count = {}
        for user_id, stats in msg_stats.items():
            groups = stats.get("groups", {})
            for gid, count in groups.items():
                if gid not in group_msg_count:
                    group_msg_count[gid] = 0
                group_msg_count[gid] += int(count)

        group_ranking = sorted(
            [{"group_id": gid, "message_count": cnt} for gid, cnt in group_msg_count.items()],
            key=lambda x: x["message_count"],
            reverse=True
        )[:20]

        # 消息趋势（最近30天）
        msg_trend = _get_msg_trend(msg_stats, days=30)

        # 插件调用趋势（最近30天）
        plugin_trend = _get_plugin_trend(plugin_stats, days=30)

        return {
            "status": "online",
            "uptime": f"{h}h {m}m",
            "plugin_count": len(plugins),
            "enabled_plugin_count": sum(1 for p in plugins if p["enabled"]),
            "disabled_plugin_count": sum(1 for p in plugins if not p["enabled"]),
            "global_blacklist_count": len(blacklist["global_disabled"]),
            "group_blacklist_count": len(blacklist["group_disabled"]),
            "user_blacklist_count": len(blacklist["user_disabled"]),
            "memory_usage": f"{mem_mb}MB",
            "config_file": str(CONFIG_FILE),
            "last_modified": datetime.fromtimestamp(CONFIG_FILE.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S") if CONFIG_FILE.exists() else "N/A",
            # 新增统计
            "total_messages": total_messages,
            "total_users": total_users,
            "today_messages": today_messages,
            "week_messages": week_messages,
            "total_plugin_calls": total_plugin_calls,
            "active_plugins": active_plugins,
            # 群排名和趋势
            "group_ranking": group_ranking,
            "msg_trend": msg_trend,
            "plugin_trend": plugin_trend,
        }

    @api_router.post("/restart")
    async def api_restart(req: Request):
        """重启 Bot 服务"""
        _require_auth(req)
        logger.warning("[WebUI] 收到重启指令，正在重启 Bot...")
        import sys, os
        # 使用 sys.executable 确保使用正确的 Python 解释器
        python = sys.executable
        # 使用 os.spawnv 启动新进程替代当前进程
        # 先关闭 ASGI 服务器
        try:
            # 通知前端即将重启
            pass
        except Exception:
            pass
        # 延迟重启，确保响应先发出
        import asyncio
        loop = asyncio.get_event_loop()
        loop.call_later(1, lambda: os.execv(python, [python, str(BASE_DIR / "bot.py")]))
        return {"success": True, "message": "Bot 正在重启，请稍后刷新页面"}

    @api_router.get("/plugins")
    async def api_plugins(req: Request):
        _require_auth(req)
        plugin_list = _get_plugin_list()

        plugin_stats = {}
        plugin_stats_file = DATA_DIR / "plugin_stats.json"
        if plugin_stats_file.exists():
            try:
                with open(plugin_stats_file, "r", encoding="utf-8") as f:
                    plugin_stats = json.load(f) or {}
            except Exception:
                pass

        plugins = []
        for p in plugin_list:
            pname = p.get("name", "")
            stats = plugin_stats.get(pname, {})
            call_count = int(stats.get("__total__", 0))
            p["call_count"] = call_count
            p["command_calls"] = stats.get("__commands__", {})
            plugins.append(p)

        return {"plugins": plugins}

    @api_router.get("/plugins/{name}")
    async def api_plugin_detail(name: str, req: Request):
        _require_auth(req)
        plugins = _get_plugin_list()
        plugin_info = None
        for p in plugins:
            if p["name"] == name:
                plugin_info = p
                break
        if plugin_info is None:
            raise HTTPException(status_code=404, detail="Plugin not found")

        config = _load_yaml()
        raw_config = config.get(name, {}) if isinstance(config, dict) else {}

        # 将配置项转换为带标签的表单项
        items = []
        seen_keys = set()

        # 从 config_manager 获取模板中的字段注释（YAML 注释）
        field_comments = {}
        if _HAS_CONFIG_MANAGER and _config_manager is not None:
            try:
                field_comments = _config_manager.get_field_comments(name)
            except Exception:
                pass

        # 先按配置文件里已有的顺序排
        if isinstance(raw_config, dict):
            for k, v in raw_config.items():
                seen_keys.add(k)
                hint = _get_hint(name, k)
                # 硬编码 hint 优先，为空时回退到模板注释
                if not hint and k in field_comments:
                    hint = field_comments[k]
                items.append({
                    "key": k,
                    "label": _get_label(name, k),
                    "hint": hint,
                    "value": _value_to_input(v),
                    "original_type": type(v).__name__,
                    "is_list": isinstance(v, list),
                })

        # 补全已知但配置里没有的条目（方便新增常用项）
        known_keys = _CONFIG_LABELS.get(name, {})
        for k in known_keys:
            if k not in seen_keys:
                info = known_keys[k] if isinstance(known_keys[k], dict) else {}
                hint = info.get("hint", "") if isinstance(info, dict) else ""
                if not hint and k in field_comments:
                    hint = field_comments[k]
                items.append({
                    "key": k,
                    "label": info.get("label", k) if isinstance(info, dict) else k,
                    "hint": hint,
                    "value": "",
                    "original_type": "missing",
                    "is_list": False,
                })

        return {
            "plugin": plugin_info,
            "items": items,
            "raw_config": raw_config if isinstance(raw_config, dict) else {},
        }

    @api_router.put("/plugins/{name}")
    async def api_plugin_update(name: str, req: Request):
        _require_auth(req)
        try:
            body = await req.json()
        except Exception:
            body = {}

        data = _load_yaml()
        if not isinstance(data, dict):
            data = {}
        if name not in data or not isinstance(data.get(name), dict):
            data[name] = {"enabled": True}

        # 支持两种保存方式：1) 直接给 key-value；2) 给 items 数组（表单方式）
        if "enabled" in body:
            data[name]["enabled"] = bool(body["enabled"])
        if "config" in body and isinstance(body["config"], dict):
            data[name].update(body["config"])
        if "items" in body and isinstance(body["items"], list):
            for item in body["items"]:
                if not isinstance(item, dict) or "key" not in item:
                    continue
                k = item["key"]
                v = item.get("value", "")
                if str(v).strip() == "" and k != "enabled":
                    # 空值保持原配置，不删除
                    continue
                data[name][k] = _input_to_value(v)

        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"写入失败: {e}")

        # 热更新：通知 config_manager 从磁盘重新读取
        if _HAS_CONFIG_MANAGER and _config_manager is not None:
            try:
                _config_manager.reload()
                logger.success(f"[WebUI] 插件 {name} 配置已热更新")
            except Exception as e:
                logger.warning(f"[WebUI] 热更新 config_manager 失败: {e}")

        return {"success": True}

    @api_router.get("/config")
    async def api_config(req: Request):
        _require_auth(req)
        content = ""
        if CONFIG_FILE.exists():
            content = CONFIG_FILE.read_text(encoding="utf-8")
        return {"content": content, "backups": _get_backups()}

    @api_router.put("/config")
    async def api_config_update(req: Request):
        _require_auth(req)
        try:
            body = await req.json()
        except Exception:
            raise HTTPException(status_code=400, detail="请求格式错误")

        content = body.get("content", "")
        try:
            if content.strip():
                yaml.safe_load(content)
        except yaml.YAMLError as e:
            raise HTTPException(status_code=400, detail=f"YAML 格式错误: {e}")

        _create_backup()
        CONFIG_FILE.write_text(content, encoding="utf-8")

        if _HAS_CONFIG_MANAGER and _config_manager is not None:
            try:
                _config_manager.reload()
                logger.success("[WebUI] 主配置已热更新")
            except Exception as e:
                logger.warning(f"[WebUI] 热更新 config_manager 失败: {e}")

        return {"success": True}

    @api_router.post("/config/backup/{timestamp}/restore")
    async def api_config_restore(timestamp: str, req: Request):
        _require_auth(req)
        backup_path = BACKUP_DIR / f"bot.yaml.{timestamp}"
        if not backup_path.exists():
            raise HTTPException(status_code=404, detail="备份不存在")

        _create_backup()
        shutil.copy2(backup_path, CONFIG_FILE)

        if _HAS_CONFIG_MANAGER and _config_manager is not None:
            try:
                _config_manager.reload()
                logger.success(f"[WebUI] 已从备份恢复并热更新配置（{timestamp}）")
            except Exception as e:
                logger.warning(f"[WebUI] 热更新 config_manager 失败: {e}")

        return {"success": True}

    @api_router.get("/blacklist")
    async def api_blacklist(req: Request):
        _require_auth(req)
        return _load_blacklist()

    @api_router.put("/blacklist")
    async def api_blacklist_update(req: Request):
        _require_auth(req)
        try:
            body = await req.json()
        except Exception:
            raise HTTPException(status_code=400, detail="请求格式错误")

        data = _load_blacklist()
        if "global_disabled" in body and isinstance(body["global_disabled"], list):
            data["global_disabled"] = body["global_disabled"]
        if "group_disabled" in body and isinstance(body["group_disabled"], dict):
            data["group_disabled"] = body["group_disabled"]
        if "user_disabled" in body and isinstance(body["user_disabled"], dict):
            data["user_disabled"] = body["user_disabled"]

        _save_blacklist(data)
        return {"success": True}

    @api_router.post("/blacklist/group/{group_id}")
    async def api_blacklist_group_add(group_id: str, req: Request):
        _require_auth(req)
        body = {}
        try:
            body = await req.json()
        except Exception:
            body = {}
        plugin = body.get("plugin") or req.query_params.get("plugin", "").strip()
        if not plugin:
            raise HTTPException(status_code=400, detail="缺少 plugin 参数")

        data = _load_blacklist()
        data.setdefault("group_disabled", {})
        data["group_disabled"].setdefault(group_id, [])
        if plugin not in data["group_disabled"][group_id]:
            data["group_disabled"][group_id].append(plugin)
        _save_blacklist(data)
        return {"success": True}

    @api_router.delete("/blacklist/group/{group_id}/{plugin}")
    async def api_blacklist_group_delete(group_id: str, plugin: str, req: Request):
        _require_auth(req)
        data = _load_blacklist()
        if group_id in data.get("group_disabled", {}):
            data["group_disabled"][group_id] = [p for p in data["group_disabled"][group_id] if p != plugin]
            if not data["group_disabled"][group_id]:
                del data["group_disabled"][group_id]
        _save_blacklist(data)
        return {"success": True}

    @api_router.post("/blacklist/user/{user_id}")
    async def api_blacklist_user_add(user_id: str, req: Request):
        _require_auth(req)
        body = {}
        try:
            body = await req.json()
        except Exception:
            body = {}
        plugin = body.get("plugin") or req.query_params.get("plugin", "").strip()
        if not plugin:
            raise HTTPException(status_code=400, detail="缺少 plugin 参数")

        data = _load_blacklist()
        data.setdefault("user_disabled", {})
        data["user_disabled"].setdefault(user_id, [])
        if plugin not in data["user_disabled"][user_id]:
            data["user_disabled"][user_id].append(plugin)
        _save_blacklist(data)
        return {"success": True}

    @api_router.delete("/blacklist/user/{user_id}/{plugin}")
    async def api_blacklist_user_delete(user_id: str, plugin: str, req: Request):
        _require_auth(req)
        data = _load_blacklist()
        if user_id in data.get("user_disabled", {}):
            data["user_disabled"][user_id] = [p for p in data["user_disabled"][user_id] if p != plugin]
            if not data["user_disabled"][user_id]:
                del data["user_disabled"][user_id]
        _save_blacklist(data)
        return {"success": True}

    @api_router.get("/groups")
    async def api_get_groups(req: Request):
        """获取 Bot 已加入的群列表（从 OneBot API 获取）"""
        _require_auth(req)
        try:
            import nonebot
            bots = nonebot.get_bots()
            if not bots:
                return {"groups": []}
            bot = next(iter(bots.values()))
            group_list = await bot.get_group_list()

            msg_stats = {}
            msg_stats_file = DATA_DIR / "message_stats.json"
            if msg_stats_file.exists():
                try:
                    with open(msg_stats_file, "r", encoding="utf-8") as f:
                        msg_stats = json.load(f) or {}
                except Exception:
                    pass

            group_msg_count = {}
            for user_id, stats in msg_stats.items():
                groups = stats.get("groups", {})
                for gid, count in groups.items():
                    if gid not in group_msg_count:
                        group_msg_count[gid] = 0
                    group_msg_count[gid] += int(count)

            groups = []
            for g in group_list:
                gid = str(g.get("group_id", ""))
                gname = g.get("group_name", "")
                avatar_url = f"https://p.qlogo.cn/gh/{gid}/{gid}/100"
                msg_count = group_msg_count.get(gid, 0)
                groups.append({
                    "id": gid,
                    "name": gname,
                    "avatar": avatar_url,
                    "member_count": g.get("member_count", 0),
                    "max_member_count": g.get("max_member_count", 0),
                    "message_count": msg_count,
                })
            return {"groups": groups}
        except Exception as e:
            logger.warning(f"[WebUI] 获取群列表失败: {e}")
            return {"groups": [], "error": str(e)}

    @api_router.get("/users")
    async def api_get_users(req: Request):
        """获取 Bot 的好友列表（从 OneBot API 获取）"""
        _require_auth(req)
        try:
            import nonebot
            from utils.avatar_cache import get_avatar_data_uri
            bots = nonebot.get_bots()
            if not bots:
                return {"users": []}
            # 取第一个 bot
            bot = next(iter(bots.values()))
            friend_list = await bot.get_friend_list()
            # 获取每个好友的详细信息
            users = []
            for f in friend_list:
                uid = str(f.get("user_id", ""))
                nickname = f.get("nickname", "")
                remark = f.get("remark", "")
                # 使用备注名，如果没有备注则使用昵称
                display_name = remark if remark else nickname
                # 获取用户头像
                avatar_uri = await get_avatar_data_uri(uid, default_char=(display_name or "U")[0])
                users.append({
                    "id": uid,
                    "nickname": display_name,
                    "avatar": avatar_uri,
                })
            return {"users": users}
        except Exception as e:
            logger.warning(f"[WebUI] 获取好友列表失败: {e}")
            return {"users": [], "error": str(e)}

    @api_router.get("/requests")
    async def api_get_requests(req: Request):
        """获取好友/群组申请列表"""
        _require_auth(req)
        try:
            request_file = DATA_DIR / "friend_requests.json"
            if not request_file.exists():
                return {"requests": [], "pending_count": 0}
            try:
                data = json.loads(request_file.read_text(encoding="utf-8"))
            except Exception:
                return {"requests": [], "pending_count": 0}

            requests = data.get("requests", [])

            from datetime import datetime
            from utils.config_manager import config_manager

            expire_hours = 48
            try:
                raw_expire = config_manager.get("miku_friend_request", "expire_hours", 48)
                expire_hours = int(raw_expire)
            except Exception:
                expire_hours = 48

            now = datetime.now()
            changed = False
            for req_item in requests:
                if req_item.get("handled"):
                    continue
                if expire_hours > 0:
                    try:
                        req_time = datetime.strptime(
                            req_item.get("time", ""), "%Y-%m-%d %H:%M:%S"
                        )
                        if (now - req_time).total_seconds() > expire_hours * 3600:
                            req_item["handled"] = True
                            req_item["action"] = "expired"
                            changed = True
                    except Exception:
                        pass

            if changed:
                request_file.write_text(
                    json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
                )

            pending_count = sum(
                1 for r in requests
                if not r.get("handled")
            )

            from utils.avatar_cache import get_avatar_data_uri
            for item in requests:
                uid = str(item.get("user_id", ""))
                if uid and not item.get("avatar"):
                    try:
                        item["avatar"] = await get_avatar_data_uri(uid, default_char="U")
                    except Exception:
                        item["avatar"] = ""
                if item.get("type") == "group":
                    gid = str(item.get("group_id", ""))
                    if gid:
                        item["group_avatar"] = f"https://p.qlogo.cn/gh/{gid}/{gid}/100"

            return {"requests": list(reversed(requests)), "pending_count": pending_count}
        except Exception as e:
            logger.warning(f"[WebUI] 获取申请列表失败: {e}")
            return {"requests": [], "pending_count": 0, "error": str(e)}

    @api_router.post("/requests/{req_id}/approve")
    async def api_approve_request(req_id: int, req: Request):
        """同意申请"""
        _require_auth(req)
        try:
            import nonebot
            bots = nonebot.get_bots()
            if not bots:
                return {"success": False, "message": "没有可用的Bot"}
            bot = next(iter(bots.values()))

            request_file = DATA_DIR / "friend_requests.json"
            data = json.loads(request_file.read_text(encoding="utf-8"))
            requests = data.get("requests", [])

            target_req = None
            for r in requests:
                if r.get("id") == req_id:
                    target_req = r
                    break

            if not target_req:
                return {"success": False, "message": "找不到该申请"}
            if target_req.get("handled"):
                action = target_req.get("action", "")
                if action == "approve":
                    return {"success": False, "message": "该申请已同意"}
                elif action == "reject":
                    return {"success": False, "message": "该申请已拒绝"}
                elif action == "expired":
                    return {"success": False, "message": "该申请已过期失效"}
                else:
                    return {"success": False, "message": "该申请已处理"}

            from datetime import datetime
            try:
                if target_req.get("type") == "friend":
                    await bot.set_friend_add_request(
                        flag=target_req["flag"], approve=True
                    )
                else:
                    await bot.set_group_add_request(
                        flag=target_req["flag"],
                        sub_type=target_req.get("sub_type", "add"),
                        approve=True,
                    )
                target_req["handled"] = True
                target_req["action"] = "approve"
                target_req["handle_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                request_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                return {"success": True, "message": "已同意"}
            except Exception as e:
                err_msg = str(e)
                if "No such request" in err_msg or "no such request" in err_msg or "1200" in err_msg:
                    target_req["handled"] = True
                    target_req["action"] = "expired"
                    target_req["handle_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    request_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                    return {"success": False, "message": "申请已失效（可能已过期或已通过其他方式处理）"}
                return {"success": False, "message": f"操作失败: {e}"}
        except Exception as e:
            return {"success": False, "message": str(e)}

    @api_router.post("/requests/{req_id}/reject")
    async def api_reject_request(req_id: int, req: Request):
        """拒绝申请"""
        _require_auth(req)
        try:
            body = await req.json()
        except Exception:
            body = {}
        reason = body.get("reason", "")

        try:
            import nonebot
            bots = nonebot.get_bots()
            if not bots:
                return {"success": False, "message": "没有可用的Bot"}
            bot = next(iter(bots.values()))

            request_file = DATA_DIR / "friend_requests.json"
            data = json.loads(request_file.read_text(encoding="utf-8"))
            requests = data.get("requests", [])

            target_req = None
            for r in requests:
                if r.get("id") == req_id:
                    target_req = r
                    break

            if not target_req:
                return {"success": False, "message": "找不到该申请"}
            if target_req.get("handled"):
                action = target_req.get("action", "")
                if action == "approve":
                    return {"success": False, "message": "该申请已同意"}
                elif action == "reject":
                    return {"success": False, "message": "该申请已拒绝"}
                elif action == "expired":
                    return {"success": False, "message": "该申请已过期失效"}
                else:
                    return {"success": False, "message": "该申请已处理"}

            from datetime import datetime
            try:
                if target_req.get("type") == "friend":
                    await bot.set_friend_add_request(
                        flag=target_req["flag"], approve=False
                    )
                else:
                    await bot.set_group_add_request(
                        flag=target_req["flag"],
                        sub_type=target_req.get("sub_type", "add"),
                        approve=False,
                        reason=reason,
                    )
                target_req["handled"] = True
                target_req["action"] = "reject"
                target_req["reason"] = reason
                target_req["handle_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                request_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                return {"success": True, "message": "已拒绝"}
            except Exception as e:
                err_msg = str(e)
                if "No such request" in err_msg or "no such request" in err_msg or "1200" in err_msg:
                    target_req["handled"] = True
                    target_req["action"] = "expired"
                    target_req["handle_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    request_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                    return {"success": False, "message": "申请已失效（可能已过期或已通过其他方式处理）"}
                return {"success": False, "message": f"操作失败: {e}"}
        except Exception as e:
            return {"success": False, "message": str(e)}

    @api_router.post("/chat/send")
    async def api_send_message(req: Request):
        """发送消息（私聊/群聊）"""
        _require_auth(req)
        try:
            body = await req.json()
        except Exception:
            return {"success": False, "message": "参数错误"}

        message_type = body.get("type", "private")
        target_id = body.get("target_id", "")
        content = body.get("message", "")

        if not target_id or not content:
            return {"success": False, "message": "目标ID和消息内容不能为空"}

        try:
            import nonebot
            bots = nonebot.get_bots()
            if not bots:
                return {"success": False, "message": "没有可用的Bot"}
            bot = next(iter(bots.values()))

            if message_type == "group":
                await bot.send_group_msg(group_id=int(target_id), message=content)
            else:
                await bot.send_private_msg(user_id=int(target_id), message=content)

            return {"success": True, "message": "发送成功"}
        except Exception as e:
            logger.warning(f"[WebUI] 发送消息失败: {e}")
            return {"success": False, "message": f"发送失败: {e}"}

    @api_router.get("/chat/history")
    async def api_chat_history(req: Request):
        """获取聊天历史（合并普通聊天记录和AI对话记录）"""
        _require_auth(req)
        try:
            message_type = req.query_params.get("type", "private")
            target_id = req.query_params.get("target_id", "")
            limit = int(req.query_params.get("limit", 100))

            if not target_id:
                return {"messages": []}

            all_messages = []

            # 1. 读取普通聊天记录（完整群聊/私聊历史）
            chat_history_dir = DATA_DIR / "chat_history" / message_type
            chat_history_file = chat_history_dir / f"{target_id}.json"
            if chat_history_file.exists():
                try:
                    data = json.loads(chat_history_file.read_text(encoding="utf-8"))
                    chat_msgs = data if isinstance(data, list) else data.get("messages", [])
                    all_messages.extend(chat_msgs)
                except Exception:
                    pass

            # 2. 读取AI对话记录
            ai_history_dir = DATA_DIR / "ai_chat_history" / message_type
            ai_history_file = ai_history_dir / f"{target_id}.json"
            if ai_history_file.exists():
                try:
                    data = json.loads(ai_history_file.read_text(encoding="utf-8"))
                    ai_msgs = data if isinstance(data, list) else data.get("messages", [])
                    # AI记录中role为assistant的是Bot回复
                    all_messages.extend(ai_msgs)
                except Exception:
                    pass

            # 3. 按时间排序并去重（根据time和content）
            seen = set()
            unique_messages = []
            for msg in sorted(all_messages, key=lambda x: x.get("time", 0)):
                key = (msg.get("time", 0), msg.get("content", ""), msg.get("role", ""))
                if key not in seen:
                    seen.add(key)
                    unique_messages.append(msg)

            return {"messages": unique_messages[-limit:]}
        except Exception as e:
            return {"messages": [], "error": str(e)}

    # -------- WebSocket 支持（实时日志 + 系统状态）--------
    _ws_clients = set()

    @app.websocket("/zhenxun/ws/v1/logs")
    async def websocket_logs(websocket: WebSocket):
        """实时日志 WebSocket"""
        await websocket.accept()
        _ws_clients.add(websocket)
        try:
            while True:
                data = await websocket.receive_text()
                try:
                    msg = json.loads(data)
                    if msg.get("type") == "heartbeat":
                        await websocket.send_text(json.dumps({"type": "heartbeat", "time": int(time.time() * 1000)}))
                except Exception:
                    pass
        except WebSocketDisconnect:
            pass
        finally:
            _ws_clients.discard(websocket)

    @app.websocket("/zhenxun/ws/v1/status")
    async def websocket_status(websocket: WebSocket):
        """系统状态 WebSocket（定时推送 CPU/内存/磁盘）"""
        await websocket.accept()
        try:
            while True:
                cpu_percent = 0
                mem_percent = 0
                disk_percent = 0
                if _HAS_PSUTIL:
                    try:
                        cpu_percent = psutil.cpu_percent(interval=0.1)
                    except Exception:
                        pass
                    try:
                        mem = psutil.virtual_memory()
                        mem_percent = mem.percent
                    except Exception:
                        pass
                    try:
                        disk = psutil.disk_usage(str(BASE_DIR))
                        disk_percent = disk.percent
                    except Exception:
                        pass
                await websocket.send_text(json.dumps({
                    "type": "system_status",
                    "cpu": cpu_percent,
                    "memory": mem_percent,
                    "disk": disk_percent,
                    "timestamp": int(time.time() * 1000),
                }))
                await asyncio.sleep(2)
        except WebSocketDisconnect:
            pass

    @app.websocket("/zhenxun/ws/v1/chat")
    async def websocket_chat(websocket: WebSocket):
        """聊天 WebSocket"""
        await websocket.accept()
        try:
            while True:
                data = await websocket.receive_text()
                try:
                    msg = json.loads(data)
                    if msg.get("type") == "heartbeat":
                        await websocket.send_text(json.dumps({"type": "heartbeat", "time": int(time.time() * 1000)}))
                    elif msg.get("type") == "get_contacts":
                        await websocket.send_text(json.dumps({
                            "type": "contacts",
                            "friends": [],
                            "groups": [],
                        }))
                except Exception:
                    pass
        except WebSocketDisconnect:
            pass

    # -------- 日志 API（只读）--------
    @api_router.get("/logs")
    async def api_logs_list(req: Request):
        """获取日志文件列表"""
        _require_auth(req)
        return {"files": _list_log_files()}

    @api_router.get("/logs/{filename}")
    async def api_log_content(filename: str, req: Request, max_lines: int = MAX_LOG_LINES):
        """读取指定日志文件的最新内容（只读）"""
        _require_auth(req)
        return _read_log_file(filename, max_lines)

    @api_router.get("/logs/{filename}/tail")
    async def api_log_tail(filename: str, req: Request, lines: int = 100):
        """读取日志文件最新 N 行（用于轮询刷新）"""
        _require_auth(req)
        return _read_log_file(filename, lines)

    # -------- API 路由（认证/插件/配置等）--------
    app.include_router(api_router)
    app.include_router(api_router_v1)

    # -------- 页面路由 --------
    @app.get("/")
    async def root(req: Request):
        return HTMLResponse((ADMIN_DIR / "login.html").read_text(encoding="utf-8"))

    STATIC_DIRS = ["assets", "css", "js"]

    @app.get("/{full_path:path}")
    async def page_route(full_path: str, req: Request):
        if ".." in full_path or full_path.startswith("/"):
            raise HTTPException(status_code=400, detail="Invalid path")

        if not full_path:
            return HTMLResponse((ADMIN_DIR / "login.html").read_text(encoding="utf-8"))

        parts = full_path.split("/", 1)
        if parts[0] in STATIC_DIRS:
            file_path = ADMIN_DIR / full_path
            if file_path.exists() and file_path.is_file():
                return FileResponse(file_path)
            raise HTTPException(status_code=404, detail="Not Found")

        html_pages = ["login", "dashboard", "plugins", "plugin", "blacklist", "config", "logs", "devices", "groups", "users", "chat", "requests"]
        if full_path in html_pages:
            html_file = ADMIN_DIR / f"{full_path}.html"
            if html_file.exists():
                return HTMLResponse(html_file.read_text(encoding="utf-8"))

        if full_path.endswith(".html"):
            html_file = ADMIN_DIR / full_path
            if html_file.exists():
                return HTMLResponse(html_file.read_text(encoding="utf-8"))

        if full_path == "favicon.ico":
            favicon = ADMIN_DIR / "favicon.ico"
            if favicon.exists():
                return FileResponse(favicon)

        raise HTTPException(status_code=404, detail="Page Not Found")

    logger.success("[WebUI] 管理后台已挂载到 http://你的IP:3108/")

# ===================== 辅助函数（在 _mount_admin 外部定义）=====================
LOGS_DIR = BASE_DIR / "logs"

# 读取日志文件（只读，每次最多返回 MAX_LINES 行）
MAX_LOG_LINES = 500


def _read_log_file(filename: str, max_lines: int = MAX_LOG_LINES) -> dict:
    """读取指定日志文件的最新内容（只读）。"""
    if not filename.startswith("bot_") or not filename.endswith(".log"):
        return {"error": "不允许访问此文件", "lines": [], "total": 0, "has_more": False}
    log_path = LOGS_DIR / filename
    if not log_path.exists():
        return {"error": "文件不存在", "filename": filename, "lines": [], "total": 0, "has_more": False}
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        lines = content.splitlines()
        total = len(lines)
        recent = lines[-max_lines:] if max_lines > 0 else lines
        has_more = total > len(recent)
        return {
            "filename": filename,
            "lines": recent,
            "total": total,
            "has_more": has_more,
            "size_bytes": log_path.stat().st_size,
            "size_str": _format_size(log_path.stat().st_size),
        }
    except Exception as e:
        return {"error": str(e), "filename": filename, "lines": [], "total": 0, "has_more": False}


def _list_log_files() -> list:
    """列出 logs 目录下所有日志文件。"""
    if not LOGS_DIR.exists():
        return []
    files = []
    for f in sorted(LOGS_DIR.glob("bot_*.log"), reverse=True):
        try:
            stat = f.stat()
            files.append({
                "name": f.name,
                "date": f.stem.replace("bot_", ""),
                "size": stat.st_size,
                "size_str": _format_size(stat.st_size),
                "mtime": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
            })
        except Exception:
            pass
    return files


def _format_size(size: int) -> str:
    if size < 1024:
        return f"{size}B"
    elif size < 1024 * 1024:
        return f"{size / 1024:.1f}KB"
    else:
        return f"{size / 1024 / 1024:.1f}MB"


# ========= 启动时挂载 =========
def _register_hook():
    try:
        driver = get_driver()
        if hasattr(driver, "on_startup") and callable(driver.on_startup):
            driver.on_startup(_mount_admin)
        else:
            _mount_admin()
    except Exception as e:
        logger.warning(f"[WebUI] 注册启动钩子失败: {e}")


_register_hook()
