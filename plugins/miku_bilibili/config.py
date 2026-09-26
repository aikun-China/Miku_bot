"""
Miku B站插件 - 配置管理模块
使用 miku_bot 的 config_manager 注册和管理配置
"""

import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from utils.config_manager import config_manager
from nonebot.log import logger

PLUGIN_NAME = "miku_bilibili"

_BILIBILI_TEMPLATE = """
miku_bilibili:
  # 是否启用B站链接解析
  enabled: true
  # 是否启用被动解析（自动解析消息中的链接）
  auto_parse: true
  # 被动解析缓存时间（分钟），同一链接在此时间内不重复解析
  cache_ttl_minutes: 5
  # 响应风格：card（图片卡片）/ text（纯文本）
  response_style: card
  # 卡片宽度
  card_width: 600
  # 卡片高度
  card_height: 500
  # 请求超时时间（秒）
  request_timeout: 10
  # 是否启用视频下载功能
  enable_download: true
  # 解析成功后是否自动下载视频
  enable_auto_download: true
  # 自动下载最大视频时长（分钟），超过不自动下载，0=不限制
  auto_download_max_duration: 5
  # 下载画质（16=360P, 32=480P, 64=720P, 80=1080P, 112=1080P+, 116=4K）
  # 注：720P及以上画质需要登录B站账号
  download_quality: 32
  # 最大下载时长（分钟），超过不下载，0=不限制
  max_download_duration: 10
  # 是否启用B站订阅功能
  enable_subscribe: true
  # 订阅检查间隔（分钟）
  subscribe_interval: 5
  # 是否启用@全体成员提醒（需要机器人管理员权限）
  enable_at_all: false
  # 默认UP主推送类型：dynamic, video, live（用逗号分隔）
  default_up_push_types: dynamic,video
  # 默认直播推送类型：live
  default_live_push_types: live
  # 是否启用动态广告过滤
  enable_ad_filter: false
  # 是否发送动态原图
  enable_dynamic_image: false
  # 新内容时间阈值（分钟），超过此时间的更新不推送
  new_content_threshold_minutes: 30
"""

_cfg = config_manager.register_plugin(
    PLUGIN_NAME,
    defaults={
        "enabled": True,
        "auto_parse": True,
        "cache_ttl_minutes": 5,
        "response_style": "card",
        "card_width": 600,
        "card_height": 500,
        "request_timeout": 10,
        "enable_download": True,
        "enable_auto_download": True,
        "auto_download_max_duration": 5,
        "download_quality": 32,
        "max_download_duration": 10,
        "enable_subscribe": True,
        "subscribe_interval": 5,
        "enable_at_all": False,
        "default_up_push_types": "dynamic,video",
        "default_live_push_types": "live",
        "enable_ad_filter": False,
        "enable_dynamic_image": False,
        "new_content_threshold_minutes": 30,
    },
    template_str=_BILIBILI_TEMPLATE,
    description="B站链接解析+订阅插件配置",
)


def get_config(key: str, default: Any = None) -> Any:
    """获取配置项"""
    return config_manager.get(PLUGIN_NAME, key, default)


def set_config(key: str, value: Any) -> None:
    """设置配置项"""
    config_manager.set(PLUGIN_NAME, key, value)


def is_enabled() -> bool:
    """检查插件是否启用"""
    raw = get_config("enabled", True)
    return str(raw).strip().lower() not in ("false", "0", "no", "")


def is_auto_parse() -> bool:
    """检查是否启用被动解析"""
    raw = get_config("auto_parse", True)
    return str(raw).strip().lower() not in ("false", "0", "no", "")


def is_download_enabled() -> bool:
    """检查下载功能是否启用"""
    raw = get_config("enable_download", True)
    return str(raw).strip().lower() not in ("false", "0", "no", "")


def is_auto_download_enabled() -> bool:
    """检查解析成功后是否自动下载视频"""
    raw = get_config("enable_auto_download", True)
    return str(raw).strip().lower() not in ("false", "0", "no", "")


