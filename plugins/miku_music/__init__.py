"""
Miku 点歌插件（本地 + 网易云 API）
================
- 指令：点歌 <歌名> [音质] / 播放 <歌名> [音质] / 来首 <歌名> [音质]
- 音质选项：标准(128k) / 高清(192k) / 极高(320k) / 无损(FLAC) / 母带(Hi-Res)
- 默认标准音质，用户可指定音质
- 优先搜索本地音乐库，本地没有则从网易云搜索并下载
- 下载后自动添加到本地音乐库，下次直接走本地
- 模糊匹配 + 多名称支持
- 以语音消息形式发送

依赖：pip install pyncm
"""

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, MessageEvent
from nonebot.adapters.onebot.v11.message import MessageSegment, Message
from nonebot.params import CommandArg
from nonebot.log import logger
from nonebot.plugin import PluginMetadata
from nonebot.exception import FinishedException

from pathlib import Path
from difflib import SequenceMatcher
from typing import Dict, List, Optional, Tuple
import json
import time
import re
import asyncio

from utils.config_manager import config_manager

# 尝试导入 pyncm
try:
    from pyncm import apis as ncm_apis
    from pyncm import GetCurrentSession, SetCurrentSession
    from pyncm.apis import login, cloudsearch, track
    PYNCM_AVAILABLE = True
except ImportError:
    PYNCM_AVAILABLE = False
    ncm_apis = None
    cloudsearch = None
    track = None

__plugin_meta__ = PluginMetadata(
    name="Miku点歌",
    description="本地+网易云点歌，支持多种音质，自动下载缓存",
    usage="点歌 <歌名> [音质]，音质：标准/高清/极高/无损/母带",
    type="application",
    supported_adapters={"~onebot.v11"},
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

MUSIC_DIR = PROJECT_ROOT / "data" / "music"
MUSIC_DIR.mkdir(parents=True, exist_ok=True)
SONGS_JSON = MUSIC_DIR / "songs.json"

# 支持的音频文件类型
AUDIO_EXTENSIONS = {".mp3", ".wav", ".ogg", ".m4a", ".flac", ".aac", ".wma"}

# 字符归一化映射（仅处理简体→繁体/日文汉字、QQ个性字体、全角符号）
# 注意：日文假名（あいうえお等）不做任何映射，保持原样以支持日文搜索
# QQ个性字体使用特殊Unicode区块，与标准日文假名不冲突
_CHAR_NORMALIZE_MAP = {
    # ===== 简体 → 繁体（汉字归一化，用于中文简繁匹配）=====
    "败": "敗", "开": "開", "关": "關", "时": "時", "间": "間",
    "长": "長", "门": "門", "问": "問", "话": "話", "会": "會",
    "对": "對", "从": "從", "个": "個", "为": "為", "么": "麼",
    "说": "說", "读": "讀", "写": "寫", "车": "車", "页": "頁",
    "点": "點", "线": "線", "练": "練", "纯": "純", "组": "組",
    "细": "細", "统": "統", "绪": "緒", "继": "繼", "绩": "績",
    "卖": "賣", "买": "買", "贝": "貝", "贞": "貞", "负": "負",
    "贵": "貴", "费": "費", "资": "資", "赋": "賦", "赌": "賭",
    "货": "貨", "质": "質", "赶": "趕", "赵": "趙", "轨": "軌",
    "军": "軍", "进": "進", "达": "達", "过": "過", "运": "運",
    "连": "連", "远": "遠", "还": "還", "这": "這", "吗": "嗎",
    "呢": "呢", "啊": "啊", "的": "的", "了": "了", "着": "著",
    "和": "和", "与": "與", "及": "及", "或": "或", "并": "并",
    "国": "國", "条": "條", "来": "來", "们": "們", "后": "後",
    "艺": "藝", "亚": "亞", "亲": "親", "诗": "詩", "语": "語",
    "词": "詞", "课": "課", "试": "試", "谁": "誰", "误": "誤",
    "谋": "謀", "谓": "謂", "责": "責", "贤": "賢", "贴": "貼",
    "赔": "賠", "赐": "賜", "贺": "賀", "赢": "贏", "懒": "懶",
    "戏": "戲", "战": "戰", "截": "截", "户": "戶", "所": "所",
    "房": "房", "厅": "廳", "历": "歷", "压": "壓", "厌": "厭",
    "层": "層", "属": "屬", "尝": "嘗", "实": "實", "审": "審",
    "宽": "寬", "寻": "尋", "导": "導", "尘": "塵", "两": "兩",
    "丽": "麗", "举": "舉", "义": "義", "乐": "樂", "书": "書",
    "争": "爭", "于": "於", "亏": "虧", "云": "雲", "产": "產",
    "亿": "億", "仅": "僅", "仓": "倉", "价": "價", "众": "眾",
    "优": "優", "传": "傳", "伦": "倫", "伪": "偽", "体": "體",
    "余": "餘", "佛": "佛", "你": "你", "怜": "憐", "恋": "戀",
    "恶": "惡", "梦": "夢", "发": "發", "圣": "聖", "声": "聲",
    "节": "節", "药": "藥", "饮": "飲", "饲": "飼", "馆": "館",
    "骑": "騎", "转": "轉", "轮": "輪", "软": "軟", "轻": "輕",
    "车": "車", "输": "輸", "辅": "輔", "边": "邊", "过": "過",
    "达": "達", "迟": "遲", "进": "進", "远": "遠", "迟": "遲",
    "开": "開", "闭": "閉", "问": "問", "间": "間", "说": "說",
    "读": "讀", "写": "寫", "译": "譯", "证": "證", "评": "評",
    "识": "識", "让": "讓", "语": "語", "请": "請", "谁": "誰",
    "谈": "談", "谊": "誼", "谋": "謀", "谓": "謂", "谓": "謂",
    "贝": "貝", "贞": "貞", "负": "負", "责": "責", "贤": "賢",
    "贴": "貼", "赌": "賭", "赔": "賠", "赐": "賜", "贺": "賀",
    # ===== QQ 个性字体（特殊 Unicode 区块 → 标准 ASCII）=====
    "𝗔": "A", "𝗕": "B", "𝗖": "C", "𝗗": "D", "𝗘": "E",
    "𝗙": "F", "𝗚": "G", "𝗛": "H", "𝗜": "I", "𝗝": "J",
    "𝗞": "K", "𝗟": "L", "𝗠": "M", "𝗡": "N", "𝗢": "O",
    "𝗣": "P", "𝗤": "Q", "𝗥": "R", "𝗦": "S", "𝗧": "T",
    "𝗨": "U", "𝗩": "V", "𝗪": "W", "𝗫": "X", "𝗬": "Y", "𝗭": "Z",
    "𝗮": "a", "𝗯": "b", "𝗰": "c", "𝗱": "d", "𝗲": "e",
    "𝗳": "f", "𝗴": "g", "𝗵": "h", "𝗶": "i", "𝗷": "j",
    "𝗸": "k", "𝗹": "l", "𝗺": "m", "𝗻": "n", "𝗼": "o",
    "𝗽": "p", "𝗾": "q", "𝗿": "r", "𝘀": "s", "𝘁": "t",
    "𝘂": "u", "𝘃": "v", "𝘄": "w", "𝘅": "x", "𝘆": "y", "𝘇": "z",
    # ===== 常用全角符号 → 半角 =====
    "！": "!", "？": "?", "～": "~", "　": " ",
    "（": "(", "）": ")", "【": "[", "】": "]",
    "，": ",", "。": ".", "、": ",", "：": ":",
    "／": "/", "＼": "\\",
}

# 反向映射：繁体/日文 → 简体（用于把 songs.json 里的歌名归一化成简体）
# 与 _CHAR_NORMALIZE_MAP 方向相反，用于搜索时的双向匹配
_REVERSE_NORMALIZE_MAP = {}
for _k, _v in _CHAR_NORMALIZE_MAP.items():
    if _k != _v and _v not in _REVERSE_NORMALIZE_MAP:
        _REVERSE_NORMALIZE_MAP[_v] = _k

# 歌单最大显示条数
MAX_PLAYLIST_DISPLAY = 20

# 网易云音质映射
# standard: 标准 128k, higher: 高清 192k, exhigh: 极高 320k, lossless: 无损, hires: 母带
NCM_QUALITY_MAP = {
    "standard": "standard",   # 标准 128kbps
    "higher": "higher",       # 高清 192kbps
    "exhigh": "exhigh",       # 极高 320kbps
    "lossless": "lossless",   # 无损 FLAC
    "hires": "hires",         # 母带 Hi-Res
}

# 用户输入的音质别名映射（支持中英文简写）
QUALITY_ALIASES = {
    "标准": "standard", "standard": "standard", "128k": "standard", "128": "standard",
    "高清": "higher", "higher": "higher", "192k": "higher", "192": "higher",
    "极高": "exhigh", "exhigh": "exhigh", "320k": "exhigh", "320": "exhigh", "hq": "exhigh",
    "无损": "lossless", "lossless": "lossless", "sq": "lossless", "flac": "lossless",
    "母带": "hires", "hires": "hires", "master": "hires", "hi-res": "hires", "hifi": "hires",
}

# 音质显示标签
QUALITY_LABELS = {
    "standard": "标准",
    "higher": "高清",
    "exhigh": "极高",
    "lossless": "无损",
    "hires": "母带",
}


# ============================================================
# 插件配置
# ============================================================
_TEMPLATE = (
    "\n"
    "miku_music:\n"
    "  # 是否启用点歌插件\n"
    "  enabled: true\n"
    "  # 模糊匹配相似度阈值 (0-100)，低于此阈值的本地结果将先验证网易云是否有更精确匹配\n"
    "  similarity_threshold: 65\n"
    "  # 本地分数低于该阈值时，触发网易云交叉验证（防止误匹配到名字相似的不同歌曲）\n"
    "  similarity_crosscheck_threshold: 75\n"
    "  # 本地搜索结果数量上限\n"
    "  max_results: 5\n"
    "  # 是否发送文字说明（歌名+歌手）\n"
    "  send_info: true\n"
    "  # 最大文件大小限制（MB）\n"
    "  max_file_size_mb: 30\n"
    "  # 是否启用网易云搜索（本地找不到时）\n"
    "  enable_ncm: true\n"
    "  # 网易云下载音质：standard(标准128k) / higher(高清192k) / exhigh(极高320k) / lossless(无损) / hires(母带)\n"
    "  # 注意：无损、母带和极高需要登录Cookie，否则降级为标准音质\n"
    "  ncm_quality: exhigh\n"
    "  # 网易云搜索结果数量\n"
    "  ncm_search_limit: 5\n"
    "  # 网易云Cookie（登录后可下载高品质音频）\n"
    "  # 获取方式：浏览器登录music.163.com → F12 → Application → Cookies\n"
    "  # 复制 MUSIC_U 的值粘贴到这里\n"
    "  ncm_cookie: \"\"\n"
)

_cfg = config_manager.register_plugin(
    "miku_music",
    defaults={
        "enabled": True,
        "similarity_threshold": 65,
        "similarity_crosscheck_threshold": 75,
        "max_results": 5,
        "send_info": True,
        "max_file_size_mb": 20,
        "enable_ncm": True,
        "ncm_quality": "standard",
        "ncm_search_limit": 5,
        "ncm_cookie": "",
    },
    template_str=_TEMPLATE,
    description="点歌插件配置（本地+网易云）",
)


def _is_enabled() -> bool:
    raw = config_manager.get("miku_music", "enabled", True)
    return str(raw).strip().lower() not in ("false", "0", "no", "")


def _conf(key: str, default=None):
    return config_manager.get("miku_music", key, default)


# ============================================================
# 网易云 Cookie 管理（SVIP 高品质下载）
# ============================================================
# Cookie 状态追踪
_COOKIE_STATUS = {
    "valid": None,       # None=未检测, True=有效, False=已过期
    "last_check": 0,     # 上次检测时间戳
    "last_warning": 0,   # 上次提醒时间戳（用于限频）
    "need_notify": False,  # 是否需要在下次点歌时提醒用户
}
_COOKIE_CHECK_INTERVAL = 300   # Cookie 有效性检查间隔（秒）
_COOKIE_WARNING_INTERVAL = 1800  # Cookie 过期提醒间隔（秒），30分钟最多提醒一次
_PYNCM_DICT_SESSION = False  # pyncm GetCurrentSession 返回 dict 形态时置 True，用于跳过备用下载 pyncm 分支，避免 WeAPI 报错刷屏


def _get_ncm_cookie() -> str:
    """获取配置的网易云 Cookie 字符串"""
    raw = _conf("ncm_cookie", "")
    if not raw:
        return ""
    raw = str(raw).strip()
    if not raw:
        return ""
    # 用户只填了 MUSIC_U 的值，自动补全为 Cookie 格式
    if "=" not in raw:
        return f"MUSIC_U={raw}"
    return raw


async def _check_cookie_validity(cookie: str = None) -> Tuple[Optional[bool], Optional[dict]]:
    """
    检测网易云 Cookie 是否有效
    返回 (valid, info):
        valid: True=有效, False=已过期/无效, None=未配置Cookie或检测异常
        info:  dict with keys {nickname, userId, vipType, vipLabel} 或 None
    使用 POST 方法访问用户信息接口
    """
    if cookie is None:
        cookie = _get_ncm_cookie()
    if not cookie:
        return (None, None)

    import time
    now = time.time()
    # 缓存结果，避免频繁检测（只用 cookie 是否完全相同做缓存 key 简化）
    if (_COOKIE_STATUS["valid"] is not None
            and (now - _COOKIE_STATUS["last_check"]) < _COOKIE_CHECK_INTERVAL):
        info = {}
        for k in ("nickname", "userId", "vipType"):
            if k in _COOKIE_STATUS:
                info[k] = _COOKIE_STATUS[k]
        return (_COOKIE_STATUS["valid"], info or None)

    try:
        import httpx
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://music.163.com/",
            "Cookie": cookie,
            "Content-Type": "application/x-www-form-urlencoded",
        }
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            # 先请求一次首页，让服务器种下 session cookie，再查登录态（更贴近真实流程）
            try:
                await client.get("https://music.163.com/", headers={
                    "User-Agent": headers.get("User-Agent", ""),
                    "Referer": "https://music.163.com/",
                    "Cookie": cookie,
                })
            except Exception:
                pass

            # ===== 主检测接口0：/api/login/status（网易云新版登录状态查询）=====
            try:
                resp_s = await client.get(
                    "https://music.163.com/api/login/status",
                    headers=headers,
                )
                if resp_s.status_code == 200:
                    ds = resp_s.json()
                    if isinstance(ds, dict):
                        code_s = ds.get("code")
                        profile_s = (ds.get("profile") or {}) if isinstance(ds, dict) else {}
                        account_s = (ds.get("account") or {}) if isinstance(ds, dict) else {}
                        if code_s == 200 and ((profile_s and profile_s.get("userId")) or (account_s and account_s.get("id"))):
                            nickname = profile_s.get("nickname", "未知") if isinstance(profile_s, dict) else "未知"
                            user_id = profile_s.get("userId") if isinstance(profile_s, dict) else None
                            vip_type = (profile_s.get("vipType", 0) if isinstance(profile_s, dict) else 0) or 0
                            vip_label = {0: "非会员", 1: "VIP", 10: "黑胶VIP", 11: "SVIP"}.get(vip_type, f"Type{vip_type}")
                            info = {"nickname": nickname, "userId": user_id, "vipType": vip_type, "vipLabel": vip_label}
                            _COOKIE_STATUS["valid"] = True
                            _COOKIE_STATUS["last_check"] = now
                            _COOKIE_STATUS["nickname"] = nickname
                            if user_id is not None:
                                _COOKIE_STATUS["userId"] = user_id
                            _COOKIE_STATUS["vipType"] = vip_type
                            logger.info(f"[点歌] 网易云Cookie有效 (api/login/status)｜用户: {nickname}, 会员: {vip_label}")
                            return (True, info)
                        # code=200 但没 profile 时保留异常：打印首次响应，帮用户定位
                        logger.debug(f"[点歌] /api/login/status 响应: code={code_s}, keys={list(ds.keys())[:12]}")
            except Exception as _e:
                logger.debug(f"[点歌] /api/login/status 检测异常: {_e}")

            # ===== 主检测接口1：/api/nuser/account/get（纯 GET，兼容纯 Cookie 未加密请求）=====
            try:
                resp0 = await client.get(
                    "https://music.163.com/api/nuser/account/get",
                    headers=headers,
                )
                if resp0.status_code == 200:
                    d = resp0.json()
                    if isinstance(d, dict):
                        code0 = d.get("code")
                        profile = (d.get("profile") or {})
                        account = (d.get("account") or {})
                        if code0 == 200 and ((profile and profile.get("userId")) or (account and account.get("id"))):
                            nickname = profile.get("nickname", "未知") if isinstance(profile, dict) else "未知"
                            user_id = profile.get("userId") if isinstance(profile, dict) else None
                            vip_type = (profile.get("vipType", 0) if isinstance(profile, dict) else 0) or 0
                            vip_label = {0: "非会员", 1: "VIP", 10: "黑胶VIP", 11: "SVIP"}.get(vip_type, f"Type{vip_type}")
                            info = {"nickname": nickname, "userId": user_id, "vipType": vip_type, "vipLabel": vip_label}
                            _COOKIE_STATUS["valid"] = True
                            _COOKIE_STATUS["last_check"] = now
                            _COOKIE_STATUS["nickname"] = nickname
                            if user_id is not None:
                                _COOKIE_STATUS["userId"] = user_id
                            _COOKIE_STATUS["vipType"] = vip_type
                            logger.info(f"[点歌] 网易云Cookie有效 (api/nuser/account)｜用户: {nickname}, 会员: {vip_label}")
                            return (True, info)
                        if code0 != 200:
                            logger.debug(f"[点歌] /api/nuser/account/get 未通过: code={code0}, body={str(d)[:200]}")
            except Exception as _e0:
                logger.debug(f"[点歌] /api/nuser/account/get 检测异常: {_e0}")

            # ===== 主检测接口2：POST /api/s/user/account（纯表单，不走 weapi 加密路径）=====
            resp = await client.post(
                "https://music.163.com/api/s/user/account",
                headers=headers,
                data={"csrf_token": ""},
            )
            if resp.status_code == 200:
                try:
                    data = resp.json()
                    code = data.get("code") if isinstance(data, dict) else None
                    if code == 200:
                        profile = (data.get("profile") or {}) if isinstance(data, dict) else {}
                        account = (data.get("account") or {}) if isinstance(data, dict) else {}
                        if (profile and profile.get("userId")) or (account and account.get("id")):
                            nickname = profile.get("nickname", "未知") if isinstance(profile, dict) else "未知"
                            user_id = profile.get("userId") if isinstance(profile, dict) else None
                            vip_type = (profile.get("vipType", 0) if isinstance(profile, dict) else 0) or 0
                            vip_label = {0: "非会员", 1: "VIP", 10: "黑胶VIP", 11: "SVIP"}.get(vip_type, f"Type{vip_type}")
                            info = {
                                "nickname": nickname,
                                "userId": user_id,
                                "vipType": vip_type,
                                "vipLabel": vip_label,
                            }
                            _COOKIE_STATUS["valid"] = True
                            _COOKIE_STATUS["last_check"] = now
                            _COOKIE_STATUS["nickname"] = nickname
                            if user_id is not None:
                                _COOKIE_STATUS["userId"] = user_id
                            _COOKIE_STATUS["vipType"] = vip_type
                            logger.info(f"[点歌] 网易云Cookie有效 (api/s/user/account)｜用户: {nickname}, 会员: {vip_label}")
                            return (True, info)
                        else:
                            # code=200 但没有 userId —— 这种通常是 Cookie 没带 MUSIC_U 但仍可匿名请求
                            logger.debug(f"[点歌] /api/s/user/account code=200 但无userId: {str(data)[:200]}")
                    elif code == 301 or code == 302:
                        _COOKIE_STATUS["valid"] = False
                        _COOKIE_STATUS["last_check"] = now
                        logger.warning(f"[点歌] 网易云Cookie已过期或无效 (code={code})")
                        return (False, None)
                    else:
                        logger.debug(f"[点歌] /api/s/user/account 非200: code={code}, body={str(data)[:200]}")
                except Exception as _e2:
                    logger.debug(f"[点歌] /api/s/user/account JSON解析异常: {_e2}")

            # ===== 接口3（最可靠）：用 pyncm 自己的 login.GetLoginStatus —— 因为 pyncm session 已经
            # 是我们替换好的标准 requests.Session + 完整 Cookie jar + jar 已写入 MUSIC_U，
            # 它内部走 WeAPI 加密路径，这是网易云服务器真正接受的方式。
            if PYNCM_AVAILABLE and login is not None:
                import asyncio as _aio
                loop = _aio.get_event_loop()
                try:
                    status_data = await loop.run_in_executor(None, login.GetLoginStatus)
                except Exception as _pyncm_err:
                    msg = str(_pyncm_err)
                    logger.debug(f"[点歌] pyncm登录态接口失败: {msg[:200]}")
                    status_data = None
                if (isinstance(status_data, dict)
                        and status_data.get("data")
                        and isinstance(status_data["data"], dict)):
                    dd = status_data["data"]
                    account = dd.get("account") or {}
                    profile = dd.get("profile") or {}
                    if (isinstance(account, dict) and account.get("id")) or (
                        isinstance(profile, dict) and profile.get("userId")
                    ):
                        nickname = (profile.get("nickname", "未知")
                                    if isinstance(profile, dict) else "未知")
                        user_id = profile.get("userId") if isinstance(profile, dict) else None
                        vip_type = (profile.get("vipType", 0)
                                    if isinstance(profile, dict) else 0) or 0
                        vip_label = {
                            0: "非会员", 1: "VIP", 10: "黑胶VIP", 11: "SVIP"
                        }.get(vip_type, f"Type{vip_type}")
                        info = {
                            "nickname": nickname,
                            "userId": user_id,
                            "vipType": vip_type,
                            "vipLabel": vip_label,
                        }
                        _COOKIE_STATUS["valid"] = True
                        _COOKIE_STATUS["last_check"] = now
                        _COOKIE_STATUS["nickname"] = nickname
                        if user_id is not None:
                            _COOKIE_STATUS["userId"] = user_id
                        _COOKIE_STATUS["vipType"] = vip_type
                        logger.info(
                            f"[点歌] 网易云Cookie有效 (pyncm)｜用户: {nickname}, 会员: {vip_label}"
                        )
                        return (True, info)

            # 尝试备用检测：搜索一首需要VIP的歌曲
            resp2 = await client.get(
                "https://music.163.com/api/search/get/web",
                params={"s": "阴天", "type": 1, "limit": 1},
                headers=headers,
            )
            if resp2.status_code == 200:
                try:
                    data2 = resp2.json()
                    if data2.get("result") and data2["result"].get("songs"):
                        # 能搜索说明Cookie有效
                        _COOKIE_STATUS["valid"] = True
                        _COOKIE_STATUS["last_check"] = now
                        logger.info("[点歌] 网易云Cookie有效 (搜索验证通过，但未识别到vipType)")
                        return (True, {"vipLabel": "未知", "nickname": "未知"})
                except Exception:
                    pass

            logger.debug(f"[点歌] Cookie检测请求返回异常: HTTP {resp.status_code}")
            return (None, None)
    except Exception as e:
        logger.warning(f"[点歌] Cookie有效性检测异常: {e}")
        return (None, None)


