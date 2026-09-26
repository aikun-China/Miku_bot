"""
Miku B站插件 - 数据模型模块
定义所有数据结构，使用 dataclass 实现
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, List, Dict, Any


class ContentType(Enum):
    """内容类型"""
    VIDEO = "video"
    LIVE = "live"
    ARTICLE = "article"
    SEASON = "season"
    USER = "user"
    OPUS = "opus"
    UNKNOWN = "unknown"


class SubType(Enum):
    """订阅类型"""
    UP = "up"
    LIVE = "live"
    SEASON = "season"


class NotificationType(Enum):
    """通知类型"""
    DYNAMIC = "dynamic"
    VIDEO = "video"
    LIVE = "live"


# ============================================================
# 解析相关模型
# ============================================================

@dataclass
class VideoInfo:
    """视频信息"""
    bvid: str
    aid: int = 0
    title: str = ""
    desc: str = ""
    pic: str = ""
    owner_name: str = ""
    owner_mid: int = 0
    owner_face: str = ""
    view: int = 0
    like: int = 0
    coin: int = 0
    favorite: int = 0
    share: int = 0
    reply: int = 0
    danmaku: int = 0
    duration: int = 0
    pubdate: int = 0
    ctime: int = 0
    tid: int = 0
    tname: str = ""
    url: str = ""
    pages: List[Dict[str, Any]] = field(default_factory=list)
    ai_summary: str = ""
    online_count: str = ""
    first_frame: str = ""
    short_link: str = ""


@dataclass
class LiveInfo:
    """直播信息"""
    room_id: int = 0
    short_id: int = 0
    uid: int = 0
    title: str = ""
    cover: str = ""
    live_status: int = 0
    live_start_time: int = 0
    area_id: int = 0
    area_name: str = ""
    parent_area_id: int = 0
    parent_area_name: str = ""
    description: str = ""
    uname: str = ""
    face: str = ""
    room_url: str = ""
    space_url: str = ""
    keyframe_url: str = ""
    online: int = 0
    url: str = ""


@dataclass
class ArticleInfo:
    """专栏/动态信息"""
    id: str
    type: str = "article"
    url: str = ""
    title: str = ""
    author: str = ""
    markdown_content: str = ""
    screenshot_bytes: Optional[bytes] = None
    screenshot_path: str = ""
    publish_time: int = 0


@dataclass
class SeasonInfo:
    """番剧/影视信息"""
    season_id: int = 0
    media_id: int = 0
    title: str = ""
    cover: str = ""
    desc: str = ""
    type_name: str = ""
    areas: str = ""
    styles: str = ""
    publish: Dict[str, Any] = field(default_factory=dict)
    rating_score: float = 0.0
    rating_count: int = 0
    total_ep: int = 0
    status: int = 0
    url: str = ""
    target_ep_id: int = 0
    target_ep_title: str = ""
    target_ep_long_title: str = ""
    target_ep_cover: str = ""
    stat_views: int = 0
    stat_danmakus: int = 0
    stat_reply: int = 0
    stat_favorites: int = 0
    stat_coins: int = 0
    stat_share: int = 0
    stat_likes: int = 0


@dataclass
class UserInfo:
    """用户信息"""
    mid: int = 0
    name: str = ""
    face: str = ""
    sign: str = ""
    level: int = 0
    sex: str = "保密"
    birthday: str = ""
    top_photo: str = ""
    live_room_status: int = 0
    live_room_url: str = ""
    live_room_title: str = ""
    following: int = 0
    follower: int = 0
    archive_view: int = 0
    article_view: int = 0
    likes: int = 0
    url: str = ""


# ============================================================
# 订阅相关模型
# ============================================================

@dataclass
class SubscribeItem:
    """订阅项"""
    id: int = 0
    sub_type: SubType = SubType.UP
    target_id: str = ""
    name: str = ""
    face: str = ""
    room_id: str = ""
    enable_dynamic: bool = True
    enable_video: bool = True
    enable_live: bool = True
    at_all_dynamic: bool = False
    at_all_video: bool = False
    at_all_live: bool = False
    last_dynamic_ts: int = 0
    last_video_bvid: str = ""
    last_video_created: int = 0
    last_live_status: int = 0
    last_season_ep_id: int = 0
    created_at: float = 0.0
    updated_at: float = 0.0


@dataclass
class SubscriptionTarget:
    """订阅目标关系（订阅项与会话的关系）"""
    id: int = 0
    sub_id: int = 0
    target_id: str = ""
    created_at: float = 0.0


@dataclass
class Notification:
    """通知数据"""
    content: List[Any]
    type: NotificationType
    at_all: bool = False


# ============================================================
# 异常类
# ============================================================

class BilibiliBaseException(Exception):
    """B站插件基础异常"""

    def __init__(self, message: str = "", context: Optional[Dict[str, Any]] = None, cause: Optional[Exception] = None):
        self.message = message
        self.context = context or {}
        self.cause = cause
        super().__init__(message)


class UrlParseError(BilibiliBaseException):
    """URL解析错误"""
    pass


class UnsupportedUrlError(BilibiliBaseException):
    """不支持的URL格式"""
    pass


class ShortUrlError(BilibiliBaseException):
    """短链接解析错误"""
    pass


class BilibiliRequestError(BilibiliBaseException):
    """B站请求错误"""
    pass


class BilibiliResponseError(BilibiliBaseException):
    """B站响应错误"""
    pass


class ResourceNotFoundError(BilibiliBaseException):
    """资源未找到"""
    pass


class ResourceForbiddenError(BilibiliBaseException):
    """资源访问被禁止"""
    pass


class RateLimitError(BilibiliBaseException):
    """请求频率限制"""

    def __init__(self, message: str = "", retry_after: int = 60, context: Optional[Dict[str, Any]] = None, cause: Optional[Exception] = None):
        super().__init__(message, context, cause)
        self.retry_after = retry_after


class DownloadError(BilibiliBaseException):
    """下载错误"""
    pass