def get_auto_download_max_duration() -> int:
    """获取自动下载最大视频时长（分钟），0表示不限制"""
    try:
        return int(get_config("auto_download_max_duration", 5))
    except (ValueError, TypeError):
        return 5


def is_subscribe_enabled() -> bool:
    """检查订阅功能是否启用"""
    raw = get_config("enable_subscribe", True)
    return str(raw).strip().lower() not in ("false", "0", "no", "")


def get_response_style() -> str:
    """获取响应风格"""
    return str(get_config("response_style", "card") or "card")


def get_request_timeout() -> float:
    """获取请求超时时间（秒）"""
    try:
        return float(get_config("request_timeout", 10))
    except (ValueError, TypeError):
        return 10.0


def get_download_quality() -> int:
    """获取下载画质"""
    try:
        return int(get_config("download_quality", 32))
    except (ValueError, TypeError):
        return 32


def get_max_download_duration() -> int:
    """获取最大下载时长（分钟），0表示不限制"""
    try:
        return int(get_config("max_download_duration", 10))
    except (ValueError, TypeError):
        return 10


def get_subscribe_interval() -> int:
    """获取订阅检查间隔（分钟）"""
    try:
        return int(get_config("subscribe_interval", 5))
    except (ValueError, TypeError):
        return 5


def get_cache_ttl_minutes() -> int:
    """获取缓存TTL（分钟）"""
    try:
        return int(get_config("cache_ttl_minutes", 5))
    except (ValueError, TypeError):
        return 5


def get_card_width() -> int:
    """获取卡片宽度"""
    try:
        return int(get_config("card_width", 600))
    except (ValueError, TypeError):
        return 600


def get_card_height() -> int:
    """获取卡片高度"""
    try:
        return int(get_config("card_height", 500))
    except (ValueError, TypeError):
        return 500


def is_at_all_enabled() -> bool:
    """检查是否启用@全体成员"""
    raw = get_config("enable_at_all", False)
    return str(raw).strip().lower() in ("true", "1", "yes")


def get_default_up_push_types() -> list[str]:
    """获取默认UP主推送类型列表"""
    raw = str(get_config("default_up_push_types", "dynamic,video") or "dynamic,video")
    return [t.strip() for t in raw.split(",") if t.strip()]


def get_default_live_push_types() -> list[str]:
    """获取默认直播推送类型列表"""
    raw = str(get_config("default_live_push_types", "live") or "live")
    return [t.strip() for t in raw.split(",") if t.strip()]


def is_ad_filter_enabled() -> bool:
    """检查是否启用广告过滤"""
    raw = get_config("enable_ad_filter", False)
    return str(raw).strip().lower() in ("true", "1", "yes")


def is_dynamic_image_enabled() -> bool:
    """检查是否发送动态原图"""
    raw = get_config("enable_dynamic_image", False)
    return str(raw).strip().lower() in ("true", "1", "yes")


def get_new_content_threshold_minutes() -> int:
    """获取新内容时间阈值（分钟）"""
    try:
        return int(get_config("new_content_threshold_minutes", 30))
    except (ValueError, TypeError):
        return 30


DATA_DIR = PROJECT_ROOT / "data" / "miku_bilibili"
DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = DATA_DIR / "bilibili.db"
COOKIE_FILE = DATA_DIR / "bili_cookies.json"
DOWNLOAD_DIR = DATA_DIR / "downloads"
CACHE_DIR = DATA_DIR / "cache"
AVATAR_CACHE_DIR = CACHE_DIR / "avatars"
BANGUMI_COVER_CACHE_DIR = CACHE_DIR / "bangumi_covers"
IMAGE_CACHE_DIR = CACHE_DIR / "images"
DYNAMIC_PATH = CACHE_DIR / "dynamics"

for d in [DOWNLOAD_DIR, CACHE_DIR, AVATAR_CACHE_DIR, BANGUMI_COVER_CACHE_DIR, IMAGE_CACHE_DIR, DYNAMIC_PATH]:
    d.mkdir(parents=True, exist_ok=True)

logger.info(f"[miku_bilibili] 配置模块已加载，数据目录: {DATA_DIR}")