async def _notify_cookie_expired(bot: Bot = None, event: MessageEvent = None):
    """
    通知用户 Cookie 已过期
    - 30分钟内最多提醒一次
    - 如果有 bot 和 event，直接在当前会话提醒
    - 否则记录日志，等待下一次点歌时提醒
    """
    import time
    now = time.time()
    if (now - _COOKIE_STATUS["last_warning"]) < _COOKIE_WARNING_INTERVAL:
        return  # 限频，不重复提醒

    _COOKIE_STATUS["last_warning"] = now
    warning_msg = (
        "⚠️ 网易云Cookie可能已过期！\n"
        "高品质音乐（无损/母带/极高）可能无法下载\n"
        "请重新登录 music.163.com → F12 → Application → Cookies → 更新 MUSIC_U"
    )

    # 如果有 bot 和 event，直接在当前会话发送
    if bot and event:
        try:
            await bot.send(event, warning_msg)
        except Exception:
            pass

    # 始终记录日志
    logger.warning(f"[点歌] Cookie过期提醒: {warning_msg}")


def _init_pyncm_session():
    """启动时将 Cookie 注入 pyncm 会话，启用登录态下载（兼容多版本 pyncm）

    思路优先级：
    1) 优先尝试用 pyncm 自己的 SetCurrentSession（如果有）把一个「真正能
       发网络请求」的 session 注入 pyncm 全局。注意：不强行依赖独立的
       `requests` 包（用户环境里可能没装），而是优先使用 pyncm 内部自带的
       HTTP 客户端（如 pyncm 用的 httpx.Client 或 requests.Session）来
       创建。如果 pyncm 内部用的是 dict 形态 session，就只用字段注入。
    2) 否则退化为修改默认 session 的 headers / cookies 字段，兼容 dict /
       requests.Session 两种形态。
    3) 登录态校验**优先**用我们自己 httpx + _check_cookie_validity（已验证
       稳定可用，不依赖 pyncm），只有它拿不到 vipType 时才 fallback 到
       pyncm 的 login.GetLoginStatus。
    """
    if not PYNCM_AVAILABLE:
        return
    cookie = _get_ncm_cookie()
    if not cookie:
        logger.info("[点歌] 未配置网易云Cookie，使用游客模式（标准音质）")
        return

    # 预设：字段注入之后，如果仍然是 dict 形态 session，
    # 就把 _PYNCM_DICT_SESSION 设置为 True，备用下载时直接跳过 pyncm，
    # 避免 WeAPI request failed 刷屏
    global _PYNCM_DICT_SESSION

    try:
        session = GetCurrentSession()
        session_type = type(session).__name__
        ref_session_headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/126.0.0.0 Safari/537.36"
            ),
            "Referer": "https://music.163.com/",
            "Accept": "*/*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            # 注意：Cookie 在 _write_session_headers_cookies 里单独写
        }

        # ===== 优先级 1：尝试找 pyncm 真正用于请求的 session 类型 =====
        # 优先用 SetCurrentSession，但不依赖独立的 requests 模块。
        # 策略：遍历几种能构造「真 session」的方法，都失败就退化为字段注入。
        injected_true_session = False
        try:
            _setter = globals().get("SetCurrentSession") or (
                hasattr(ncm_apis, "SetCurrentSession") and ncm_apis.SetCurrentSession
            )
            if callable(_setter):
                # A. 先尝试用 pyncm 自己的 session 类：如果 pyncm 内部有
                #    _session_cls 或 requests（它自己依赖的 requests 可能
                #    在它自己的子模块命名空间里），就用它构造。
                true_session = None
                # 策略 A1：直接创建「与当前 session 类型相同」的新对象
                try:
                    if not isinstance(session, dict):
                        true_session = type(session)()
                except Exception:
                    true_session = None
                # 策略 A2：如果当前就是 dict，那就直接退化为字段注入，
                # 因为我们不知道 pyncm 真正的网络层长啥样。
                if true_session is not None:
                    # 为 true_session 写入 headers / cookies
                    _write_session_headers_cookies(
                        true_session, cookie, _FakeSessionHeaders(ref_session_headers)
                    )
                    try:
                        _setter(true_session)
                        logger.info(
                            f"[点歌] pyncm会话已替换为{type(true_session).__name__}"
                            f"（原类型: {session_type}）"
                        )
                        session = true_session
                        session_type = type(session).__name__
                        injected_true_session = True
                    except Exception:
                        injected_true_session = False
        except Exception as inject_err:
            logger.debug(f"[点歌] pyncm SetCurrentSession注入跳过: {inject_err}")
            injected_true_session = False

        # ===== 优先级 2：字段注入（兼容 dict / 属性 session）=====
        if not injected_true_session:
            latest_session = GetCurrentSession()
            _write_session_headers_cookies(
                latest_session, cookie, _FakeSessionHeaders(ref_session_headers)
            )
            logger.info(f"[点歌] pyncm字段注入完成（session类型: {type(latest_session).__name__}）")

        # 更新 _PYNCM_DICT_SESSION 标志（备用下载中用）
        final_check = GetCurrentSession()
        _PYNCM_DICT_SESSION = isinstance(final_check, dict)
        if _PYNCM_DICT_SESSION:
            logger.info(
                "[点歌] pyncm底层session仍为dict形态，备用下载时将跳过pyncm"
                "（避免WeAPI风控刷屏）"
            )
    except Exception as e:
        logger.warning(f"[点歌] 加载Cookie到pyncm失败: {e}")
        return

    # ===== 登录态校验（优先用 httpx 直连，不依赖 pyncm）=====
    try:
        import asyncio as _asyncio
        loop = _asyncio.new_event_loop()
        try:
            valid, info = loop.run_until_complete(_check_cookie_validity(cookie))
        finally:
            loop.close()
        if valid and info:
            nickname = info.get("nickname", "未知")
            vip_type = info.get("vipType", 0)
            vip_label = {0: "非会员", 1: "VIP", 10: "黑胶VIP", 11: "SVIP"}.get(vip_type, f"Type{vip_type}")
            logger.info(
                f"[点歌] 网易云登录态验证通过 (httpx)｜用户: {nickname}, 会员: {vip_label}"
            )
            _COOKIE_STATUS["cookie_valid"] = True
            _COOKIE_STATUS["nickname"] = nickname
            _COOKIE_STATUS["vipType"] = vip_type
            if vip_type in (10, 11):
                logger.info("[点歌] Cookie已加载，高品质音乐可正常下载")
            else:
                logger.warning(
                    f"[点歌] ⚠️  已配置网易云Cookie，但服务器识别当前账号是「{vip_label}」"
                    f"（用户: {nickname}）"
                    "｜若你的网易云账号确实是黑胶SVIP/黑胶VIP，请立刻检查："
                    "1) 复制的MUSIC_U是否真的是你SVIP账号对应的那个？"
                    "（而不是浏览器残留的其他普通账号Cookie）"
                    "2) 建议从网易云网页端退出当前账号，重新登录SVIP账号，"
                    "然后再次F12复制完整Cookie字符串。"
                    "没有SVIP Cookie的情况下，VIP专属歌曲的API都会返回 payed=0 和 code=-110。"
                )
            return
        elif valid:
            # valid=True 但 info 为空 → 走到了搜索 backup，无法判定 vipType
            # 因为 pyncm session 替换成了真正的 Session，这里再用 pyncm 自检查一遍会员信息
            logger.info("[点歌] httpx仅通过搜索验证（无法识别vipType），再尝试pyncm自检...")
        else:
            logger.warning(f"[点歌] httpx校验Cookie失败，尝试pyncm自检｜httpx结果: valid={valid}, info={info}")
    except Exception as httpx_err:
        logger.warning(f"[点歌] httpx校验Cookie异常（不影响主流程）: {httpx_err}")

    # ===== httpx 校验失败时，才 fallback 到 pyncm 的登录态接口 =====
    try:
        if login and hasattr(login, 'GetLoginStatus'):
            status_data = login.GetLoginStatus()
            if isinstance(status_data, dict) and status_data.get("data"):
                data = status_data["data"]
                account = data.get("account") or {}
                profile = data.get("profile") or {}
                if account.get("id") or profile.get("userId"):
                    nickname = profile.get("nickname", "未知")
                    vip_type = profile.get("vipType", 0)
                    vip_label = {0: "非会员", 1: "VIP", 10: "黑胶VIP", 11: "SVIP"}.get(vip_type, f"Type{vip_type}")
                    logger.info(
                        f"[点歌] pyncm登录态验证通过 (用户: {nickname}, 会员: {vip_label})"
                    )
                    logger.info("[点歌] 网易云Cookie已加载，可下载高品质音频")
                    _COOKIE_STATUS["cookie_valid"] = True
                    _COOKIE_STATUS["nickname"] = nickname
                    _COOKIE_STATUS["vipType"] = vip_type
                else:
                    logger.warning(f"[点歌] pyncm识别到Cookie但登录态未生效: {status_data}")
            else:
                logger.warning(f"[点歌] pyncm登录态接口返回异常: {status_data}")
        else:
            logger.info("[点歌] Cookie已写入pyncm（登录API不可用，跳过自检）")
    except Exception as check_err:
        err_msg = str(check_err)
        # WeAPI 返回非 JSON 通常是 403/460 风控或返回了 HTML，提示但不吓人
        if "Expecting value" in err_msg or "WeAPI" in err_msg or "EAPI" in err_msg:
            logger.info(
                "[点歌] pyncm自检API被风控拦截（返回非JSON），"
                "但Cookie已写入真实httpx下载路径，不影响高品质下载"
            )
        else:
            logger.warning(f"[点歌] pyncm登录态自检失败（不影响主流程）: {check_err}")

    # === 启动时最终诊断：如果配置了 ncm_cookie，但 vipType 仍不是 SVIP/黑胶VIP —— 打印明确的Cookie归属提醒
    # （如果上面已经在 valid&info 分支报过一次警告，这里就不用重复；这里覆盖搜索backup的情况）
    _stored_vip = 0
    _stored_nick = None
    try:
        _stored_vip = int(_COOKIE_STATUS.get("vipType", 0) or 0)
    except Exception:
        _stored_vip = 0
    _stored_nick = _COOKIE_STATUS.get("nickname") if isinstance(_COOKIE_STATUS, dict) else None
    if _get_ncm_cookie() and _stored_vip not in (10, 11):
        vip_readable = {0: "非会员", 1: "VIP", 10: "黑胶VIP", 11: "SVIP"}.get(
            _stored_vip, f"Type{_stored_vip}"
        )
        if not _stored_nick or _stored_nick == "未知":
            hint = (
                "目前各登录API均不可用（HTTP 404 / 返回非JSON），"
                "无法识别账号昵称和会员等级。"
                "如果你的账号确实是黑胶SVIP，请退出网易云网页端账号后重新登录，"
                "再复制『完整Cookie字符串』（至少包含 MUSIC_U + NTES_SES + os 等）"
                "覆盖写入 bot.yaml 中的 ncm_cookie。"
            )
        else:
            hint = (
                f"当前Cookie归属账号『{_stored_nick}』（{vip_readable}），"
                "不是你截图中的那个SVIP账号。"
                "请退出浏览器网易云账号，重新登录SVIP账号后再复制完整Cookie。"
            )
        logger.warning(
            "[点歌] ⚠️  检测到网易云Cookie不是黑胶SVIP/黑胶VIP（或归属不明）。"
            f"{hint}"
            "没有SVIP账号的MUSIC_U，VIP歌曲API一定会返回『payed=0 / code=-110』，"
            "这与插件逻辑无关。"
        )
    return


