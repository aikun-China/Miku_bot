"""
Miku B站插件 - 消息渲染模块
构建视频/直播/番剧/用户/专栏的展示消息
支持纯文本和图片卡片两种模式
"""

import sys
import base64
from pathlib import Path
from typing import Optional, List, Any

from nonebot.adapters.onebot.v11 import MessageSegment, Message
from nonebot.log import logger

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from utils.html_render import render_template
from utils.screenshot import screenshot_html, to_image_uri
from utils.bg_helper import find_and_load_bg

from .config import (
    get_response_style, get_card_width, get_card_height,
)
from .model import VideoInfo, LiveInfo, SeasonInfo, UserInfo, ArticleInfo
from .cache import download_image, IMAGE_CACHE_DIR


TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)


# ============================================================
# 格式化工具
# ============================================================

def format_number(n: int) -> str:
    """格式化数字"""
    if n >= 100000000:
        return f"{n / 100000000:.1f}亿"
    if n >= 10000:
        return f"{n / 10000:.1f}万"
    return str(n)


def format_duration(seconds: int) -> str:
    """格式化时长"""
    if seconds <= 0:
        return "0:00"
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def format_date(timestamp: int) -> str:
    """格式化日期"""
    if timestamp <= 0:
        return "未知"
    from datetime import datetime
    dt = datetime.fromtimestamp(timestamp)
    return dt.strftime("%Y-%m-%d")


def format_datetime(timestamp: int) -> str:
    """格式化日期时间"""
    if timestamp <= 0:
        return "未知"
    from datetime import datetime
    dt = datetime.fromtimestamp(timestamp)
    return dt.strftime("%Y-%m-%d %H:%M")


def html_escape(s) -> str:
    """HTML转义"""
    if s is None:
        return ""
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


