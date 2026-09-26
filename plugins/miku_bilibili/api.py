"""
Miku B站插件 - B站API调用模块
封装所有B站API请求
"""

from typing import Dict, Any, Optional, List, Tuple
import re

from curl_cffi import requests as curl_requests
from nonebot.log import logger

from .config import get_request_timeout
from .credential import get_headers_with_cookie, get_cookies_dict, get_wbi_keys, sign_wbi
from .model import (
    VideoInfo, LiveInfo, ArticleInfo, SeasonInfo, UserInfo,
    BilibiliRequestError, BilibiliResponseError, ResourceNotFoundError,
    ResourceForbiddenError, RateLimitError,
)

# 全局 curl_cffi 会话单例（模拟 Chrome TLS 指纹，规避B站风控）
_client: Optional[curl_requests.AsyncSession] = None


async def _get_client() -> curl_requests.AsyncSession:
    """获取全局会话单例（复用连接池，模拟 chrome131 浏览器指纹）"""
    global _client
    if _client is None:
        _client = curl_requests.AsyncSession(
            impersonate="chrome131",
            timeout=get_request_timeout(),
            allow_redirects=True,
        )
    return _client


async def close_client():
    """关闭全局会话（程序退出时调用）"""
    global _client
    if _client is not None:
        await _client.close()
        _client = None


async def _retry_async(coro_func, max_retries: int = 2, delay: float = 1.0):
    """带重试的异步调用"""
    last_error = None
    for attempt in range(max_retries + 1):
        try:
            return await coro_func()
        except (curl_requests.errors.RequestsError, OSError, TimeoutError) as e:
            last_error = e
            if attempt < max_retries:
                logger.warning(f"[miku_bilibili] API请求失败（第{attempt + 1}次），{delay}秒后重试: {e}")
                import asyncio
                await asyncio.sleep(delay)
            else:
                raise
        except RateLimitError:
            raise
        except Exception as e:
            raise
    raise last_error


# ============================================================
# 视频API
# ============================================================

async def fetch_video_info(vid: str) -> VideoInfo:
    """获取视频信息，支持BV号和AV号"""
    params = {}
    if vid.upper().startswith("BV"):
        params["bvid"] = vid
    elif vid.lower().startswith("av"):
        try:
            params["aid"] = int(vid[2:])
        except ValueError:
            raise ResourceNotFoundError(f"无效的AV号: {vid}")
    else:
        raise ResourceNotFoundError(f"不支持的视频ID格式: {vid}")

    async def _fetch():
        client = await _get_client()
        r = await client.get(
            "https://api.bilibili.com/x/web-interface/view",
            params=params,
            headers=get_headers_with_cookie(),
        )
        r.raise_for_status()
        return r.json()

    data = await _retry_async(_fetch)

    if data.get("code") != 0:
        code = data.get("code")
        msg = data.get("message", "未知错误")
        if code == -403:
            raise ResourceForbiddenError(f"视频访问被禁止: {msg}")
        elif code in [-404, 62002]:
            raise ResourceNotFoundError(f"视频不存在: {msg}")
        elif code == -412:
            raise RateLimitError(f"请求频率过高: {msg}", retry_after=60)
        else:
            raise BilibiliResponseError(f"获取视频信息失败: {msg}", context={"code": code})

    v = data.get("data", {})
    stat = v.get("stat", {})
    owner = v.get("owner", {})

    return VideoInfo(
        bvid=v.get("bvid", ""),
        aid=v.get("aid", 0),
        title=v.get("title", "未知标题"),
        desc=v.get("desc", "") or "暂无简介",
        pic=v.get("pic", ""),
        owner_name=owner.get("name", "未知UP主"),
        owner_mid=owner.get("mid", 0),
        owner_face=owner.get("face", ""),
        view=int(stat.get("view", 0)),
        like=int(stat.get("like", 0)),
        coin=int(stat.get("coin", 0)),
        favorite=int(stat.get("favorite", 0)),
        share=int(stat.get("share", 0)),
        reply=int(stat.get("reply", 0)),
        danmaku=int(stat.get("danmaku", 0)),
        duration=int(v.get("duration", 0)),
        pubdate=int(v.get("pubdate", 0)),
        ctime=int(v.get("ctime", 0)),
        tid=int(v.get("tid", 0)),
        tname=v.get("tname", ""),
        url=f"https://www.bilibili.com/video/{v.get('bvid', vid)}",
        pages=v.get("pages", []),
    )