class _FakeSessionHeaders:
    """临时构造一个带 headers 属性的 fake session，给
    _write_session_headers_cookies 的 reference_session 参数用。"""
    def __init__(self, headers_dict: dict):
        self.headers = dict(headers_dict)


def _write_session_headers_cookies(session, cookie: str, reference_session=None):
    """把 Cookie 同时写入 session 的 headers 和 cookies 字段。

    兼容三种形态：
      - session 是标准 requests.Session（属性访问）
      - session 是 dict，key 是 "headers"/"cookies"
      - session 是其他自定义对象，通过 getattr 取字段
    """
    # 1) 写 headers["Cookie"]
    headers_obj = None
    if isinstance(session, dict):
        if "headers" not in session or not isinstance(session["headers"], dict):
            session["headers"] = {}
        headers_obj = session["headers"]
    else:
        headers_obj = getattr(session, "headers", None)
    if isinstance(headers_obj, dict):
        headers_obj["Cookie"] = cookie
        if reference_session is not None:
            # 合并 reference_session 中的一些标准请求头（UA, Referer）
            for _k, _v in dict(reference_session.headers).items():
                if _k.lower() == "cookie":
                    continue
                headers_obj[_k] = _v
    elif hasattr(headers_obj, "update"):
        patch = {"Cookie": cookie}
        if reference_session is not None:
            for _k, _v in dict(reference_session.headers).items():
                if _k.lower() == "cookie":
                    continue
                patch[_k] = _v
        try:
            headers_obj.update(patch)
        except Exception:
            pass

    # 2) 写 cookies
    cookies_obj = None
    if isinstance(session, dict):
        cookies_obj = session.get("cookies")
        if cookies_obj is None:
            cookies_obj = {}
            session["cookies"] = cookies_obj
    else:
        cookies_obj = getattr(session, "cookies", None)

    if cookies_obj is not None:
        try:
            if hasattr(cookies_obj, "set") and callable(cookies_obj.set):
                # 标准 RequestsCookieJar：按域名写入
                for item in cookie.split(";"):
                    item = item.strip()
                    if "=" in item:
                        k, v = item.split("=", 1)
                        k = k.strip()
                        v = v.strip()
                        if not k:
                            continue
                        try:
                            cookies_obj.set(k, v, domain=".music.163.com")
                            cookies_obj.set(k, v, domain="music.163.com")
                            cookies_obj.set(k, v, domain=".163.com")
                        except Exception:
                            cookies_obj.set(k, v)
            elif isinstance(cookies_obj, dict):
                for item in cookie.split(";"):
                    item = item.strip()
                    if "=" in item:
                        k, v = item.split("=", 1)
                        cookies_obj[k.strip()] = v.strip()
        except Exception as cookie_err:
            logger.debug(f"[点歌] 写入cookie jar失败（可忽略）: {cookie_err}")



