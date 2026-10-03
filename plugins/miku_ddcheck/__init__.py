"""
Miku 成分姬 (ddcheck 适配版)
=============================
基于 nonebot-plugin-ddcheck / zhenxun-nonebot-plugin-ddcheck 重构，
适配 MikuBot 架构：

- 命令：查成分 <B站用户名/UID>
- 凭证：不再读取 .env，改为动态读取 config/bot.yaml 的 bilibili_credential
        （复用 miku_bilibili 的统一凭证存储，实现「一次扫码，永久查成分」）
- 联动：未登录时提示先发送 `b站登录` 扫码
- 安全：绝不在日志/群聊中明文输出 SESSDATA
- 网络：使用 curl_cffi（浏览器指纹），异步优先
- 输出：PIL 渲染成分卡片图片后以 MessageSegment.image 发送
"""

import asyncio
import sys
from pathlib import Path
from typing import Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from nonebot import on_command
from nonebot.adapters.onebot.v11 import (
    Bot,
    MessageEvent,
    Message,
    MessageSegment,
    GroupMessageEvent,
)
from nonebot.plugin import PluginMetadata
from nonebot.log import logger
from nonebot.params import CommandArg
from curl_cffi import requests as curl_requests

# ── 复用 miku_bilibili 的统一凭证（config/bot.yaml -> bilibili_credential）──
from plugins.miku_bilibili.credential import (
    get_credential,
    check_login_status,
    get_headers_with_cookie,
    get_wbi_keys,
    sign_wbi,
)

try:
    from utils.plugin_registry import register_plugin_info
except ImportError:
    register_plugin_info = lambda *args, **kwargs: None

try:
    from plugins.miku_stats import record_plugin_usage
except ImportError:
    record_plugin_usage = lambda *args, **kwargs: None


__plugin_meta__ = PluginMetadata(
    name="Miku成分姬",
    description="查询B站关注列表的VTuber成分（ddcheck 适配版）",
    usage="查成分 <B站用户名/UID>",
    type="application",
    supported_adapters={"~onebot.v11"},
)


_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.bilibili.com/",
}
_BILI_API = "https://api.bilibili.com"
_TIMEOUT = 10

# VTB 成分数据源（运行时拉取，失败则降级为空列表）
_VTB_LIST_URL = "https://api.vtbs.moe/v1/short"


# ============================================================
# 请求工具
# ============================================================

async def _fetch_json(url: str, params: dict = None, headers: dict = None) -> Optional[dict]:
    """带浏览器指纹的异步请求。401/403 视为凭证失效。"""
    try:
        async with curl_requests.AsyncSession(
            impersonate="chrome131", timeout=_TIMEOUT, headers=headers or _HEADERS
        ) as client:
            r = await client.get(url, params=params)
            if r.status_code in (401, 403):
                logger.warning("[miku_ddcheck] B站请求 401/403，凭证可能失效")
                return None
            if r.status_code != 200:
                return None
            return r.json()
    except Exception as e:
        logger.warning(f"[miku_ddcheck] 请求失败 {url}: {e}")
        return None


async def get_uid(keyword: str) -> Optional[str]:
    """解析用户：纯数字视为 UID，否则按用户名搜索。"""
    keyword = keyword.strip()
    if keyword.isdigit():
        return keyword
    try:
        params = {"search_type": "bili_user", "keyword": keyword}
        data = await _fetch_json(
            _BILI_API + "/x/web-interface/search/type",
            params=params,
            headers=get_headers_with_cookie(),
        )
        if not data or data.get("code") != 0:
            return None
        result = data.get("data", {}).get("result") or []
        if result:
            return str(result[0].get("mid"))
    except Exception as e:
        logger.warning(f"[miku_ddcheck] 用户名搜索失败: {e}")
    return None


async def get_user_name(uid: str) -> str:
    """根据 UID 获取用户名。"""
    try:
        data = await _fetch_json(
            _BILI_API + "/x/web-interface/card",
            params={"mid": uid, "photo": "false"},
            headers=get_headers_with_cookie(),
        )
        if data and data.get("code") == 0:
            return data.get("data", {}).get("card", {}).get("name", str(uid))
    except Exception:
        pass
    return str(uid)