async def cover_to_data_uri(url: str) -> str:
    """下载封面图并转base64 data URI"""
    if not url:
        return ""
    try:
        import hashlib
        safe_key = hashlib.md5(url.encode()).hexdigest()
        cache_path = IMAGE_CACHE_DIR / f"{safe_key}.jpg"

        if not cache_path.exists():
            await download_image(url, cache_path)

        if cache_path.exists():
            with open(cache_path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("ascii")
            return f"data:image/jpeg;base64,{b64}"
    except Exception as e:
        logger.warning(f"[miku_bilibili] 封面下载失败: {e}")
    return ""


# ============================================================
# 纯文本消息构建
# ============================================================

def build_video_text(info: VideoInfo) -> str:
    """构建视频纯文本消息"""
    lines = [
        f"🎬 {info.title}",
        f"👤 UP主: {info.owner_name}",
        f"▶ 播放: {format_number(info.view)}  👍 点赞: {format_number(info.like)}",
        f"💰 投币: {format_number(info.coin)}  ⭐ 收藏: {format_number(info.favorite)}",
        f"⏱ {format_duration(info.duration)}  📅 {format_date(info.pubdate)}",
        f"🔗 {info.url}",
    ]
    if info.desc:
        desc = info.desc[:100] + "..." if len(info.desc) > 100 else info.desc
        lines.append(f"📝 {desc}")
    return "\n".join(lines)


def build_live_text(info: LiveInfo) -> str:
    """构建直播纯文本消息"""
    status = "🔴 直播中" if info.live_status == 1 else ("⚫ 轮播中" if info.live_status == 2 else "⚫ 未开播")
    lines = [
        f"📺 {info.title}",
        f"👤 主播: {info.uname or f'UID {info.uid}'}",
        f"📊 状态: {status}  👥 在线: {format_number(info.online)}",
        f"🏷 分区: {info.parent_area_name} / {info.area_name}",
        f"🔗 {info.room_url or info.url}",
    ]
    if info.live_status == 1 and info.live_start_time:
        lines.insert(3, f"⏰ 开播时间: {format_datetime(info.live_start_time)}")
    return "\n".join(lines)


def build_user_text(info: UserInfo) -> str:
    """构建用户纯文本消息"""
    lines = [
        f"👤 {info.name} (Lv.{info.level})",
        f"🔢 UID: {info.mid}",
        f"👥 关注: {format_number(info.following)}  粉丝: {format_number(info.follower)}",
    ]
    if info.live_room_status == 1:
        lines.append(f"🔴 直播中: {info.live_room_title}")
    if info.sign:
        sign = info.sign[:80] + "..." if len(info.sign) > 80 else info.sign
        lines.append(f"📝 签名: {sign}")
    lines.append(f"🔗 空间: {info.url}")
    return "\n".join(lines)


def build_season_text(info: SeasonInfo) -> str:
    """构建番剧纯文本消息"""
    status_map = {2: "未开播", 4: "会员抢先", 13: "已完结"}
    status = status_map.get(info.status, f"状态未知({info.status})")

    lines = [
        f"🎬 {info.title} ({info.type_name})",
        f"📊 状态: {status} (共{info.total_ep}话)",
        f"🌍 地区: {info.areas or '未知'}  🎭 风格: {info.styles or '未知'}",
    ]
    if info.rating_score > 0:
        lines.append(f"⭐ 评分: {info.rating_score}分 ({info.rating_count}人评价)")
    lines.append(f"▶ 播放: {format_number(info.stat_views)}  ⭐ 追番: {format_number(info.stat_favorites)}")
    lines.append(f"🔗 {info.url}")
    if info.target_ep_title:
        lines.append(f"📺 当前: {info.target_ep_long_title or info.target_ep_title}")
    return "\n".join(lines)


def build_article_text(info: ArticleInfo) -> str:
    """构建专栏纯文本消息"""
    lines = []
    if info.title:
        lines.append(f"📝 【专栏】{info.title}")
    if info.author:
        lines.append(f"👤 作者: {info.author}")
    if info.markdown_content:
        import re
        plain = re.sub(r"[#*`~_>]", "", info.markdown_content)
        plain = re.sub(r"!\[.*?\]\(.*?\)", "[图片]", plain)
        plain = re.sub(r"\[(.*?)\]\(.*?\)", r"\1", plain)
        plain = re.sub(r"\n\s*\n", "\n", plain).strip()
        summary = plain[:150] + "..." if len(plain) > 150 else plain
        lines.append(f"📄 摘要: {summary}")
    if info.url:
        lines.append(f"🔗 {info.url}")
    return "\n".join(lines) if lines else "无法获取专栏信息"


# ============================================================
# 图片卡片渲染
# ============================================================

def _bilibili_icon_data_uri() -> str:
    """加载 B站 SVG 图标并转 data URI（白色填充）"""
    import re
    icon_path = TEMPLATES_DIR / "icons" / "bilibili.svg"
    if not icon_path.exists():
        return ""
    try:
        svg_text = icon_path.read_text(encoding="utf-8")
        svg_text = re.sub(r'fill="[^"]*"', 'fill="currentColor"', svg_text)
        svg_b64 = base64.b64encode(svg_text.encode("utf-8")).decode("ascii")
        return f"data:image/svg+xml;base64,{svg_b64}"
    except Exception:
        return ""


async def render_video_card(info: VideoInfo) -> Optional[str]:
    """渲染视频信息为图片卡片，返回图片路径"""
    try:
        bg_uri = find_and_load_bg("bilibili_card", TEMPLATES_DIR)
        cover_uri = await cover_to_data_uri(info.pic)

        if cover_uri:
            cover_html = f'<img src="{cover_uri}" alt="封面">'
        else:
            cover_html = '<div class="cover-placeholder">视频</div>'

        owner_name = info.owner_name or "U"
        # 加载UP主真实头像，失败回退首字
        up_avatar_uri = await cover_to_data_uri(info.owner_face) if info.owner_face else ""
        if up_avatar_uri:
            author_avatar_html = f'<img src="{up_avatar_uri}" alt="头像">'
        else:
            author_avatar_html = html_escape(owner_name[0])

        desc_text = info.desc or ""
        desc_html = ""
        if desc_text.strip():
            desc_short = desc_text[:100] + ("..." if len(desc_text) > 100 else "")
            desc_html = f'<div class="desc-box">{html_escape(desc_short)}</div>'

        # 右上角 B站图标
        bilibili_icon_uri = _bilibili_icon_data_uri()
        if bilibili_icon_uri:
            header_tag_html = f'<img class="header-tag-icon" src="{bilibili_icon_uri}" alt="">'
        else:
            header_tag_html = "B站"

        # B站主题色（粉）
        color1, color2 = "#fb7299", "#ff9eb5"

        html = render_template(
            "bilibili_card",
            TEMPLATES_DIR,
            bg_data_uri=bg_uri,
            header_tag_html=header_tag_html,
            theme_color1=color1,
            theme_color2=color2,
            platform_name="哔哩哔哩",
            cover_html=cover_html,
            title=html_escape(info.title),
            author=html_escape(info.owner_name),
            author_avatar_html=author_avatar_html,
            author_tag_text="UP主",
            stat1=format_number(info.view),
            stat_label1="播放",
            stat2=format_number(info.like),
            stat_label2="点赞",
            stat3=format_number(info.coin),
            stat_label3="投币",
            stat4=format_number(info.favorite),
            stat_label4="收藏",
            desc_html=desc_html,
        )

        img_path = await screenshot_html(html, width=600, height=1298)
        return str(img_path)
    except Exception as e:
        logger.warning(f"[miku_bilibili] 视频卡片渲染失败: {e}")
        return None


async def render_live_card(info: LiveInfo) -> Optional[str]:
    """渲染直播信息为图片卡片，返回图片路径"""
    try:
        bg_uri = find_and_load_bg("bilibili_live", TEMPLATES_DIR)
        cover_uri = await cover_to_data_uri(info.cover)
        face_uri = await cover_to_data_uri(info.face) if info.face else ""

        status_text = "直播中" if info.live_status == 1 else ("轮播中" if info.live_status == 2 else "未开播")
        status_class = "live" if info.live_status == 1 else "offline"

        start_time = format_datetime(info.live_start_time) if info.live_status == 1 and info.live_start_time else ""

        html = render_template(
            "bilibili_live",
            TEMPLATES_DIR,
            bg_data_uri=bg_uri,
            cover_src=cover_uri,
            face_src=face_uri,
            title=html_escape(info.title),
            uname=html_escape(info.uname or f"UID {info.uid}"),
            status_text=status_text,
            status_class=status_class,
            area=html_escape(f"{info.parent_area_name} / {info.area_name}"),
            start_time=start_time,
            online=format_number(info.online),
            room_url=html_escape(info.room_url or info.url),
        )

        img_path = await screenshot_html(html, width=500, height=600)
        return str(img_path)
    except Exception as e:
        logger.warning(f"[miku_bilibili] 直播卡片渲染失败: {e}")
        return None


async def render_user_card(info: UserInfo) -> Optional[str]:
    """渲染用户信息为图片卡片，返回图片路径"""
    try:
        bg_uri = find_and_load_bg("bilibili_user", TEMPLATES_DIR)
        face_uri = await cover_to_data_uri(info.face)
        top_photo_uri = await cover_to_data_uri(info.top_photo) if info.top_photo else ""

        live_status_text = "直播中" if info.live_room_status == 1 else ""

        html = render_template(
            "bilibili_user",
            TEMPLATES_DIR,
            bg_data_uri=bg_uri,
            top_photo_src=top_photo_uri,
            face_src=face_uri,
            name=html_escape(info.name),
            level=info.level,
            sex=html_escape(info.sex),
            birthday=html_escape(info.birthday),
            sign=html_escape(info.sign or "这个人很神秘，什么都没有写..."),
            following=format_number(info.following),
            follower=format_number(info.follower),
            likes=format_number(info.likes),
            archive_view=format_number(info.archive_view),
            article_view=format_number(info.article_view),
            live_status_text=live_status_text,
            live_title=html_escape(info.live_room_title),
            live_url=html_escape(info.live_room_url),
            space_url=html_escape(info.url),
        )

        img_path = await screenshot_html(html, width=500, height=700)
        return str(img_path)
    except Exception as e:
        logger.warning(f"[miku_bilibili] 用户卡片渲染失败: {e}")
        return None


async def render_season_card(info: SeasonInfo) -> Optional[str]:
    """渲染番剧信息为图片卡片，返回图片路径"""
    try:
        bg_uri = find_and_load_bg("bilibili_season", TEMPLATES_DIR)
        cover_url = info.target_ep_cover or info.cover
        cover_uri = await cover_to_data_uri(cover_url)

        status_map = {2: "未开播", 4: "会员抢先", 13: "已完结"}
        status_text = status_map.get(info.status, f"状态({info.status})")

        title = info.target_ep_long_title or info.target_ep_title or info.title
        season_title = info.title if info.target_ep_id else ""

        rating_text = f"{info.rating_score}分" if info.rating_score > 0 else "暂无评分"

        html = render_template(
            "bilibili_season",
            TEMPLATES_DIR,
            bg_data_uri=bg_uri,
            cover_src=cover_uri,
            title=html_escape(title),
            season_title=html_escape(season_title),
            type_name=html_escape(info.type_name),
            status_text=status_text,
            total_ep=info.total_ep,
            areas=html_escape(info.areas),
            styles=html_escape(info.styles),
            rating_text=rating_text,
            rating_count=format_number(info.rating_count),
            desc=html_escape(info.desc or "暂无简介"),
            views=format_number(info.stat_views),
            danmakus=format_number(info.stat_danmakus),
            favorites=format_number(info.stat_favorites),
            reply=format_number(info.stat_reply),
            likes=format_number(info.stat_likes),
            coins=format_number(info.stat_coins),
            share=format_number(info.stat_share),
            url=html_escape(info.url),
        )

        img_path = await screenshot_html(html, width=420, height=700)
        return str(img_path)
    except Exception as e:
        logger.warning(f"[miku_bilibili] 番剧卡片渲染失败: {e}")
        return None


# ============================================================
# 统一消息构建接口
# ============================================================

async def build_video_message(info: VideoInfo) -> Message:
    """构建视频消息（根据配置选择卡片或文本）"""
    msg = Message()

    if get_response_style() == "card":
        img_path = await render_video_card(info)
        if img_path:
            msg.append(MessageSegment.image(to_image_uri(img_path)))
            return msg
        logger.warning("[miku_bilibili] 图片渲染失败，回退到文本")

    msg.append(build_video_text(info))
    return msg


async def build_live_message(info: LiveInfo) -> Message:
    """构建直播消息"""
    msg = Message()

    if get_response_style() == "card":
        img_path = await render_live_card(info)
        if img_path:
            msg.append(MessageSegment.image(to_image_uri(img_path)))
            return msg

    msg.append(build_live_text(info))
    return msg


async def build_user_message(info: UserInfo) -> Message:
    """构建用户消息"""
    msg = Message()

    if get_response_style() == "card":
        img_path = await render_user_card(info)
        if img_path:
            msg.append(MessageSegment.image(to_image_uri(img_path)))
            return msg

    msg.append(build_user_text(info))
    return msg


async def build_season_message(info: SeasonInfo) -> Message:
    """构建番剧消息"""
    msg = Message()

    if get_response_style() == "card":
        img_path = await render_season_card(info)
        if img_path:
            msg.append(MessageSegment.image(to_image_uri(img_path)))
            return msg

    msg.append(build_season_text(info))
    return msg


async def build_article_message(info: ArticleInfo) -> Message:
    """构建专栏消息"""
    msg = Message()
    if info.screenshot_bytes:
        msg.append(MessageSegment.image(info.screenshot_bytes))
    else:
        msg.append(build_article_text(info))
    return msg
