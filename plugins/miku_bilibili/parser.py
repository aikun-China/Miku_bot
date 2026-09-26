"""
Miku B站插件 - URL解析模块
从文本/消息中提取和解析B站URL
"""

import re
import json
from typing import List, Tuple, Optional, Any

from nonebot.log import logger
from nonebot.adapters.onebot.v11 import MessageEvent, Message

from .model import ContentType, UrlParseError, UnsupportedUrlError
from .api import resolve_short_url


class BilibiliUrlParser:
    """B站URL解析器"""

    VIDEO_PATTERNS = [
        re.compile(r"https?://(?:www\.|m\.)?bilibili\.com/video/(BV[0-9A-Za-z]{10}|av\d+)", re.I),
        re.compile(r"(?<![A-Za-z0-9])(BV[0-9A-Za-z]{10})(?![A-Za-z0-9])"),
        re.compile(r"(?<![A-Za-z0-9])av(\d{3,12})(?![A-Za-z0-9])", re.I),
    ]

    SHORT_URL_PATTERN = re.compile(r"https?://b23\.tv/[A-Za-z0-9]+", re.I)

    LIVE_PATTERNS = [
        re.compile(r"https?://live\.bilibili\.com/(\d+)", re.I),
        re.compile(r"(?<![A-Za-z0-9])live\.bilibili\.com/(\d+)", re.I),
    ]

    ARTICLE_PATTERNS = [
        re.compile(r"https?://(?:www\.|m\.)?bilibili\.com/read/(cv\d+)", re.I),
        re.compile(r"(?<![A-Za-z0-9])(cv\d+)(?![A-Za-z0-9])", re.I),
    ]

    SEASON_PATTERNS = [
        re.compile(r"https?://(?:www\.|m\.)?bilibili\.com/bangumi/play/(ss\d+|ep\d+)", re.I),
        re.compile(r"(?<![A-Za-z0-9])(ss\d{1,8}|ep\d{1,8})(?![A-Za-z0-9])", re.I),
    ]

    USER_PATTERNS = [
        re.compile(r"https?://space\.bilibili\.com/(\d+)", re.I),
        re.compile(r"(?<![A-Za-z0-9])space\.bilibili\.com/(\d+)", re.I),
    ]

    OPUS_PATTERNS = [
        re.compile(r"https?://(?:www\.bilibili\.com/opus/|t\.bilibili\.com/)(\d+)", re.I),
    ]

    @classmethod
    def extract_all(cls, text: str) -> List[Tuple[str, ContentType]]:
        """
        从文本中提取所有B站链接和类型
        返回: [(url_or_id, ContentType), ...]
        """
        results = []
        seen = set()

        logger.debug(f"[miku_bilibili] extract_all 输入文本: {text[:200]}...")

        # 1. 短链接优先
        for m in re.finditer(cls.SHORT_URL_PATTERN, text):
            url = m.group(0)
            key = f"short:{url}"
            logger.debug(f"[miku_bilibili] 匹配短链接: {url}")
            if key not in seen:
                seen.add(key)
                results.append((url, ContentType.VIDEO))

        # 2. 视频URL
        for i, pattern in enumerate(cls.VIDEO_PATTERNS):
            matches = list(re.finditer(pattern, text))
            if matches:
                logger.debug(f"[miku_bilibili] 视频模式{i} 匹配到 {len(matches)} 个")
            for m in matches:
                full_match = m.group(0)
                vid = m.group(1) if m.lastindex else full_match
                logger.debug(f"[miku_bilibili] 视频匹配: full={full_match[:50]}, vid={vid}")
                if full_match.lower().startswith("av"):
                    vid_key = f"video:av{vid.lower()}"
                else:
                    vid_key = f"video:{vid.upper()}"
                if vid_key not in seen:
                    seen.add(vid_key)
                    results.append((full_match, ContentType.VIDEO))

        # 3. 直播URL
        for pattern in cls.LIVE_PATTERNS:
            for m in re.finditer(pattern, text):
                room_id = m.group(1)
                key = f"live:{room_id}"
                if key not in seen:
                    seen.add(key)
                    results.append((m.group(0), ContentType.LIVE))

        # 4. 专栏URL
        for pattern in cls.ARTICLE_PATTERNS:
            for m in re.finditer(pattern, text):
                cv_id = m.group(1).lower()
                key = f"article:{cv_id}"
                if key not in seen:
                    seen.add(key)
                    results.append((m.group(0), ContentType.ARTICLE))

        # 5. 番剧URL
        for pattern in cls.SEASON_PATTERNS:
            for m in re.finditer(pattern, text):
                ss_or_ep = m.group(1).lower()
                key = f"season:{ss_or_ep}"
                if key not in seen:
                    seen.add(key)
                    results.append((m.group(0), ContentType.SEASON))

        # 6. 用户空间URL（仅匹配完整URL）
        for pattern in cls.USER_PATTERNS:
            for m in re.finditer(pattern, text):
                uid = m.group(1)
                key = f"user:{uid}"
                if key not in seen:
                    seen.add(key)
                    results.append((m.group(0), ContentType.USER))

        # 7. 动态URL
        for pattern in cls.OPUS_PATTERNS:
            for m in re.finditer(pattern, text):
                opus_id = m.group(1)
                key = f"opus:{opus_id}"
                if key not in seen:
                    seen.add(key)
                    results.append((m.group(0), ContentType.OPUS))

        return results