async def get_following(uid: str) -> List[Dict]:
    """获取关注列表（WBI 签名）。返回 list of {name, uname, mid}。"""
    results: List[Dict] = []
    try:
        img_key, sub_key = get_wbi_keys()
        pn = 1
        while pn <= 8:  # 最多 8 页 * 50 = 400 个关注
            params = {
                "vmid": int(uid),
                "pn": pn,
                "ps": 50,
                "order": "desc",
                "order_type": "attention",
            }
            if img_key and sub_key:
                params = sign_wbi(params, img_key, sub_key)
            data = await _fetch_json(
                _BILI_API + "/x/relation/followings",
                params=params,
                headers=get_headers_with_cookie(),
            )
            if not data or data.get("code") != 0:
                break
            lst = data.get("data", {}).get("list") or []
            results.extend(lst)
            if len(lst) < 50:
                break
            pn += 1
    except Exception as e:
        logger.warning(f"[miku_ddcheck] 获取关注列表失败: {e}")
    return results


async def get_vtb_list() -> List[Dict]:
    """获取 VTB 列表（vtbs.moe）。失败返回空列表。"""
    try:
        data = await _fetch_json(_VTB_LIST_URL)
        if isinstance(data, list):
            return data
    except Exception as e:
        logger.warning(f"[miku_ddcheck] 获取 VTB 列表失败: {e}")
    return []


# ============================================================
# 成分计算
# ============================================================

def compute_component(following: List[Dict], vtb_list: List[Dict]) -> List[Dict]:
    """将关注列表与 VTB 列表匹配，按组织统计成分。"""
    # 建立 VTB 名称 -> 组织 映射
    name_to_org: Dict[str, str] = {}
    for vtb in vtb_list:
        if not isinstance(vtb, dict):
            continue
        org = vtb.get("group") or vtb.get("org") or ""
        names = vtb.get("names") or []
        if not isinstance(names, list):
            continue
        for n in names:
            if n:
                name_to_org.setdefault(str(n).strip(), str(org))

    matched: Dict[str, int] = {}
    for f in following:
        uname = str(f.get("uname") or f.get("name") or "").strip()
        if not uname:
            continue
        org = name_to_org.get(uname)
        if org:
            matched[org] = matched.get(org, 0) + 1

    total = sum(matched.values()) or 1
    result = []
    for org, cnt in matched.items():
        result.append({
            "name": org or "未知",
            "count": cnt,
            "percent": round(cnt / total * 100, 1),
        })
    result.sort(key=lambda x: x["count"], reverse=True)
    return result


# ============================================================
# 图片渲染（PIL）
# ============================================================

def _find_font():
    """寻找中文字体。"""
    candidates = [
        Path("C:/Windows/Fonts/msyh.ttc"),      # 微软雅黑
        Path("C:/Windows/Fonts/simhei.ttf"),    # 黑体
        Path("C:/Windows/Fonts/simsun.ttc"),    # 宋体
        Path("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"),
    ]
    for p in candidates:
        if p.exists():
            return str(p)
    return None