def _get_effective_quality(user_quality: str = None) -> str:
    """获取有效音质：优先用户指定，无Cookie时自动降级为 standard"""
    if user_quality:
        quality = str(user_quality).lower()
    else:
        quality = str(_conf("ncm_quality", "standard")).lower()
    
    cookie = _get_ncm_cookie()
    # 无 Cookie 时，高品质音质自动降级
    if not cookie and quality in ("exhigh", "lossless", "higher", "hires"):
        logger.info(f"[点歌] 无Cookie，音质 {quality} → standard")
        return "standard"
    return quality


def _normalize_text(text: str, direction: str = "forward") -> str:
    """归一化文本，处理简体↔繁体/日文、QQ个性字体、全角字符等
    direction: "forward" = 简体→繁体/日文（用户输入匹配 songs.json）
               "reverse" = 繁体/日文→简体（songs.json 歌名匹配用户输入）
    """
    if not text:
        return text
    mapping = _CHAR_NORMALIZE_MAP if direction == "forward" else _REVERSE_NORMALIZE_MAP
    result = []
    for ch in text:
        mapped = mapping.get(ch)
        result.append(mapped if mapped else ch)
    return "".join(result)


def _parse_quality_from_query(query: str) -> tuple:
    """
    从用户输入中解析音质选项
    返回 (清理后的query, 音质) 元组
    """
    words = query.strip().split()
    if len(words) <= 1:
        return query, None
    
    # 检查最后一个词是否是音质关键词
    last_word = words[-1].lower()
    if last_word in QUALITY_ALIASES:
        quality = QUALITY_ALIASES[last_word]
        clean_query = " ".join(words[:-1])
        return clean_query, quality
    
    # 检查是否有 "音质:xxx" 或 "音质=xxx" 格式
    for i, word in enumerate(words):
        word_lower = word.lower()
        for alias, q in QUALITY_ALIASES.items():
            if word_lower == alias:
                remaining = words[:i] + words[i+1:]
                return " ".join(remaining), q
            # 支持 "音质:极高" 这种格式
            if word_lower in ("音质:", "音质=", "音质：") and i + 1 < len(words):
                next_word = words[i + 1].lower()
                if next_word in QUALITY_ALIASES:
                    remaining = words[:i] + words[i+2:]
                    return " ".join(remaining), QUALITY_ALIASES[next_word]
    
    return query, None


# ============================================================
# 歌曲缓存（避免每次读取文件）
# ============================================================
_SONGS_CACHE: Optional[List[Dict]] = None
_SONGS_CACHE_TIME: float = 0
_CACHE_TTL_SECONDS = 60


def _infer_quality_from_song(song: Dict) -> str:
    """根据歌曲来源、文件扩展名和配置，推断缺失 quality 时的默认音质"""
    file_name = song.get("file", "")
    fp = MUSIC_DIR / file_name if file_name else None

    # 1) FLAC 文件统一视为 lossless
    if fp and fp.exists() and fp.suffix.lower() == ".flac":
        return "lossless"

    # 2) netease 来源：按配置中记录的默认音质（有Cookie用户一般用 exhigh）
    if song.get("source") == "netease":
        cfg_q = _conf("ncm_quality", "standard")
        q = str(cfg_q).lower().strip()
        if q in NCM_QUALITY_MAP:
            return q
        # 兼容旧版，默认配值 standard + 有 cookie 时用 exhigh
        if _get_ncm_cookie():
            return "exhigh"
        return "standard"

    # 3) 本地来源或未知来源：使用配置默认
    cfg_q = _conf("ncm_quality", "standard")
    if str(cfg_q).lower() in NCM_QUALITY_MAP:
        return str(cfg_q).lower()
    return "standard"


def _migrate_quality_fields(songs: List[Dict]) -> bool:
    """补全 songs.json 中缺少 quality 字段的条目，返回是否发生过修改"""
    changed = False
    for s in songs:
        if not isinstance(s, dict):
            continue
        if "quality" not in s or not s.get("quality"):
            q = _infer_quality_from_song(s)
            s["quality"] = q
            changed = True
            primary = ""
            names = s.get("names", [])
            if isinstance(names, list) and names:
                primary = str(names[0])
            logger.info(f"[点歌] 补全音质字段: {primary} → {QUALITY_LABELS.get(q, q)}")
    return changed


def _load_songs() -> List[Dict]:
    """从 songs.json 加载歌曲列表（带缓存，自动补全 quality 字段）"""
    global _SONGS_CACHE, _SONGS_CACHE_TIME
    current_time = time.time()
    if _SONGS_CACHE is not None and (current_time - _SONGS_CACHE_TIME) < _CACHE_TTL_SECONDS:
        return _SONGS_CACHE

    if not SONGS_JSON.exists():
        _SONGS_CACHE = []
        _SONGS_CACHE_TIME = current_time
        return []

    try:
        data = json.loads(SONGS_JSON.read_text(encoding="utf-8"))
        songs = data.get("songs", []) if isinstance(data, dict) else data
        valid_songs = [s for s in songs if isinstance(s, dict) and s.get("names") and s.get("file")]
        # 自动补全缺少 quality 的旧条目
        if _migrate_quality_fields(valid_songs):
            _save_songs(valid_songs)
        _SONGS_CACHE = valid_songs
        _SONGS_CACHE_TIME = current_time
        return valid_songs
    except Exception as e:
        logger.error(f"[点歌] 加载歌曲数据失败: {e}")
        _SONGS_CACHE = []
        _SONGS_CACHE_TIME = current_time
        return []


def _invalidate_cache():
    global _SONGS_CACHE, _SONGS_CACHE_TIME
    _SONGS_CACHE = None
    _SONGS_CACHE_TIME = 0


def _save_songs(songs: List[Dict]):
    """保存歌曲列表到 songs.json"""
    data = {"songs": songs}
    SONGS_JSON.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    _invalidate_cache()


# 音质等级，数值越大品质越高
_QUALITY_RANK = {
    "standard": 1,
    "higher": 2,
    "exhigh": 3,
    "lossless": 4,
    "hires": 5,
}


def _quality_rank(q: str) -> int:
    """返回音质等级数值，未知音质视为 standard(1)"""
    if not q:
        return 1
    return _QUALITY_RANK.get(str(q).lower(), 1)


def _is_upgrade_needed(local_q: str, user_q: str) -> bool:
    """判断本地音质是否需要按用户请求升级（不同音质都重新下载，用户指定为准）"""
    if not user_q:
        return False  # 用户没指定，不升级
    local_rank = _quality_rank(local_q)
    user_rank = _quality_rank(user_q)
    # 用户请求的音质与本地不同 → 重新下载对应音质（既升级也允许降级重下）
    return local_rank != user_rank


def _add_song_to_library(song_info: Dict):
    """将下载的歌曲添加到本地音乐库（同一 ncm_id + 相同音质才覆盖，不同音质各自独立保存）"""
    songs = _load_songs()
    ncm_id = song_info.get("ncm_id")
    new_quality = song_info.get("quality", "")
    new_file = song_info.get("file", "")
    updated = False

    if ncm_id:
        for s in songs:
            if s.get("ncm_id") == ncm_id and (s.get("quality", "") == new_quality or not s.get("quality")):
                # 相同 ncm_id 且相同音质（或旧条目无音质）：更新文件与信息
                if new_file:
                    s["file"] = new_file
                if song_info.get("artist"):
                    s["artist"] = song_info["artist"]
                if song_info.get("album"):
                    s["album"] = song_info["album"]
                s["quality"] = new_quality
                # 保留 is_cover 标记
                if song_info.get("is_cover"):
                    s["is_cover"] = True
                # 保留 original_artist 标记
                if song_info.get("original_artist"):
                    s["original_artist"] = song_info["original_artist"]
                # 合并 names，避免重复
                existing_names = [str(n) for n in (s.get("names") or [])]
                for n in song_info.get("names", []):
                    if str(n) not in existing_names:
                        existing_names.append(str(n))
                s["names"] = existing_names
                updated = True
                break

    if not updated:
        songs.append(song_info)

    _save_songs(songs)
    primary = ""
    names = song_info.get("names", []) or []
    if isinstance(names, list) and names:
        primary = str(names[0])
    q_label = QUALITY_LABELS.get(new_quality, new_quality) if new_quality else "未知"
    action = "已更新本地音乐库" if updated else "已添加到本地音乐库"
    logger.info(f"[点歌] {action}: {primary} ({q_label})")


# ============================================================
# 文件有效性检查
# ============================================================
def _is_valid_audio_file(file_path: Path) -> tuple:
    if not file_path.exists():
        return False, "文件不存在"

    ext = file_path.suffix.lower()
    if ext not in AUDIO_EXTENSIONS:
        return False, f"不支持该文件类型：{ext}"

    file_size = file_path.stat().st_size
    max_size_mb = int(_conf("max_file_size_mb", 20) or 20)
    max_size = max_size_mb * 1024 * 1024

    if file_size > max_size:
        size_mb = file_size / (1024 * 1024)
        return False, f"文件过大：{size_mb:.1f}MB（限制：{max_size_mb}MB）"

    return True, ""


# ============================================================
# 语音消息音频处理（QQ语音有大小限制，需转码压缩）
# ============================================================
# QQ 语音消息大小上限约 4MB（silk 编码后）
VOICE_MAX_BYTES = 4 * 1024 * 1024

# 直接发送的条件：文件大小 <= 3MB 且为 MP3
DIRECT_SEND_MAX_BYTES = 3 * 1024 * 1024


def _get_ffmpeg_path() -> Optional[str]:
    """查找系统中的 ffmpeg 可执行文件"""
    import shutil
    found = shutil.which("ffmpeg")
    if found:
        return found
    for p in [
        r"E:\ffmpeg-master-latest-win64-gpl\bin\ffmpeg.exe",
        r"C:\ffmpeg\bin\ffmpeg.exe",
        r"D:\ffmpeg\bin\ffmpeg.exe",
    ]:
        if Path(p).exists():
            return p
    return None


_FFMPEG_PATH = None


def _get_ffmpeg() -> Optional[str]:
    global _FFMPEG_PATH
    if _FFMPEG_PATH is None:
        _FFMPEG_PATH = _get_ffmpeg_path() or ""
    return _FFMPEG_PATH or None


async def _prepare_voice_audio(file_path: Path) -> Path:
    """
    准备用于 QQ 语音消息发送的音频：
    - 文件 <= 3MB 且为 MP3：直接返回
    - 文件 > 3MB 或为 FLAC：转码为 WAV 单声道 16kHz（NapCat 兼容最好）
    - 不截断时长，QQ 当前版本支持长语音
    """
    if not file_path.exists():
        return file_path

    ffmpeg = _get_ffmpeg()
    file_size = file_path.stat().st_size
    is_flac = file_path.suffix.lower() == ".flac"

    # 直接发送条件：MP3 且不大
    if not is_flac and file_size <= DIRECT_SEND_MAX_BYTES:
        return file_path

    # 需要转码：输出为 WAV（NapCat 处理更稳定）
    voice_path = file_path.with_suffix(".voice.wav")
    if voice_path.exists():
        return voice_path

    if not ffmpeg:
        logger.warning("[点歌] 未找到 ffmpeg，尝试直接发送原文件")
        return file_path

    try:
        import asyncio
        cmd = [
            ffmpeg, "-y", "-i", str(file_path),
            "-ac", "1",           # 单声道
            "-ar", "16000",       # 16kHz（QQ 语音推荐）
            "-acodec", "pcm_s16le",
            "-f", "wav",
            str(voice_path),
        ]
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(process.communicate(), timeout=120)

        if process.returncode == 0 and voice_path.exists():
            vsize = voice_path.stat().st_size
            logger.info(
                f"[点歌] 语音转码: {file_path.name} → {voice_path.name} "
                f"({vsize / 1024 / 1024:.2f}MB)"
            )
            return voice_path
        else:
            err = stderr.decode("utf-8", errors="ignore")[:300] if stderr else ""
            logger.error(f"[点歌] ffmpeg 转码失败 (code={process.returncode}): {err}")
            return file_path
    except Exception as e:
        logger.error(f"[点歌] 音频转码异常: {e}")
        return file_path


# ============================================================
# 搜索匹配
# ============================================================
def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio() * 100