async def fetch_video_download_url(bvid: str, qn: int = 32, fnval: int = 16) -> Dict[str, Any]:
    """获取视频下载链接"""
    async def _fetch():
        client = await _get_client()
        r = await client.get(
            "https://api.bilibili.com/x/player/playurl",
            params={"bvid": bvid, "qn": qn, "fnval": fnval, "fnver": 0, "fourk": 1},
            headers=get_headers_with_cookie(),
        )
        r.raise_for_status()
        return r.json()

    data = await _retry_async(_fetch)
    if data.get("code") != 0:
        raise BilibiliResponseError(
            f"获取下载链接失败: {data.get('message', '未知错误')}",
            context={"code": data.get("code")}
        )
    return data.get("data", {})


# ============================================================
# 直播API
# ============================================================

async def fetch_live_info(room_id: str) -> LiveInfo:
    """获取直播间信息"""
    async def _fetch():
        client = await _get_client()
        r = await client.get(
            "https://api.live.bilibili.com/xlive/web-room/v1/index/getInfoByRoom",
            params={"room_id": room_id},
            headers=get_headers_with_cookie(),
        )
        r.raise_for_status()
        return r.json()

    data = await _retry_async(_fetch)

    if data.get("code") != 0:
        code = data.get("code")
        msg = data.get("message", "未知错误")
        if code == -404:
            raise ResourceNotFoundError(f"直播间不存在: {msg}")
        elif code == -412:
            raise RateLimitError(f"请求频率过高: {msg}", retry_after=60)
        else:
            raise BilibiliResponseError(f"获取直播间信息失败: {msg}")

    room_data = data.get("data", {})
    room_info = room_data.get("room_info", {})
    anchor_info = room_data.get("anchor_info", {}).get("base_info", {})

    return LiveInfo(
        room_id=room_info.get("room_id", int(room_id)),
        short_id=room_info.get("short_id", 0),
        uid=room_info.get("uid", 0),
        title=room_info.get("title", ""),
        cover=room_info.get("cover", ""),
        live_status=room_info.get("live_status", 0),
        live_start_time=room_info.get("live_start_time", 0),
        area_id=room_info.get("area_id", 0),
        area_name=room_info.get("area_name", ""),
        parent_area_id=room_info.get("parent_area_id", 0),
        parent_area_name=room_info.get("parent_area_name", ""),
        description=room_info.get("description", ""),
        uname=anchor_info.get("uname", ""),
        face=anchor_info.get("face", ""),
        room_url=f"https://live.bilibili.com/{room_info.get('room_id', room_id)}",
        space_url=f"https://space.bilibili.com/{room_info.get('uid', 0)}",
        keyframe_url=room_info.get("keyframe", ""),
        online=room_info.get("online", 0),
        url=f"https://live.bilibili.com/{room_id}",
    )


# ============================================================
# 专栏/动态API
# ============================================================

async def fetch_article_info(cv_id: str) -> ArticleInfo:
    """获取专栏文章信息"""
    article_id = cv_id
    if cv_id.lower().startswith("cv"):
        try:
            article_id = cv_id[2:]
        except (ValueError, IndexError):
            raise ResourceNotFoundError(f"无效的专栏ID: {cv_id}")

    async def _fetch():
        client = await _get_client()
        r = await client.get(
            "https://api.bilibili.com/x/article/viewinfo",
            params={"id": article_id},
            headers=get_headers_with_cookie(),
        )
        r.raise_for_status()
        return r.json()

    data = await _retry_async(_fetch)

    if data.get("code") != 0:
        code = data.get("code")
        msg = data.get("message", "未知错误")
        if code == -404:
            raise ResourceNotFoundError(f"专栏不存在: {msg}")
        else:
            raise BilibiliResponseError(f"获取专栏信息失败: {msg}")

    v = data.get("data", {})
    return ArticleInfo(
        id=cv_id,
        type="article",
        title=v.get("title", ""),
        author=v.get("author_name", ""),
        publish_time=v.get("publish_time", 0),
        url=f"https://www.bilibili.com/read/cv{article_id}",
    )