def extract_bilibili_from_json(json_str: str) -> str:
    """从QQ JSON卡片消息中提取B站链接相关文本"""
    try:
        data = json.loads(json_str)
        urls = []

        def _extract_urls(obj):
            if isinstance(obj, str):
                if ("bilibili.com" in obj.lower() or "b23.tv" in obj.lower()
                        or re.search(r"BV[0-9A-Za-z]{10}", obj)):
                    urls.append(obj)
            elif isinstance(obj, dict):
                for v in obj.values():
                    _extract_urls(v)
            elif isinstance(obj, list):
                for item in obj:
                    _extract_urls(item)

        _extract_urls(data)
        return " ".join(urls)
    except Exception:
        if "bilibili.com" in json_str.lower() or "b23.tv" in json_str.lower():
            return json_str
        return ""


def extract_text_from_message(message: Message) -> str:
    """从消息中提取所有文本（包括JSON卡片中的内容）"""
    text_parts = [str(message)]

    for seg in message:
        if seg.type == "json":
            try:
                json_data_str = seg.data.get("data", "") if hasattr(seg, "data") else ""
                if json_data_str:
                    extracted = extract_bilibili_from_json(json_data_str)
                    if extracted:
                        text_parts.append(extracted)
                        logger.debug("[miku_bilibili] 从JSON卡片消息段提取到内容")
            except Exception as e:
                logger.debug(f"[miku_bilibili] 解析JSON消息段失败: {e}")
        elif seg.type == "reply":
            pass

    return "\n".join(text_parts).strip()


async def extract_bilibili_from_event(event: MessageEvent) -> Optional[str]:
    """从事件中提取B站URL（包括回复消息）"""
    try:
        if hasattr(event, "reply") and event.reply:
            reply_msg = event.reply.message
            reply_text = extract_text_from_message(reply_msg)
            items = BilibiliUrlParser.extract_all(reply_text)
            if items:
                logger.info(f"[miku_bilibili] 从回复消息提取到B站链接")
                return items[0][0]
    except Exception as e:
        logger.debug(f"[miku_bilibili] 检查回复消息失败: {e}")

    try:
        current_msg = event.get_message()
        current_text = extract_text_from_message(current_msg)
        items = BilibiliUrlParser.extract_all(current_text)
        if items:
            return items[0][0]
    except Exception as e:
        logger.debug(f"[miku_bilibili] 从当前消息提取URL失败: {e}")

    return None


async def parse_url(url: str) -> Tuple[ContentType, str]:
    """
    解析B站URL，返回内容类型和资源ID
    会自动解析短链接
    """
    original_url = url.strip()
    current_url = original_url

    # 处理短链接
    if "b23.tv" in current_url.lower():
        resolved = await resolve_short_url(current_url)
        if resolved:
            current_url = resolved
            logger.debug(f"[miku_bilibili] 短链接解析结果: {resolved}")
        else:
            raise UrlParseError(f"短链接解析失败: {original_url}")

    # 视频
    for pattern in BilibiliUrlParser.VIDEO_PATTERNS:
        m = pattern.search(current_url)
        if m:
            vid = m.group(1) if m.lastindex else m.group(0)
            if m.group(0).lower().startswith("av"):
                vid = f"av{vid}"
            return ContentType.VIDEO, vid

    # 直播
    for pattern in BilibiliUrlParser.LIVE_PATTERNS:
        m = pattern.search(current_url)
        if m:
            return ContentType.LIVE, m.group(1)

    # 专栏
    for pattern in BilibiliUrlParser.ARTICLE_PATTERNS:
        m = pattern.search(current_url)
        if m:
            cv_id = m.group(1).lower()
            return ContentType.ARTICLE, cv_id

    # 番剧
    for pattern in BilibiliUrlParser.SEASON_PATTERNS:
        m = pattern.search(current_url)
        if m:
            return ContentType.SEASON, m.group(1).lower()

    # 用户
    for pattern in BilibiliUrlParser.USER_PATTERNS:
        m = pattern.search(current_url)
        if m:
            return ContentType.USER, m.group(1)

    # 动态
    for pattern in BilibiliUrlParser.OPUS_PATTERNS:
        m = pattern.search(current_url)
        if m:
            return ContentType.OPUS, m.group(1)

    raise UnsupportedUrlError(f"不支持的URL格式: {original_url}")


def extract_video_id(text: str) -> Optional[str]:
    """从文本中提取视频ID（BV/AV）"""
    items = BilibiliUrlParser.extract_all(text)
    video_items = [item for item in items if item[1] == ContentType.VIDEO]
    if video_items:
        return video_items[0][0]
    return None


def extract_live_room_id(text: str) -> Optional[str]:
    """从文本中提取直播间ID"""
    items = BilibiliUrlParser.extract_all(text)
    live_items = [item for item in items if item[1] == ContentType.LIVE]
    if live_items:
        m = re.search(r"\d+", live_items[0][0])
        if m:
            return m.group(0)
    return None