def _search_local_songs(query: str, songs: List[Dict], threshold: int = 50, max_results: int = 5) -> List[Dict]:
    """模糊搜索本地歌曲（支持简繁/日文汉字归一化匹配）"""
    results = []
    query = query.strip().lower()
    if not query:
        return results

    # 对 query 做双向归一化：正向（简体→繁体）和反向（繁体→简体）
    query_fwd = _normalize_text(query, "forward").lower()
    query_rev = _normalize_text(query, "reverse").lower()

    for song in songs:
        names = song.get("names", [])
        if not isinstance(names, list):
            names = [str(names)]

        best_score = 0
        best_name = ""
        for name in names:
            name_str = str(name)
            name_lower = name_str.lower()
            # 对 song name 也做双向归一化
            name_fwd = _normalize_text(name_lower, "forward")
            name_rev = _normalize_text(name_lower, "reverse")

            # 4 种匹配方向，取最高分
            for q, n in [(query, name_lower), (query_fwd, name_fwd), (query_rev, name_rev), (query_fwd, name_rev)]:
                score = _similarity(q, n)

                if q == n:
                    score = max(score, 100)

                if q in n:
                    match_ratio = len(q) / max(1, len(n))
                    score = max(score, 80 + min(15, match_ratio * 15))
                elif n in q and len(n) >= 3:
                    score = min(score, 40)

                if score > best_score:
                    best_score = score
                    best_name = name_str

        if best_score >= threshold:
            results.append({
                "song": song,
                "score": best_score,
                "matched_name": best_name,
                "source": "local",
            })

    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:max_results]


# ============================================================
# 网易云 API 搜索与下载
# ============================================================
def _sanitize_filename(name: str) -> str:
    """清理文件名中的非法字符"""
    return re.sub(r'[\\/:*?"<>|]', "_", name).strip()


async def _ncm_search(query: str, limit: int = 5) -> List[Dict]:
    """搜索网易云音乐（优先直接HTTP请求，更稳定；失败再试pyncm）"""
    # 优先用直接HTTP请求（网易云WeAPI反爬较严，pyncm容易失败）
    result = await _ncm_search_direct(query, limit)
    if result:
        return result

    # 直接HTTP失败，尝试 pyncm 作为备用
    if PYNCM_AVAILABLE:
        logger.warning("[点歌] 直接HTTP搜索无结果，尝试pyncm")
        try:
            if cloudsearch and hasattr(cloudsearch, 'GetSearchResult'):
                r = cloudsearch.GetSearchResult(
                    keyword=query,
                    search_type=1,
                    limit=limit,
                )
            else:
                r = ncm_apis.cloudsearch.GetSearchResult(
                    keyword=query,
                    search_type=1,
                    limit=limit,
                )
            if isinstance(r, dict):
                songs = r.get("result", {}).get("songs", [])
                if songs:
                    parsed = []
                    for s in songs:
                        if not isinstance(s, dict):
                            continue
                        artists = s.get("ar", [])
                        artist_name = "、".join(a.get("name", "") for a in artists if isinstance(a, dict)) if artists else "未知"
                        parsed.append({
                            "ncm_id": s.get("id"),
                            "name": s.get("name", ""),
                            "artist": artist_name,
                            "album": s.get("al", {}).get("name", "") if isinstance(s.get("al"), dict) else "",
                            "duration": s.get("dt", 0),
                        })
                    return parsed
        except Exception as e:
            logger.error(f"[点歌] pyncm搜索也失败: {e}")

    logger.warning(f"[点歌] 网易云搜索无结果: {query}")
    return []


async def _ncm_search_direct(query: str, limit: int = 5) -> List[Dict]:
    """直接使用HTTP请求搜索网易云音乐"""
    try:
        import httpx
        url = "https://music.163.com/api/search/get/web"
        params = {
            "s": query,
            "type": 1,
            "limit": limit,
            "offset": 0,
        }
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/120.0.0.0 Safari/537.36",
            "Referer": "https://music.163.com/",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        # 带上Cookie以获取更准确的搜索结果
        cookie = _get_ncm_cookie()
        if cookie:
            headers["Cookie"] = cookie

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url, params=params, headers=headers)
            if resp.status_code != 200:
                logger.error(f"[点歌] 直接HTTP搜索失败: HTTP {resp.status_code}")
                return []
            
            try:
                result = resp.json()
            except Exception:
                logger.error(f"[点歌] 直接HTTP搜索返回非JSON: {resp.text[:100]}")
                return []
            
            songs = result.get("result", {}).get("songs", [])
            parsed = []
            for s in songs:
                if not isinstance(s, dict):
                    continue
                artists = s.get("artists", []) or s.get("ar", [])
                artist_name = "、".join(a.get("name", "") for a in artists if isinstance(a, dict)) if artists else "未知"
                parsed.append({
                    "ncm_id": s.get("id"),
                    "name": s.get("name", ""),
                    "artist": artist_name,
                    "album": s.get("album", {}).get("name", "") if isinstance(s.get("album"), dict) else s.get("al", {}).get("name", ""),
                    "duration": s.get("duration", 0) or s.get("dt", 0),
                })
            return parsed
    except Exception as e:
        logger.error(f"[点歌] 直接HTTP搜索异常: {e}")
        return []


# 音质降级链（从高到低），用于下载失败时自动降级
_QUALITY_FALLBACK_CHAIN = ["hires", "lossless", "exhigh", "higher", "standard"]


async def _ncm_download(song_id: int, song_name: str, artist: str, quality: str = None) -> Optional[tuple]:
    """
    下载网易云音乐到本地（优先直接HTTP请求，更稳定；失败再试pyncm）
    高音质下载失败时自动降级到更低音质
    返回 (file_path, actual_quality) 或 None
    """
    target_quality = _get_effective_quality(quality)

    # 构建降级链：从用户请求的音质开始，逐级降级
    start_idx = _QUALITY_FALLBACK_CHAIN.index(target_quality) if target_quality in _QUALITY_FALLBACK_CHAIN else 3
    fallback_chain = _QUALITY_FALLBACK_CHAIN[start_idx:]

    last_error = None
    _last_fee: Optional[int] = None  # 记录官方/备用API返回的 fee 字段，用于下载失败后诊断（Cookie vs 版权锁死）
    has_cookie = False
    for idx, q in enumerate(fallback_chain):
        if idx > 0:
            logger.warning(f"[点歌] 音质「{QUALITY_LABELS.get(target_quality, target_quality)}」下载失败，"
                           f"降级尝试「{QUALITY_LABELS.get(q, q)}」")
        direct_result = await _ncm_download_direct(song_id, song_name, artist, q)
        # 兼容老返回（None / Path）和新返回（Tuple[Path|None, dict]）
        path: Optional[Path] = None
        ctx: dict = {}
        if isinstance(direct_result, tuple) and len(direct_result) >= 2:
            path, ctx = direct_result[0], direct_result[1] or {}
        else:
            path = direct_result
            ctx = {}
        if isinstance(ctx, dict):
            ctx_fee = ctx.get("fee")
            if ctx_fee is not None:
                try:
                    fi = int(ctx_fee)
                    if _last_fee is None or fi > int(_last_fee):
                        _last_fee = fi
                except Exception:
                    pass
            if not has_cookie and ctx.get("has_cookie"):
                has_cookie = True
        if path:
            if q != target_quality:
                logger.info(f"[点歌] 已降级下载: {target_quality} → {q}")
            return (path, q)

        # 直接HTTP失败，尝试 pyncm 作为备用（注意 pyncm 的 EAPI 容易被风控拦截，成功率较低）
        # 特别：如果 pyncm 的 session 仍为 dict 形态（没替换成真 HTTP 客户端），
        # 就直接跳过 pyncm，避免 WeAPI request failed 刷屏
        if PYNCM_AVAILABLE and not _PYNCM_DICT_SESSION:
            try:
                # pyncm 的 GetTrackAudio 使用 EAPI（加密接口），风控更严
                # 先记录一下 pyncm session 当前 cookies，辅助定位是否登录态缺失
                _sess = GetCurrentSession() if PYNCM_AVAILABLE else None
                if _sess:
                    # 兼容 pyncm 不同版本：session 可能是 dict 或 requests.Session 对象
                    _cookie_str = ""
                    _cookie_keys: list = []
                    if isinstance(_sess, dict):
                        _cookie_str = str((_sess.get("headers") or {}).get("Cookie", "") or "")
                        _c = _sess.get("cookies")
                        if isinstance(_c, dict):
                            _cookie_keys = list(_c.keys())
                        elif hasattr(_c, "keys"):
                            try:
                                _cookie_keys = list(_c.keys())
                            except Exception:
                                pass
                    else:
                        _h = getattr(_sess, "headers", None)
                        if isinstance(_h, dict):
                            _cookie_str = str(_h.get("Cookie", "") or "")
                        elif hasattr(_h, "get"):
                            try:
                                _cookie_str = str(_h.get("Cookie", "") or "")
                            except Exception:
                                pass
                        _c = getattr(_sess, "cookies", None)
                        if _c is not None:
                            try:
                                _cookie_keys = list(_c.keys())
                            except Exception:
                                _cookie_keys = []
                    _has_music_u = any(
                        k.strip().lower() == "music_u"
                        for k in _cookie_keys + [
                            item.split("=", 1)[0] if "=" in item else ""
                            for item in _cookie_str.split(";")
                        ]
                    )
                    logger.debug(f"[点歌] pyncm会话MUSIC_U状态: {_has_music_u}")

                if track and hasattr(track, 'GetTrackAudio'):
                    audio_data = track.GetTrackAudio(
                        song_ids=song_id,
                        level=q,
                    )
                else:
                    audio_data = ncm_apis.track.GetTrackAudio(
                        song_ids=song_id,
                        level=q,
                    )
                if isinstance(audio_data, dict):
                    data_list = audio_data.get("data", [])
                    if data_list and data_list[0].get("url"):
                        path = await _download_audio(data_list[0]["url"], song_name, artist, q)
                        if path:
                            if q != target_quality:
                                logger.info(f"[点歌] 已降级下载(pyncm): {target_quality} → {q}")
                            return (path, q)
                    elif data_list:
                        logger.warning(
                            f"[点歌] pyncm返回空URL｜版权信息: fee={data_list[0].get('fee')}, st={data_list[0].get('st')}"
                        )
            except Exception as e:
                last_error = e
                err_msg = str(e)
                if "EAPI" in err_msg or "Expecting value" in err_msg or "column 1" in err_msg:
                    # EAPI 失败时尝试诊断：抓 pyncm session 最近一次请求的返回码
                    extra = ""
                    try:
                        _sess2 = GetCurrentSession() if PYNCM_AVAILABLE else None
                        if _sess2:
                            # pyncm 使用 requests.Session，如果刚刚请求过通常会有历史
                            pass
                    except Exception:
                        pass
                    logger.warning(
                        f"[点歌] pyncm下载({q})跳过（EAPI返回非JSON，疑似风控或登录态未正确加载{extra}）"
                    )
                else:
                    logger.warning(f"[点歌] pyncm下载({q})失败: {e}")

    logger.error(f"[点歌] 所有音质下载均失败 (song_id={song_id}, last_error={last_error})")

    # 所有音质都失败时，检测 Cookie 是否过期
    cookie_valid_tuple = await _check_cookie_validity()
    cookie_valid = cookie_valid_tuple[0] if isinstance(cookie_valid_tuple, tuple) else cookie_valid_tuple
    cookie_info = cookie_valid_tuple[1] if (isinstance(cookie_valid_tuple, tuple) and len(cookie_valid_tuple) > 1) else None
    if cookie_valid is False:
        logger.warning("[点歌] 下载失败且Cookie已过期，用户需要更新Cookie")
        # Cookie过期信息会在 handle_music 中通过 _notify_cookie_expired 提醒
        _COOKIE_STATUS["need_notify"] = True
    elif cookie_valid and has_cookie and (_last_fee is None or _last_fee >= 1):
        # Cookie 有效 + 歌曲是VIP类(fee>=1) 但URL仍然空
        vip_now = _COOKIE_STATUS.get("vipType", 0) if isinstance(_COOKIE_STATUS, dict) else 0
        if cookie_info:
            vip_now = cookie_info.get("vipType", vip_now) or 0
        nick_now = (cookie_info or {}).get("nickname") if cookie_info else _COOKIE_STATUS.get("nickname")
        vip_label = {0: "非会员", 1: "VIP", 10: "黑胶VIP", 11: "SVIP"}.get(vip_now, f"Type{vip_now}")
        if vip_now in (10, 11):
            logger.warning(
                f"[点歌] Cookie为{vip_label}(用户: {nick_now or '未知'})，但该歌曲仍返回空URL "
                f"→ 该歌曲确实是「版权独占锁死」，即使是SVIP API也不给下载链接（不是Cookie/Cookie配置问题）"
            )
        elif vip_now == 1:
            logger.warning(
                f"[点歌] Cookie为{vip_label}(用户: {nick_now or '未知'})，但该歌曲仍返回空URL "
                f"→ 可能需要更高等级的SVIP才能下载，或歌曲版权独占锁死"
            )
        else:
            logger.warning(
                f"[点歌] 服务器识别当前Cookie是「{vip_label}」(用户: {nick_now or '未知'}) "
                f"→ 若你的账号确实是SVIP，请检查Cookie内容是否完整（必须包含 MUSIC_U，且不要用中文或特殊字符转义坏的粘贴内容）"
            )

    return None