def render_component_image(user_name: str, uid: str, component: List[Dict]) -> Path:
    """渲染成分卡片图片，返回图片路径。"""
    from PIL import Image, ImageDraw, ImageFont

    font_path = _find_font()

    def _font(size: int):
        try:
            if font_path:
                return ImageFont.truetype(font_path, size)
        except Exception:
            pass
        return ImageFont.load_default()

    line_h = 44
    margin = 24
    width = 520
    header_h = 110
    title_font = _font(30)
    sub_font = _font(18)
    row_font = _font(22)
    bar_font = _font(18)

    rows = component[:12]
    height = header_h + len(rows) * line_h + 40

    img = Image.new("RGB", (width, height), (22, 22, 34))
    draw = ImageDraw.Draw(img)

    # 标题区
    draw.text((margin, 24), f"{user_name} 的成分", fill=(255, 255, 255), font=title_font)
    draw.text((margin, 68), f"UID: {uid}  · 共匹配 {len(rows)} 组", fill=(180, 180, 200), font=sub_font)

    # 行
    y = header_h
    for i, row in enumerate(rows):
        name = row["name"]
        cnt = row["count"]
        pct = row["percent"]
        bar_w = int((width - margin * 2) * min(pct / 100.0, 1.0))

        # 名称
        draw.text((margin, y + 8), name, fill=(240, 240, 255), font=row_font)
        # 数量
        draw.text((width - margin - 110, y + 8), f"{cnt} | {pct}%", fill=(255, 220, 120), font=row_font)
        # 进度条
        draw.rectangle([margin, y + 38, margin + bar_w, y + 44], fill=(96, 165, 250))
        y += line_h

    out_dir = PROJECT_ROOT / "data" / "miku_ddcheck"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"component_{uid}.png"
    img.save(path)
    return path


# ============================================================
# 命令：查成分
# ============================================================

ddcheck_cmd = on_command(
    "查成分",
    aliases={"查成分姬", "ddcheck", "成分"},
    priority=10,
    block=True,
)


@ddcheck_cmd.handle()
async def _ddcheck_handler(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    # 联动：未登录时提示先扫码
    is_login, uname = await check_login_status()
    if not is_login:
        await ddcheck_cmd.finish(
            "❌ 尚未获取B站凭证，无法查询成分\n"
            "请先发送 `b站登录` 扫码获取凭证（一次扫码，永久复用）"
        )

    keyword = args.extract_plain_text().strip()
    if not keyword:
        await ddcheck_cmd.finish(
            "📝 查成分\n\n"
            "用法：查成分 <B站用户名/UID>\n\n"
            "示例：\n"
            "  查成分 123456\n"
            "  查成分 某位UP主"
        )

    await ddcheck_cmd.send("⏳ 正在查询成分，请稍候...")

    # 解析 UID
    uid = await get_uid(keyword)
    if not uid:
        await ddcheck_cmd.finish(f"❌ 未找到用户「{keyword}」，请检查名称或直接使用 UID")

    record_plugin_usage("miku_ddcheck", user_id=str(event.user_id), command_name="查成分")

    # 并发获取关注列表与用户名
    user_name_task = asyncio.ensure_future(get_user_name(uid))
    following = await get_following(uid)
    user_name = await user_name_task

    if not following:
        await ddcheck_cmd.finish(
            f"⚠️ 未能获取到 {user_name}（UID:{uid}）的关注列表\n"
            f"可能是隐私设置或凭证权限不足，请重试"
        )

    # VTB 匹配
    vtb_list = await get_vtb_list()
    component = compute_component(following, vtb_list)

    if not component:
        await ddcheck_cmd.finish(
            f"🔍 {user_name}（UID:{uid}）的关注中没有匹配到 VTB 成分"
        )

    # 渲染并发送图片
    try:
        img_path = render_component_image(user_name, uid, component)
        from utils.screenshot import to_image_uri
        await ddcheck_cmd.send(MessageSegment.image(to_image_uri(str(img_path))))
        await ddcheck_cmd.send(
            f"✅ {user_name} 的成分：共关注 {len(following)} 人，匹配 {sum(c['count'] for c in component)} 位 VTuber"
        )
    except Exception as e:
        logger.error(f"[miku_ddcheck] 渲染成分图片失败: {e}", exc_info=True)
        # 文本兜底
        lines = [f"{user_name}（UID:{uid}）的成分："]
        for row in component:
            lines.append(f"  {row['name']}: {row['count']} ({row['percent']}%)")
        await ddcheck_cmd.finish("\n".join(lines))


register_plugin_info(
    "miku_ddcheck",
    name="Miku成分姬",
    icon="🔍",
    order=9,
    description="查询B站关注列表的VTuber成分（复用统一B站凭证）",
    commands=["查成分"],
    usage="查成分 <B站用户名/UID>",
)

logger.info("[miku_ddcheck] 成分姬插件已加载（复用 config/bot.yaml 统一凭证）")