# ============================================================
# 用户API
# ============================================================

async def fetch_user_info(uid: str) -> UserInfo:
    """获取用户信息"""
    async def _fetch():
        client = await _get_client()
        r = await client.get(
            "https://api.bilibili.com/x/web-interface/card",
            params={"mid": uid, "photo": "true"},
            headers=get_headers_with_cookie(),
        )
        r.raise_for_status()
        return r.json()

    data = await _retry_async(_fetch)

    if data.get("code") != 0:
        code = data.get("code")
        msg = data.get("message", "未知错误")
        if code == -404:
            raise ResourceNotFoundError(f"用户不存在: {msg}")
        else:
            raise BilibiliResponseError(f"获取用户信息失败: {msg}")

    card = data.get("data", {}).get("card", {})
    live_room = card.get("live_room", {})

    return UserInfo(
        mid=card.get("mid", int(uid)),
        name=card.get("name", ""),
        face=card.get("face", ""),
        sign=card.get("sign", ""),
        level=card.get("level", 0),
        sex=card.get("sex", "保密"),
        birthday=card.get("birthday", ""),
        top_photo=data.get("data", {}).get("top_photo", ""),
        live_room_status=live_room.get("liveStatus", 0),
        live_room_url="https:" + live_room.get("url", "") if live_room.get("url") else "",
        live_room_title=live_room.get("title", ""),
        following=data.get("data", {}).get("following", 0),
        follower=card.get("fans", 0),
        archive_view=card.get("archive_count", 0),
        likes=card.get("like_num", 0),
        url=f"https://space.bilibili.com/{uid}",
    )


async def fetch_user_dynamics(uid: str) -> Dict[str, Any]:
    """获取用户动态（使用WBI签名）"""
    img_key, sub_key = get_wbi_keys()
    params = {"host_mid": int(uid), "offset": 0, "need_top": 0}
    if img_key and sub_key:
        params = sign_wbi(params, img_key, sub_key)

    async def _fetch():
        client = await _get_client()
        r = await client.get(
            "https://api.bilibili.com/x/polymer/web-dynamic/v1/feed/space",
            params=params,
            headers=get_headers_with_cookie(),
        )
        r.raise_for_status()
        return r.json()

    data = await _retry_async(_fetch)
    if data.get("code") != 0:
        code = data.get("code")
        msg = data.get("message", "未知错误")
        if code == -352:
            raise BilibiliResponseError(f"风控校验失败: {msg}")
        raise BilibiliResponseError(f"获取动态失败: {msg}")
    return data.get("data", {})


async def fetch_user_videos(uid: str, ps: int = 5, pn: int = 1) -> Dict[str, Any]:
    """获取用户投稿视频列表（使用WBI签名）"""
    img_key, sub_key = get_wbi_keys()
    params = {"mid": int(uid), "ps": ps, "pn": pn, "order": "pubdate"}
    if img_key and sub_key:
        params = sign_wbi(params, img_key, sub_key)

    async def _fetch():
        client = await _get_client()
        r = await client.get(
            "https://api.bilibili.com/x/space/wbi/arc/search",
            params=params,
            headers=get_headers_with_cookie(),
        )
        r.raise_for_status()
        return r.json()

    data = await _retry_async(_fetch)
    if data.get("code") != 0:
        raise BilibiliResponseError(
            f"获取视频列表失败: {data.get('message', '未知错误')}",
            context={"code": data.get("code")}
        )
    return data.get("data", {})


# ============================================================
# 番剧API
# ============================================================