def _build_ncm_client():
    """构建httpx客户端（保持会话，避免CDN 403）
    注意：不在默认headers或cookie jar中设置Cookie，
    而是用 httpx.Request 对象显式传递Cookie header，避免httpx cookie jar覆盖。
    """
    import httpx

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://music.163.com/",
    }
    client = httpx.AsyncClient(timeout=60.0, follow_redirects=True, headers=headers)

    return client


async def _ncm_download_direct(song_id: int, song_name: str, artist: str, quality: str = None) -> Tuple[Optional[Path], dict]:
    """直接使用HTTP请求下载网易云音乐（同一会话，避免CDN 403）

    返回 (audio_path_or_None, ctx):
        ctx 是 dict，目前包含:
            - fee: Optional[int]  — 官方/备用API返回的歌曲 fee 字段（供外层诊断）
            - has_cookie: bool    — 本次请求是否携带了 Cookie
            - reason: str         — 简单说明（"ok" / "no_url" / "html_not_audio"）
    """
    effective_quality = _get_effective_quality(quality)
    quality_map = {"standard": 128000, "higher": 192000, "exhigh": 320000, "lossless": 0, "hires": 0}
    br = quality_map.get(effective_quality, 128000)

    has_cookie = bool(_get_ncm_cookie())
    cookie_str = _get_ncm_cookie()
    ctx_fee: Optional[int] = None
    ctx_reason = "no_url"

    def _update_fee_from_data(data0) -> None:
        """从 data[0] 对象中提取 fee 并更新 ctx_fee（永远取最大的 fee 值）"""
        nonlocal ctx_fee
        try:
            if isinstance(data0, dict):
                f = data0.get("fee")
                if f is not None:
                    try:
                        fi = int(f)
                    except Exception:
                        return
                    if ctx_fee is None or fi > ctx_fee:
                        ctx_fee = fi
        except Exception:
            pass

    if has_cookie:
        logger.info(f"[点歌] 开始下载(带Cookie): {song_name} - {artist} (id={song_id}, br={br})")
    else:
        logger.info(f"[点歌] 开始下载(无Cookie): {song_name} - {artist} (id={song_id}, br={br})")

    import httpx as _httpx
    client = _build_ncm_client()
    audio_url = None
    try:
        # 先访问首页和歌曲页，建立正常浏览会话（CDN校验用）
        try:
            await client.get("https://music.163.com/")
            await client.get(f"https://music.163.com/song?id={song_id}")
        except Exception:
            pass

        # 构建带Cookie的公共请求头
        api_headers = {}
        if cookie_str:
            api_headers["Cookie"] = cookie_str

        # 方式1：官方API获取URL（用 Request 对象直接发送，绕过 cookie jar 覆盖）
        try:
            req = _httpx.Request(
                "GET",
                "https://music.163.com/api/song/enhance/player/url",
                params={"ids": f'[{song_id}]', "br": br, "csrf_token": ""},
                headers=api_headers,
            )
            resp = await client.send(req)
            if resp.status_code == 200:
                result = resp.json()
                result_code = result.get("code", -1)
                data_list = result.get("data", [])
                if data_list and data_list[0].get("url"):
                    audio_url = data_list[0]["url"]
                    actual_br = data_list[0].get("br", 0)
                    logger.info(f"[点歌] 官方API获取URL成功 (br={br}, actual_br={actual_br})")
                else:
                    # 详细记录版权字段，便于判断是Cookie失效还是真版权受限
                    if data_list:
                        d0 = data_list[0]
                        fee = d0.get("fee")
                        st = d0.get("st")
                        payed = d0.get("payed")
                        level = d0.get("level")
                        per_song_code = d0.get("code")  # data[0].code（注意不是外层 result.code）
                        cannot_reason = None
                        try:
                            fp = (d0.get("freeTrialPrivilege") or {}) if isinstance(d0, dict) else {}
                            cannot_reason = fp.get("cannotListenReason") if isinstance(fp, dict) else None
                        except Exception:
                            cannot_reason = None
                        _update_fee_from_data(d0)
                        extra = ""
                        if per_song_code == -110:
                            extra += "｜per_song_code=-110(版权不足/需要付费)"
                        if cannot_reason is not None:
                            extra += f"｜cannotListenReason={cannot_reason}"
                        if payed == 0 and has_cookie:
                            extra += "｜服务器未识别到付费会话(payed=0)"
                        logger.warning(
                            f"[点歌] 官方API返回空URL (code={result_code}, has_cookie={has_cookie})"
                            f"｜版权信息: fee={fee}, st={st}, payed={payed}, level={level}, br={br}{extra}"
                        )
                        logger.debug(f"[点歌] API返回data完整详情: {d0}")
                    else:
                        logger.warning(f"[点歌] 官方API返回空URL且data为空 (code={result_code}, has_cookie={has_cookie})")
        except Exception as e:
            logger.error(f"[点歌] 官方API获取URL失败: {e}")

        # 方式2：备用API v1
        if not audio_url:
            try:
                req = _httpx.Request(
                    "GET",
                    "https://music.163.com/api/song/enhance/player/url/v1",
                    params={"ids": f'[{song_id}]', "level": effective_quality, "encodeType": "mp3", "csrf_token": ""},
                    headers=api_headers,
                )
                resp = await client.send(req)
                if resp.status_code == 200:
                    result = resp.json()
                    result_code = result.get("code", -1)
                    data_list = result.get("data", [])
                    if data_list and data_list[0].get("url"):
                        audio_url = data_list[0]["url"]
                        logger.info(f"[点歌] 备用API获取URL成功 (level={effective_quality})")
                    else:
                        if data_list:
                            d0 = data_list[0]
                            fee = d0.get("fee")
                            st = d0.get("st")
                            _update_fee_from_data(d0)
                            logger.warning(
                                f"[点歌] 备用API返回空URL (code={result_code})"
                                f"｜版权信息: fee={fee}, st={st}, level={d0.get('level')}"
                            )
                        else:
                            logger.warning(f"[点歌] 备用API返回空URL且data为空 (code={result_code})")
            except Exception as e:
                logger.error(f"[点歌] 备用API下载失败: {e}")

        # 方式2.5：POST player/url（模拟网页播放页真实请求：/api/song/enhance/player/url + immerseType=1 + realIP）
        # 注意：不能用 /weapi/ 那个路径！那需要加密 payload（pyncm 的 EAPI/WeAPI 加密流程），
        # 直接发纯表单到 /weapi/ 会被返回 460/HTML 而不是 JSON。
        if not audio_url:
            try:
                post_headers = dict(api_headers)
                post_headers["Content-Type"] = "application/x-www-form-urlencoded"
                post_headers["Origin"] = "https://music.163.com"
                # 从已有 cookie 中提取 __csrf，没有就留空
                csrf = ""
                if cookie_str:
                    for _item in cookie_str.split(";"):
                        _item = _item.strip()
                        if _item.lower().startswith("__csrf") and "=" in _item:
                            csrf = _item.split("=", 1)[1].strip()
                            break
                req = _httpx.Request(
                    "POST",
                    f"https://music.163.com/api/song/enhance/player/url?csrf_token={csrf}",
                    data={
                        "ids": f"[{song_id}]",
                        "br": str(br),
                        "level": effective_quality,
                        "encodeType": "mp3",
                        "immerseType": "1",
                        "withCredentials": "true",
                        "realIP": "",
                        "csrf_token": csrf,
                    },
                    headers=post_headers,
                )
                resp = await client.send(req)
                raw_text = resp.text if resp is not None else ""
                preview = (raw_text or "")[:200].replace("\n", " ")
                if resp.status_code == 200:
                    try:
                        result = resp.json()
                    except Exception as json_err:
                        logger.error(
                            f"[点歌] PlayerURL_POST返回非JSON (HTTP {resp.status_code}): "
                            f"响应预览: {preview[:120]}｜{json_err}"
                        )
                        result = None
                    if result:
                        result_code = result.get("code", -1)
                        data_list = result.get("data", [])
                        if data_list and data_list[0].get("url"):
                            audio_url = data_list[0]["url"]
                            actual_br = data_list[0].get("br", 0)
                            logger.info(f"[点歌] PlayerURL_POST获取URL成功 (level={effective_quality}, actual_br={actual_br})")
                        else:
                            if data_list:
                                d0 = data_list[0]
                                fee = d0.get("fee")
                                st = d0.get("st")
                                _update_fee_from_data(d0)
                                logger.warning(
                                    f"[点歌] PlayerURL_POST返回空URL (code={result_code})"
                                    f"｜版权信息: fee={fee}, st={st}, level={d0.get('level')}, payed={d0.get('payed')}"
                                )
                            else:
                                logger.warning(f"[点歌] PlayerURL_POST返回空URL且data为空 (code={result_code})")
                else:
                    logger.error(
                        f"[点歌] PlayerURL_POST请求失败 HTTP {resp.status_code}: 响应预览: {preview[:120]}"
                    )
            except Exception as e:
                logger.error(f"[点歌] PlayerURL_POST获取URL异常: {e}")

        # 方式3：外链接口
        if not audio_url:
            try:
                audio_url = f"https://music.163.com/song/media/outer/url?id={song_id}.mp3"
                logger.info(f"[点歌] 尝试外链接口下载")
            except Exception as e:
                logger.error(f"[点歌] 外链接口下载失败: {e}")

        # 获取到URL后，用同一会话下载（CDN URL 无需 Cookie）
        if audio_url:
            path = await _download_with_client(client, audio_url, song_name, artist, effective_quality)
            if path:
                ctx_reason = "ok"
                return (path, {"fee": ctx_fee, "has_cookie": has_cookie, "reason": ctx_reason})
            ctx_reason = "html_not_audio"

    finally:
        await client.aclose()

    logger.error(f"[点歌] 所有下载方式均失败 (song_id={song_id}, has_cookie={has_cookie})")
    return (None, {"fee": ctx_fee, "has_cookie": has_cookie, "reason": ctx_reason})


def _is_valid_audio_data(data: bytes) -> bool:
    """通过文件头 magic number 判断是否为有效音频"""
    if len(data) < 12:
        return False
    # MP3: ID3v2 标签 (开头 49 44 33) 或 MPEG 帧头 (FF FB / FF F3 / FF F2)
    if data[:3] == b"ID3":
        return True
    if data[0] == 0xFF and (data[1] & 0xFE) == 0xFA:
        return True
    # WAV: RIFF + WAVE
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return True
    # FLAC: fLaC
    if data[:4] == b"fLaC":
        return True
    # OGG: OggS
    if data[:4] == b"OggS":
        return True
    # M4A/AAC: ftyp
    if data[4:8] == b"ftyp":
        return True
    return False


async def _download_with_client(client, url: str, song_name: str, artist: str, quality: str = None) -> Optional[Path]:
    """使用已有的httpx client下载音频（保持会话，避免CDN 403）"""
    try:
        safe_name = _sanitize_filename(f"{song_name} - {artist}")
        file_ext = ".mp3"

        resp = await client.get(url)
        if resp.status_code not in (200, 206):
            logger.error(f"[点歌] 下载失败: HTTP {resp.status_code}")
            return None

        content = resp.content

        content_type = resp.headers.get("content-type", "")
        if content_type and "text/html" in content_type.lower():
            logger.warning(f"[点歌] 下载的是 HTML 页面而非音频 (Content-Type: {content_type})")
            return None

        # 根据音质或内容类型判断文件格式
        if quality in ("lossless", "hires"):
            file_ext = ".flac"
        elif content[:4] == b"fLaC":
            file_ext = ".flac"
        elif content[:3] == b"ID3" or (content[0] == 0xFF and (content[1] & 0xFE) == 0xFA):
            file_ext = ".mp3"

        file_path = MUSIC_DIR / f"{safe_name} [{quality}]{file_ext}"

        if len(content) < 100 * 1024:
            logger.warning(f"[点歌] 下载文件过小 ({len(content)}B)，跳过")
            return None

        if not _is_valid_audio_data(content):
            logger.warning(f"[点歌] 下载文件不是有效音频 (magic: {content[:8].hex()})，跳过")
            return None

        file_path.write_bytes(content)
        file_size = file_path.stat().st_size
        logger.info(f"[点歌] 下载完成: {file_path.name} ({file_size / 1024 / 1024:.1f}MB)")
        return file_path

    except Exception as e:
        logger.error(f"[点歌] 下载异常: {e}")
        return None


async def _download_audio(url: str, song_name: str, artist: str, quality: str = None) -> Optional[Path]:
    """下载音频文件到本地（自动建立会话，避免CDN 403）"""
    client = _build_ncm_client()
    try:
        # 先访问首页建立会话
        try:
            await client.get("https://music.163.com/")
        except Exception:
            pass
        return await _download_with_client(client, url, song_name, artist, quality)
    finally:
        await client.aclose()


async def _search_and_download_ncm(query: str, quality: str = None) -> Optional[Dict]:
    """
    从网易云搜索并下载歌曲
    返回歌曲信息字典（含本地文件路径），失败返回 None
    如果搜索成功但下载失败，返回 {"_search_ok": True, "_download_failed": True}
    原唱下载失败时，自动尝试翻唱版本
    """
    # 清理末尾/开头的情绪标点，避免干扰网易云搜索
    _SEARCH_PUNCT_RE = re.compile(r'[\s!?！？~～…・·]+$|^[\s!?！？~～…・·]+')
    search_query = _SEARCH_PUNCT_RE.sub('', query).strip()
    if not search_query:
        search_query = query

    search_limit = int(_conf("ncm_search_limit", 5))
    ncm_results = await _ncm_search(search_query, limit=search_limit)

    if not ncm_results:
        return None

    # 区分原唱（歌名与用户输入完全一致）和翻唱（歌名不一致）
    query_clean = query.strip().lower()
    original_candidates = [r for r in ncm_results if str(r.get("name", "")).strip().lower() == query_clean]
    cover_candidates = [r for r in ncm_results if str(r.get("name", "")).strip().lower() != query_clean]

    # 如果没有完全匹配的原唱，则把搜索结果第一条当作原唱
    if not original_candidates:
        original_candidates = [ncm_results[0]]
        cover_candidates = ncm_results[1:]

    # 优先尝试原唱，记录原唱歌手名用于后续比较
    original_artist = ""
    original_name = ""
    for candidate in original_candidates:
        original_artist = candidate.get("artist", "")
        original_name = candidate.get("name", "")
        song_id = candidate["ncm_id"]
        artist = candidate["artist"]
        album = candidate.get("album", "")

        result = await _download_ncm_by_id(song_id, original_name, artist, quality, album=album, is_cover=False)
        if result:
            return result

    # 原唱失败，尝试翻唱版本（注意歌手不同才算真正翻唱，同人不同版不算）
    for candidate in cover_candidates:
        song_id = candidate["ncm_id"]
        song_name = candidate["name"]
        artist = candidate["artist"]
        album = candidate.get("album", "")

        # 同一歌手的不同版本（live/remix/现场版等）不算翻唱，不发送翻唱提示
        is_cover = True
        if original_artist and original_artist == artist:
            is_cover = False
        # 从歌名中的 (Cover xxx) / (cover xxx) 提取原作者，若与歌手相同也不算翻唱
        cover_match = re.search(r'\((?:Cover|cover|COVER)\s+(.+?)\)', song_name)
        if cover_match and cover_match.group(1).strip() == artist:
            is_cover = False

        # 计算原唱显示名（用于提示文字和持久化保存）
        original_display = ""
        if is_cover:
            if cover_match:
                original_display = cover_match.group(1).strip()
            elif original_artist:
                original_display = original_artist
            else:
                original_display = "原作者"

        result = await _download_ncm_by_id(
            song_id, song_name, artist, quality, album=album,
            is_cover=is_cover, original_artist=original_display
        )
        if result:
            if is_cover:
                logger.info(f"[点歌] 原唱({original_display})版权受限，已选择翻唱: {song_name} - {artist}")
            else:
                logger.info(f"[点歌] 原唱版本不可下载，已选择同歌手其他版本: {song_name} - {artist}")
            return result

    # 所有搜索结果都下载失败
    return {
        "_search_ok": True,
        "_download_failed": True,
        "song_name": ncm_results[0]["name"],
        "artist": ncm_results[0]["artist"],
    }


async def _download_ncm_by_id(song_id: int, song_name: str, artist: str,
                              quality: str = None, album: str = "",
                              is_cover: bool = False, original_artist: str = "") -> Optional[Dict]:
    """按 ncm_id 直接下载指定音质（无需搜索），用于本地已有但音质不符合时的重下"""
    logger.info(f"[点歌] 按ID下载: {song_name} - {artist} (id={song_id}, quality={quality}, is_cover={is_cover})")

    effective_quality = _get_effective_quality(quality)
    result = await _ncm_download(song_id, song_name, artist, effective_quality)
    if not result:
        return None

    file_path, actual_quality = result
    song_info = {
        "names": [song_name],
        "artist": artist,
        "file": file_path.name,
        "source": "netease",
        "ncm_id": song_id,
        "album": album,
        "quality": actual_quality,
        "is_cover": is_cover,
        "original_artist": original_artist,
    }
    _add_song_to_library(song_info)

    return {
        "song": song_info,
        "score": 100,
        "matched_name": song_name,
        "source": "netease",
        "is_new": True,
        "was_fallback": actual_quality != effective_quality,
        "is_cover": is_cover,
        "original_artist": original_artist,
    }


# ============================================================
# 指令：点歌
# ============================================================
music_cmd = on_command(
    "点歌",
    aliases={"播放", "来首", "来首歌", "听歌", "放歌"},
    priority=5,
    block=True,
)