async def fetch_bangumi_info(season_id: Optional[int] = None, ep_id: Optional[int] = None) -> SeasonInfo:
    """获取番剧/影视信息"""
    if not season_id and not ep_id:
        raise ValueError("必须提供 season_id 或 ep_id")

    api_param = ""
    if season_id:
        api_param = f"season_id={season_id}"
    elif ep_id:
        api_param = f"ep_id={ep_id}"

    async def _fetch():
        client = await _get_client()
        r = await client.get(
            f"https://api.bilibili.com/pgc/view/web/season?{api_param}",
            headers=get_headers_with_cookie(),
        )
        r.raise_for_status()
        return r.json()

    data = await _retry_async(_fetch)

    if data.get("code") != 0:
        code = data.get("code")
        msg = data.get("message", "未知错误")
        if code == -404:
            raise ResourceNotFoundError(f"番剧不存在: {msg}")
        elif code == -412:
            raise RateLimitError(f"请求频率过高: {msg}", retry_after=60)
        else:
            raise BilibiliResponseError(f"获取番剧信息失败: {msg}")

    result = data.get("result", {})
    stat_data = result.get("stat", {})
    rating_data = result.get("rating", {})
    styles_data = result.get("styles", [])
    styles_str = ""
    if isinstance(styles_data, list):
        styles_str = ", ".join(str(style) for style in styles_data if style)

    areas = ", ".join(
        [
            area.get("name", "")
            for area in result.get("areas", [])
            if isinstance(area, dict)
        ]
    )

    season_info = SeasonInfo(
        season_id=result.get("season_id", 0),
        media_id=result.get("media_id", 0),
        title=result.get("title", ""),
        cover=result.get("cover", ""),
        desc=result.get("evaluate", ""),
        type_name=result.get("type_name", ""),
        areas=areas,
        styles=styles_str,
        publish=result.get("publish", {}),
        rating_score=rating_data.get("score", 0.0) if rating_data else 0.0,
        rating_count=rating_data.get("count", 0) if rating_data else 0,
        total_ep=len(result.get("episodes", [])),
        status=result.get("status", 0),
        stat_views=stat_data.get("views", 0),
        stat_danmakus=stat_data.get("danmakus", 0),
        stat_reply=stat_data.get("reply", 0),
        stat_favorites=stat_data.get("favorites", 0),
        stat_coins=stat_data.get("coins", 0),
        stat_share=stat_data.get("share", 0),
        stat_likes=stat_data.get("likes", 0),
        url=f"https://www.bilibili.com/bangumi/play/ss{result.get('season_id', 0)}",
    )

    if ep_id:
        episodes = result.get("episodes", [])
        for episode in episodes:
            if episode.get("ep_id") == ep_id or episode.get("id") == ep_id:
                season_info.target_ep_id = ep_id
                season_info.target_ep_title = episode.get("title", "")
                season_info.target_ep_long_title = episode.get("long_title", "")
                season_info.target_ep_cover = episode.get("cover", "")
                break

    return season_info


async def search_bangumi(keyword: str) -> List[Dict[str, Any]]:
    """搜索番剧"""
    params = {
        "keyword": keyword,
        "search_type": "media_bangumi",
        "page": 1,
        "page_size": 10,
    }

    async def _fetch():
        client = await _get_client()
        r = await client.get(
            "https://api.bilibili.com/x/web-interface/search/type",
            params=params,
            headers=get_headers_with_cookie(),
        )
        r.raise_for_status()
        return r.json()

    try:
        data = await _retry_async(_fetch)
        if data.get("code") == 0:
            return data.get("data", {}).get("result", [])
    except Exception as e:
        logger.warning(f"[miku_bilibili] 搜索番剧失败: {e}")
    return []


# ============================================================
# 短链接解析
# ============================================================

async def resolve_short_url(short_url: str) -> Optional[str]:
    """解析b23.tv短链接"""
    if not short_url.startswith("http"):
        short_url = "https://" + short_url

    try:
        client = await _get_client()
        r = await client.get(
            short_url,
            headers={"User-Agent": get_headers_with_cookie()["User-Agent"]},
        )
        final_url = str(r.url)
        import urllib.parse
        parsed = urllib.parse.urlparse(final_url)
        query_params = urllib.parse.parse_qs(parsed.query)
        filtered_params = {k: v for k, v in query_params.items() if k in ["p"]}
        new_query = (
            urllib.parse.urlencode(filtered_params, doseq=True)
            if filtered_params
            else ""
        )
        clean_url = urllib.parse.urlunparse(
            (
                parsed.scheme,
                parsed.netloc,
                parsed.path,
                parsed.params,
                new_query,
                "",
            )
        )
        return clean_url
    except Exception as e:
        logger.warning(f"[miku_bilibili] 短链接解析失败: {short_url} - {e}")
    return None