@music_cmd.handle()
async def handle_music(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    try:
        if not _is_enabled():
            return

        raw_query = args.extract_plain_text().strip()
        if not raw_query:
            await music_cmd.finish("❓ 点什么歌？用法：点歌 <歌名> [音质]\n音质选项：标准/高清/极高/无损/母带")
            return

        # 不做任何字符翻译，保留用户输入原样（日文/个性字体均不转换）
        # raw_query = _normalize_text(raw_query)

        # 清理用户情绪标点：仅用于网易云搜索，保留原始 query 用于本地匹配和显示
        # 例如 "春日影-MyGO！！！！！" 搜索时 → "春日影-MyGO"
        _SEARCH_PUNCT_RE = re.compile(r'[\s!?！？~～…・·]+$|^[\s!?！？~～…・·]+')
        search_query = _SEARCH_PUNCT_RE.sub('', raw_query).strip()

        # 解析用户输入中的音质选项
        query, user_quality = _parse_quality_from_query(raw_query)
        if not query:
            await music_cmd.finish("❓ 请输入歌曲名！用法：点歌 <歌名> [音质]")
            return

        threshold = int(_conf("similarity_threshold", 50) or 50)
        max_results = int(_conf("max_results", 5) or 5)
        send_info_raw = _conf("send_info", True)
        send_info = str(send_info_raw).strip().lower() not in ("false", "0", "no", "")

        # 计算实际音质（用户指定优先，无Cookie自动降级）
        effective_quality = _get_effective_quality(user_quality)
        quality_label = QUALITY_LABELS.get(effective_quality, "标准")

        # 点歌时按需检测 Cookie 有效性（缓存5分钟，避免频繁请求）
        if _get_ncm_cookie():
            cookie_valid_tuple = await _check_cookie_validity()
            cookie_valid = cookie_valid_tuple[0] if isinstance(cookie_valid_tuple, tuple) else cookie_valid_tuple
            if cookie_valid is False:
                _COOKIE_STATUS["need_notify"] = True

        # ====== 第一步：搜索本地音乐库 ======
        songs = _load_songs()
        local_results = _search_local_songs(query, songs, threshold=threshold, max_results=max_results)

        # 用户指定音质时：优先取本地同音质条目，没有再取同歌不同音质
        if local_results and user_quality:
            same_q = [r for r in local_results if (r["song"].get("quality", "") == effective_quality)]
            if same_q:
                local_results = same_q + [r for r in local_results if r not in same_q]

        # ====== 本地低分数交叉验证：防止误匹配名字相似的不同歌曲 ======
        # 例：なんもねえ ←/→ なんでもない（两首完全不同的歌）
        crosscheck_threshold = int(_conf("similarity_crosscheck_threshold", 75) or 75)
        enable_ncm = str(_conf("enable_ncm", True)).strip().lower() not in ("false", "0", "no", "")
        if (local_results and enable_ncm and PYNCM_AVAILABLE
                and local_results[0]["score"] < crosscheck_threshold):
            try:
                search_limit = int(_conf("ncm_search_limit", 5))
                ncm_results = await _ncm_search(search_query, limit=search_limit)
                if ncm_results:
                    query_lower = search_query.strip().lower()
                    # 检查网易云结果是否存在"与用户输入完全一致"或"用户输入完全包含"的歌名
                    exact_hit = False
                    for c in ncm_results:
                        cname = str(c.get("name", "")).strip().lower()
                        if cname == query_lower or query_lower in cname:
                            exact_hit = True
                            break
                    if exact_hit:
                        # 网易云有精确歌名匹配，说明本地模糊命中的是另一首歌，放弃本地结果
                        logger.info(
                            f"[点歌] 本地分数{local_results[0]['score']:.0f}%<{crosscheck_threshold}%，"
                            f"且网易云存在「{query}」精确歌名，跳过本地模糊结果"
                        )
                        local_results = []
            except Exception as e:
                logger.warning(f"[点歌] 网易云交叉验证失败，继续使用本地结果: {e}")

        if local_results:
            # 本地找到，直接播放
            top = local_results[0]
            song = top["song"]
            score = top["score"]
            matched = top["matched_name"]

            file_name = song.get("file", "")
            file_path = MUSIC_DIR / file_name
            local_quality = song.get("quality", "")

            is_valid, error_msg = _is_valid_audio_file(file_path)

            # ===== 音质对比：用户指定音质，本地无对应音质版本，则下载对应音质作为新条目 =====
            redownloaded = False
            was_fallback = False
            need_upgrade = False
            ncm_id = song.get("ncm_id")
            enable_ncm = str(_conf("enable_ncm", True)).strip().lower() not in ("false", "0", "no", "")
            if user_quality and is_valid and enable_ncm and PYNCM_AVAILABLE and ncm_id:
                # 本地找到的条目音质与用户请求不同（说明前面没找到同音质版本）
                if _is_upgrade_needed(local_quality, user_quality):
                    need_upgrade = True
                    stored_q_label = QUALITY_LABELS.get(local_quality, "标准") if local_quality else "未知"
                    try:
                        await music_cmd.send(
                            f"🔀 本地无「{quality_label}」音质，"
                            f"正在下载（现有「{stored_q_label}」将保留）..."
                        )
                    except Exception:
                        pass
                    # 取原有的 name/artist/album 作为下载元数据
                    names = song.get("names", [])
                    primary_name = str(names[0]) if names else matched
                    artist = song.get("artist", "") or ""
                    album = song.get("album", "") or ""
                    try:
                        dl = await _download_ncm_by_id(int(ncm_id), primary_name, artist,
                                                       user_quality, album=album)
                        if dl:
                            song = dl["song"]
                            file_name = song.get("file", "")
                            file_path = MUSIC_DIR / file_name
                            is_valid, error_msg = _is_valid_audio_file(file_path)
                            if is_valid:
                                redownloaded = True
                                local_quality = song.get("quality", "")
                                was_fallback = dl.get("was_fallback", False)
                    except Exception as e:
                        logger.error(f"[点歌] 下载指定音质失败，回退到本地旧音质: {e}")

            if not is_valid:
                # 本地文件失效，尝试从网易云下载
                logger.warning(f"[点歌] 本地文件失效: {error_msg}，尝试网易云")
            else:
                # 发送本地音乐
                # 提前计算 display_quality（下载提示要用，即使 send_info=False）
                stored_quality = song.get("quality", "")
                display_quality = QUALITY_LABELS.get(stored_quality, quality_label) if stored_quality else quality_label
                if send_info:
                    names = song.get("names", [])
                    primary_name = str(names[0]) if names else matched
                    artist = song.get("artist", "")
                    info_line = f"🎵 {primary_name}"
                    if artist:
                        info_line += f" - {artist}"
                    # 翻唱提示（本地已有非原唱标注）
                    if song.get("is_cover"):
                        orig = song.get("original_artist") or "原作者"
                        info_line += f"\n🎤 该歌曲原唱（{orig}）受版权保护，已为你选择非原唱歌曲 - {artist}"
                    if redownloaded:
                        if was_fallback:
                            info_line += f"\n📂 来源：网易云「降级下载」"
                            info_line += f"\n⚠️ 「{quality_label}」需要SVIP，已自动降级为「{display_quality}」"
                        else:
                            info_line += f"\n📂 来源：网易云（已下载「{display_quality}」音质并缓存）"
                    else:
                        info_line += f"\n📂 来源：本地音乐库"
                    info_line += f"\n🎧 音质：{display_quality}"
                    if need_upgrade and not redownloaded:
                        info_line += f"（下载失败，使用本地旧音质）"
                    # 文字先发送
                    await music_cmd.send(info_line)

                # 大文件或 FLAC 需转码为语音适用的 MP3
                voice_path = await _prepare_voice_audio(file_path)
                file_uri = voice_path.as_uri()
                # 语音单独发送（用 send 而非 finish，因为后面还要发提示）
                await music_cmd.send(MessageSegment.record(file_uri))
                await music_cmd.finish()
                return

        # ====== 第二步：本地没有，从网易云搜索下载 ======
        enable_ncm = str(_conf("enable_ncm", True)).strip().lower() not in ("false", "0", "no", "")

        if not enable_ncm:
            # 列出本地部分歌名供参考
            all_names = []
            for s in songs[:10]:
                ns = s.get("names", [])
                if isinstance(ns, list) and ns:
                    all_names.append(str(ns[0]))
            hint = f"💡 试试这些：{'、'.join(all_names)}" if all_names else ""
            await music_cmd.finish(f"❌ 没找到「{query}」相关的歌曲\n{hint}")
            return

        if not PYNCM_AVAILABLE:
            await music_cmd.finish(
                f"❌ 本地未找到「{query}」\n"
                f"⚠️ 网易云搜索功能未启用（pyncm 未安装）\n"
                f"请运行：pip install pyncm"
            )
            return

        # 告知用户正在搜索
        search_msg = f"🔍 本地未找到「{query}」，正在网易云搜索..."
        if user_quality:
            search_msg += f"\n🎧 请求音质：{quality_label}"
        try:
            await music_cmd.send(search_msg)
        except Exception:
            pass

        result = await _search_and_download_ncm(query, user_quality)

        # 判断是"没找到"还是"找到但下载失败"
        if not result:
            # 完全没找到
            fail_msg = f"❌ 网易云也没找到「{query}」"
            # 检测 Cookie 是否过期并提醒
            need_notify = _COOKIE_STATUS.pop("need_notify", False)
            if need_notify:
                fail_msg += "\n\n⚠️ 网易云Cookie可能已过期！"
                fail_msg += "\n高品质音乐无法下载，请更新Cookie"
                fail_msg += "\n获取方式：浏览器登录 music.163.com → F12 → Application → Cookies → 复制 MUSIC_U"
                asyncio.create_task(_notify_cookie_expired(bot, event))
            await music_cmd.finish(fail_msg)
            return

        if result.get("_download_failed"):
            # 找到但下载失败（版权限制等）
            song_name = result.get("song_name", query)
            artist = result.get("artist", "")
            fail_msg = f"❌ 找到「{song_name}」"
            if artist:
                fail_msg += f" - {artist}"
            fail_msg += "\n但网易云因版权限制无法下载"
            # 区分 Cookie 是否为真实 SVIP：
            #   - vipType in (10,11) → 服务器认了SVIP，歌曲才是版权锁死（Cookie真没问题）
            #   - 仅配置了MUSIC_U但vipType=0 → 服务器不认该Cookie为付费会员
            has_cookie_str = bool(_get_ncm_cookie())
            vip_now = 0
            try:
                vip_now = int(_COOKIE_STATUS.get("vipType", 0) or 0)
            except Exception:
                vip_now = 0
            nickname_now = _COOKIE_STATUS.get("nickname") if isinstance(_COOKIE_STATUS, dict) else None
            if has_cookie_str and vip_now in (10, 11):
                vip_label = "黑胶SVIP" if vip_now == 11 else "黑胶VIP"
                fail_msg += f"\n💡 该歌曲为版权独占资源，{vip_label}也无法通过API下载"
                who = f"（用户: {nickname_now}）" if nickname_now else ""
                fail_msg += f"\n（Cookie已生效{who}，其他歌曲可正常下载高品质）"
            elif has_cookie_str:
                # 有 cookie 但服务器未识别为 VIP（本次你遇到的情况）
                fail_msg += "\n💡 该歌曲可能为VIP专属，但当前Cookie未被识别为SVIP会员"
                if nickname_now:
                    fail_msg += f"\n（当前Cookie账号: {nickname_now}，非付费会员）"
                else:
                    fail_msg += "\n（请检查MUSIC_U是否来自你的SVIP账号，而不是其他普通账号）"
                fail_msg += "\n建议重新从SVIP账号浏览器复制完整Cookie后再试"
            else:
                fail_msg += "\n💡 该歌曲可能为VIP专属或地区限制"
                fail_msg += "\n建议配置网易云Cookie后重试"
            # 检测 Cookie 是否过期并提醒
            need_notify = _COOKIE_STATUS.pop("need_notify", False)
            if need_notify:
                fail_msg += "\n\n⚠️ 网易云Cookie可能已过期！"
                fail_msg += "\n高品质音乐无法下载，请更新Cookie"
                fail_msg += "\n获取方式：浏览器登录 music.163.com → F12 → Application → Cookies → 复制 MUSIC_U"
                asyncio.create_task(_notify_cookie_expired(bot, event))
            await music_cmd.finish(fail_msg)
            return

        # 正常下载结果
        song = result["song"]
        matched = result["matched_name"]
        is_new = result.get("is_new", False)
        was_fallback = result.get("was_fallback", False)

        file_name = song.get("file", "")
        file_path = MUSIC_DIR / file_name

        is_valid, error_msg = _is_valid_audio_file(file_path)
        if not is_valid:
            await music_cmd.finish(f"❌ 下载失败：{error_msg}")
            return

        # 发送音乐
        # 提前计算 display_quality（下载提示要用，即使 send_info=False）
        actual_quality = song.get("quality", "")
        display_quality = QUALITY_LABELS.get(actual_quality, quality_label) if actual_quality else quality_label
        if send_info:
            names = song.get("names", [])
            primary_name = str(names[0]) if names else matched
            artist = song.get("artist", "")
            info_line = f"🎵 {primary_name}"
            if artist:
                info_line += f" - {artist}"
            # 翻唱提示
            if song.get("is_cover") or result.get("is_cover"):
                orig = song.get("original_artist") or result.get("original_artist") or "原作者"
                info_line += f"\n🎤 该歌曲原唱（{orig}）受版权保护，已为你选择非原唱歌曲 - {artist}"
            if was_fallback:
                info_line += f"\n📂 来源：网易云搜索「降级下载」"
                info_line += f"\n⚠️ 「{quality_label}」需要SVIP，已自动降级为「{display_quality}」"
            else:
                info_line += f"\n📂 来源：网易云搜索"
                info_line += f"\n🎧 音质：{display_quality}"
            if is_new:
                info_line += "\n✅ 已自动缓存到本地，下次点歌更快"
            # 文字先发送
            await music_cmd.send(info_line)

        # 大文件或 FLAC 需转码为语音适用的 MP3
        voice_path = await _prepare_voice_audio(file_path)
        file_uri = voice_path.as_uri()
        # 语音单独发送（用 send 而非 finish，因为后面还要发提示）
        await music_cmd.send(MessageSegment.record(file_uri))
        await music_cmd.finish()

    except FinishedException:
        raise
    except Exception as e:
        logger.error(f"[点歌] 处理失败: {e}")
        import traceback
        logger.error(traceback.format_exc())
        try:
            await music_cmd.finish(f"❌ 点歌出错了：{e}")
        except FinishedException:
            pass


# ============================================================
# 指令：歌单
# ============================================================
playlist_cmd = on_command(
    "歌单",
    aliases={"音乐列表", "点歌列表", "歌曲列表"},
    priority=5,
    block=True,
)


@playlist_cmd.handle()
async def handle_playlist(event: MessageEvent):
    if not _is_enabled():
        return

    songs = _load_songs()
    if not songs:
        await playlist_cmd.finish("❌ 本地音乐库为空\n💡 点歌时会自动从网易云下载并缓存")
        return

    # 合并同一首歌的不同音质版本（按 ncm_id 或主歌名分组）
    groups: Dict[str, List[Dict]] = {}
    order: List[str] = []
    for s in songs:
        key = str(s.get("ncm_id") or "")
        if not key:
            names = s.get("names", [])
            key = str(names[0]) if isinstance(names, list) and names else str(id(s))
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(s)

    total_count = len(songs)
    song_count = len(groups)
    display_count = min(song_count, MAX_PLAYLIST_DISPLAY)

    lines = [f"📋 本地歌单（共 {song_count} 首，{total_count} 个音质版本，显示前 {display_count} 首）", "━━━━━━━━━━━━"]

    for i, key in enumerate(order[:MAX_PLAYLIST_DISPLAY], 1):
        group = groups[key]
        # 取第一个作为代表
        rep = group[0]
        names = rep.get("names", [])
        if not isinstance(names, list):
            names = [str(names)]
        primary = str(names[0]) if names else "未知"
        artist = rep.get("artist", "")
        source = rep.get("source", "local")
        source_tag = "☁️" if source == "netease" else "📁"
        alt_names = "、".join(str(n) for n in names[1:3]) if len(names) > 1 else ""
        # 收集本组所有音质标签
        q_tags = []
        for g in group:
            q = g.get("quality", "")
            if q:
                q_tags.append(QUALITY_LABELS.get(q, q))
        quality_str = "/".join(q_tags) if q_tags else ""
        # 检查本组是否存在翻唱版本
        is_cover = any(g.get("is_cover", False) for g in group)
        line = f"{i}. {source_tag} {primary}"
        if artist:
            line += f" - {artist}"
        if quality_str:
            line += f" [{quality_str}]"
        if is_cover:
            line += " [非原唱]"
        if alt_names:
            line += f" ({alt_names})"
        lines.append(line)

    if song_count > MAX_PLAYLIST_DISPLAY:
        lines.append(f"... 还有 {song_count - MAX_PLAYLIST_DISPLAY} 首")

    lines += ["━━━━━━━━━━━━", "📁=本地添加  ☁️=网易云缓存  [非原唱]=翻唱版本", "音质: 标=标准 清=高清 极=极高 无=无损 母=母带", "用法：点歌 <歌名> [音质]"]
    await playlist_cmd.finish("\n".join(lines))


# ============================================================
# 指令：刷新歌单缓存
# ============================================================
refresh_cmd = on_command(
    "刷新歌单",
    aliases={"刷新音乐", "重载歌单"},
    priority=5,
    block=True,
)


@refresh_cmd.handle()
async def handle_refresh(event: MessageEvent):
    if not _is_enabled():
        return

    _invalidate_cache()
    songs = _load_songs()
    await refresh_cmd.finish(f"✅ 歌单已刷新，共 {len(songs)} 首")


# ============================================================
# 菜单注册
# ============================================================
try:
    from plugins.miku_menu import register_plugin_info
except ImportError:
    register_plugin_info = lambda *args, **kwargs: None

ncm_status = "✅ 已启用" if PYNCM_AVAILABLE else "❌ 未安装(pip install pyncm)"

register_plugin_info(
    "miku_music",
    name="Miku点歌",
    icon="🎵",
    order=7,
    description="本地+网易云点歌，自动下载缓存，可同步到文件分享站",
    commands=["点歌", "歌单", "刷新歌单"],
    usage=f"""点歌 <歌名> [音质]
支持音质：标准/高清/极高/无损/母带
默认标准音质，可指定高品质
本地没有则从网易云搜索下载
下载后自动缓存，下次更快

歌单
查看本地音乐列表（最多20首）
📁=本地添加  ☁️=网易云缓存 [标/清/极/无/母]=音质

刷新歌单
重新加载歌曲配置

网易云搜索：{ncm_status}
下载音质：标准/高清/极高/无损/母带（有Cookie支持高品质）
音乐文件：data/music/ 目录""",
)

# 插件加载时初始化 pyncm 会话（加载Cookie）
_init_pyncm_session()

logger.info(f"[miku_music] 点歌插件已加载 (pyncm: {'已安装' if PYNCM_AVAILABLE else '未安装'}, cookie: {'已配置' if _get_ncm_cookie() else '未配置'})")
