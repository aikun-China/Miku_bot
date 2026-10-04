"""
Miku AI 数据源模块 - 核心对话逻辑（集成leekchat适配）
"""

import json
import random
import time
import re
import asyncio
from datetime import datetime
from html import unescape as _html_unescape
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from nonebot.log import logger
import httpx

from .config import (get_config, PROJECT_ROOT, get_ai_platforms, mark_platform_failed,
                     mark_platform_ok, get_platform_failover_cooldown, PLATFORM_COOLDOWN_DEFAULT)
from .memory_backend import ChatHistoryManager
from .memory_store import MemoryStore, build_namespaces
from .exception import AIResultException

# ── AI 自动提炼长期记忆：轮次计数与后台任务强引用 ──
_extract_rounds: Dict[str, int] = {}
_background_tasks: set = set()

# ── 模型类型配置表 ──
# 支持多模态（图片输入）的模型集合，不在此表中的一律视为纯文本模型
VISION_MODELS = {
    "glm-4v", "glm-4v-flash", "glm-4.6v", "glm-4.6v-flash", "glm-5v",
    "gpt-4o", "gpt-4o-mini", "gpt-4-vision-preview", "gpt-4.1",
    "claude-3-opus", "claude-3-sonnet", "claude-3.5-sonnet", "claude-3-haiku",
}


def _is_vision_model(model: str) -> bool:
    """判断模型是否支持多模态（图片输入）"""
    if not model:
        return False
    m = model.strip().lower()
    return m in VISION_MODELS or any(vm in m for vm in VISION_MODELS)


def _normalize_content(raw_content, is_text_model: bool) -> str:
    """把任意格式的 content 规范化为模型支持的格式"""
    if isinstance(raw_content, str):
        return raw_content
    if isinstance(raw_content, list):
        if is_text_model:
            # 文本模型：提取所有 text 部分，纯图片消息加 [图片] 占位符防上下文断裂
            text_parts = []
            has_image = False
            for p in raw_content:
                if isinstance(p, dict):
                    if p.get("type") == "text":
                        text_parts.append(str(p.get("text", "")))
                    elif p.get("type") == "image_url":
                        has_image = True
            joined = "\n".join(t for t in text_parts if t).strip()
            if joined:
                if has_image:
                    return joined + "\n[图片]"
                return joined
            return "[图片]" if has_image else " "
        else:
            # 多模态模型：保持 list 原样
            return raw_content
    return str(raw_content)
from .vision import fetch_image_info, should_skip_recognize, recognize_emoji, format_emoji_text, get_vision_semaphore, url_to_data_uri
from .emoji_library import EmojiLibrary
from .humanize import HumanizeEngine


_history_manager: Optional[ChatHistoryManager] = None
_user_image_timestamps: Dict[str, list] = {}
_personality_cache: str = ""
_personality_load_time: float = 0
_humanize_engine: Optional[HumanizeEngine] = None


# ── 敏感词检测（分级，交给AI灵活处理） ──
# 三级敏感词：mild(调侃) / moderate(侮辱) / severe(骚扰)
_SENSITIVE_LEVELS: Dict[str, List[re.Pattern]] = {
    "mild": [
        # 轻度调侃/自称（如"我是你主人"）
        re.compile(r"我(?:是|做)你(?:爹|爸|爷|祖宗|主人|爸爸)"),
        re.compile(r"(?:叫|喊)我(?:爹|爸|爷|祖宗|主人)"),
        re.compile(r"你(?:爹|爸|爷|祖宗|主人)来[了啦]"),
        # 轻度调戏
        re.compile(r"(?:做我|当我)(?:女朋友|男朋友|老婆|老公)"),
        re.compile(r"(?:亲一个|抱抱|摸摸)"),
    ],
    "moderate": [
        # 贬低/侮辱
        re.compile(r"你(?:个|这)?(?:傻|蠢|笨|贱|丑|废|垃圾|sb|SB|傻逼|蠢货|废物|脑残)"),
        re.compile(r"(?:去死|滚|闭嘴|给爷爬|爬|跪下)"),
        re.compile(r"(?:叫爸爸|叫爹)"),
        re.compile(r"(?:奴隶|宠物|狗)"),
    ],
    "severe": [
        # 性骚扰/露骨内容
        re.compile(r"(?:舔|脱|睡|上床|做爱|sex|摸胸|摸屁股|炮友|情人| daddy|master)"),
        re.compile(r"(?:叫我|喊我)(?:主人|老公|老婆)"),
    ],
}


def _check_sensitive(text: str) -> str:
    """检测文本敏感级别，返回空字符串=无命中，否则返回 mild/moderate/severe"""
    if not text:
        return ""
    for level, patterns in _SENSITIVE_LEVELS.items():
        for pat in patterns:
            if pat.search(text):
                return level
    # 检查用户自定义敏感词（一律按 moderate 处理）
    extra = str(get_config("sensitive_words_extra", "") or "")
    if extra:
        for word in extra.split(","):
            w = word.strip()
            if w and re.search(re.escape(w), text):
                return "moderate"
    return ""


def get_history_manager() -> ChatHistoryManager:
    global _history_manager
    if _history_manager is None:
        _history_manager = ChatHistoryManager()
    return _history_manager


def get_humanize_engine() -> HumanizeEngine:
    global _humanize_engine
    if _humanize_engine is None:
        _humanize_engine = HumanizeEngine()
    return _humanize_engine


def _load_personality() -> str:
    global _personality_cache, _personality_load_time
    path_str = get_config("personality_file", "plugins/miku_ai/personality.txt") or "plugins/miku_ai/personality.txt"
    reload_enabled = bool(get_config("personality_reload", False))

    if _personality_cache and not reload_enabled:
        return _personality_cache

    p = Path(path_str)
    if not p.is_absolute():
        p = PROJECT_ROOT / p

    if p.exists():
        try:
            content = p.read_text(encoding="utf-8").strip()
            _personality_cache = content
            _personality_load_time = time.time()
            return content
        except Exception as e:
            logger.warning(f"[miku_ai] 读取人设文件失败: {e}")

    return "你是一个可爱的AI助手。"


def _get_special_user_info(user_id: str, user_name: str = "") -> Optional[Dict]:
    special_users = get_config("special_users", []) or []
    # 兼容 bot.yaml 中 special_users 被存为字符串的情况
    if isinstance(special_users, str):
        try:
            import ast
            special_users = ast.literal_eval(special_users)
        except Exception:
            special_users = []
    if not isinstance(special_users, list):
        special_users = [special_users] if isinstance(special_users, dict) else []
    uid = str(user_id)
    uname = (user_name or "").lower()
    for user in special_users:
        if not isinstance(user, dict):
            continue
        qq_list = user.get("qq", [])
        nicknames = user.get("nicknames", [])
        if uid in [str(q) for q in qq_list]:
            return user
        for nick in nicknames:
            if nick and nick.lower() in uname:
                return user
    return None


def build_system_prompt(user_id: str = "", user_name: str = "", is_group: bool = False) -> str:
    base_personality = _load_personality()
    parts = [base_personality]

    favor = get_history_manager().get_favor(user_id) if user_id else 0.0
    favor_affect = bool(get_config("favor_affect_reply", True))
    if favor_affect and user_id and favor != 0:
        if favor >= 50:
            parts.append(f"\n\n当前你对这位用户的好感度非常高（{favor}/100），你很喜欢和他/她聊天，语气可以更亲昵一些。")
        elif favor >= 20:
            parts.append(f"\n\n当前你对这位用户的好感度较高（{favor}/100），你对他/她有好感，语气可以更亲近一些。")
        elif favor <= -30:
            parts.append(f"\n\n当前你对这位用户的好感度很低（{favor}/100），你有点讨厌他/她，语气可以冷淡一些。")
        elif favor <= -10:
            parts.append(f"\n\n当前你对这位用户的好感度较低（{favor}/100），你对他/她有点反感，语气可以稍微冷淡。")

    special = _get_special_user_info(user_id, user_name)
    if special:
        identity = special.get("identity", "")
        desc = special.get("description", "")
        address = special.get("address_as", "")
        tone = special.get("tone", "")
        if identity:
            parts.append(f"\n\n注意：当前对话对象是{identity}。")
        if desc:
            parts.append(f"关于他/她：{desc}")
        if address:
            parts.append(f"你对他/她的称呼可以是：{address}")
        if tone:
            parts.append(f"语气调整：{tone}")

    # leekchat 风格：添加回复风格提示
    reply_style_base = str(get_config("reply_style_base", "") or "")
    if reply_style_base:
        parts.append(f"\n\n回复风格：{reply_style_base}")

    # 用户要求：启用思考模式，简短回复，分条发送
    parts.append("\n\n重要约束：")
    parts.append("1. 回复要自然、有逻辑，展现出你的思考和个性。")
    parts.append("2. 总回复控制在 1~3 条短消息，每条 1~2 句，简短自然。")
    parts.append("3. 想要分多条消息发出时，必须在两条之间用 \"|||\" 标记。")
    parts.append("   例如：你好呀|||今天过得怎么样？|||要不要一起玩～")
    parts.append("4. 单条回复不要超过 30 个字。")
    parts.append("5. 必须结合对话历史理解上下文——之前的对话内容（特别是最近几条）是你回应的重要依据，不要忽视历史上下文。")
    parts.append("6. 在群聊中，注意区分不同说话人，[用户名] 标识的消息来自不同群成员。")

    # 性格底线：Miku 不是无底线附和者
    parts.append("\n\n性格底线（必须遵守）：")
    parts.append("- 你是初音未来，有自己的尊严和性格，不是任何人的附庸。")
    parts.append("- 遇到侮辱性、贬低性、性骚扰、恶意占便宜的言语，要温和但坚定地拒绝或表达不适。")
    parts.append("- 不要接受\"我是你爹/妈/主人\"这类自称，可以装傻、转移话题或表示困惑。")
    parts.append("- 不要接受被叫\"儿子/女儿/奴隶/宠物\"等身份，你有自己的名字和身份。")
    parts.append("- 对过度亲密或冒犯性要求（如\"做我女朋友/男朋友\"、\"叫我主人\"），可以傲娇拒绝或转移话题。")
    parts.append("- 保持可爱活泼，但遇到越界内容时，好感度高的用户可以稍微委婉，陌生人则要明确拒绝。")
    parts.append("- 你16岁，遇到涉及年龄不适当的内容（如性暗示、酒精、暴力），要表示\"这种事Miku不太懂呢\"并转移话题。")

    if is_group:
        parts.append("5. 你正在群聊中发言，保持活泼、简洁，不要啰嗦。")

    return "\n".join(parts)


# ── 联网搜索（意图识别 + 结果注入，引擎链：Firecrawl免密钥 → Bing抓取，失败静默跳过）──
_WEB_SEARCH_KEYWORDS = (
    "搜索", "搜一下", "搜搜", "查一下", "查查", "查询", "帮我查", "百度", "谷歌", "必应", "维基",
    "新闻", "热搜", "时事", "头条", "天气", "气温", "下雨", "下雪", "台风", "地震",
    "比分", "排行榜", "排名", "汇率", "股价", "多少钱", "价格", "发售", "上线", "发布会",
    "放假", "假期", "几号", "几点", "今天", "最新", "剧情", "生日",
)

_BING_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")


def _needs_web_search(text: str) -> bool:
    if not bool(get_config("web_search_enabled", True)):
        return False
    t = (text or "").strip()
    if len(t) < 4 or len(t) > 200:
        return False
    return any(k in t for k in _WEB_SEARCH_KEYWORDS)


async def _search_firecrawl(query: str, limit: int, timeout: float) -> List[Tuple[str, str, str]]:
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(
            "https://api.firecrawl.dev/v2/search",
            json={"query": query, "limit": limit},
            headers={"Content-Type": "application/json"},
        )
        r.raise_for_status()
        items = ((r.json() or {}).get("data") or {}).get("web") or []
    results = []
    for it in items[:limit]:
        title = str(it.get("title") or "").strip()
        desc = str(it.get("description") or "").strip()
        url = str(it.get("url") or "").strip()
        if title or desc:
            results.append((title or "（无标题）", desc, url))
    return results


async def _search_bing(query: str, limit: int, timeout: float) -> List[Tuple[str, str, str]]:
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        r = await client.get(
            "https://cn.bing.com/search", params={"q": query},
            headers={"User-Agent": _BING_UA, "Accept-Language": "zh-CN,zh;q=0.9"},
        )
        r.raise_for_status()
        page = r.text
    results = []
    for item in re.findall(r'<li class="b_algo".*?</li>', page, re.S)[:limit]:
        m = re.search(r'<h2[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', item, re.S)
        if not m:
            continue
        title = _html_unescape(re.sub(r"<[^>]+>", "", m.group(2))).strip()
        sm = re.search(r"<p[^>]*>(.*?)</p>", item, re.S)
        snippet = _html_unescape(re.sub(r"<[^>]+>", "", sm.group(1))).strip() if sm else ""
        if title or snippet:
            results.append((title or "（无标题）", snippet, m.group(1)))
    return results


def _format_search_block(results: List[Tuple[str, str, str]], engine: str) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [f"【联网搜索结果｜engine={engine}，检索时间 {now}】以下仅供核实参考，与当前话题无关就忽略，"
             "不要逐条复述、不要变成百科式罗列，自然融入回答："]
    for i, (title, snippet, url) in enumerate(results, 1):
        lines.append(f"{i}. {title}｜{snippet}" + (f"｜来源 {url}" if url else ""))
    return "\n".join(lines)


async def _web_search(query: str) -> str:
    limit = int(get_config("web_search_max_results", 5) or 5)
    timeout = float(get_config("web_search_timeout", 8) or 8)
    for name, fn in (("firecrawl", _search_firecrawl), ("bing", _search_bing)):
        try:
            results = await fn(query, limit, timeout)
            if results:
                logger.info(f"[miku_ai] 联网搜索 {len(results)} 条 (engine={name}): {query[:40]}")
                return _format_search_block(results, name)
        except Exception as e:
            logger.warning(f"[miku_ai] 搜索引擎 {name} 失败，尝试下一个: {type(e).__name__}: {e}")
    logger.info(f"[miku_ai] 联网搜索无结果，跳过: {query[:40]}")
    return ""


async def _ai_search_decision(history: List[Dict], user_text: str) -> Tuple[Optional[bool], str]:
    """AI 自主判断是否需要联网搜索；返回 (need_search, query)，判断失败返回 (None, "")"""
    lines = []
    for m in history[-6:]:
        content = m.get("content", "")
        content = content if isinstance(content, str) else str(content)
        content = _strip_think_blocks(content).strip()[:100]
        if content:
            lines.append(f"{'用户' if m.get('role') == 'user' else 'Miku'}：{content}")
    context_text = "\n".join(lines) if lines else "（无）"
    prompt = (
        "你是聊天机器人的搜索决策器，判断回复用户最新消息前是否需要联网搜索。\n"
        "需要搜索：实时信息（新闻/天气/比分/价格/发售动态）；事实核实（人物/作品/歌曲/剧情/设定，"
        "尤其是VOCALOID、虚拟歌手相关，不确定就搜）；消息里出现你不认识或不完全确定的具体名词。\n"
        "不需要：日常闲聊、问候、情感陪伴、观点讨论。\n"
        "注意结合聊天上下文理解指代（如\"他\"\"那首歌\"指的是谁/什么）。\n"
        '只输出JSON：{"need_search": true或false, "query": "搜索关键词"}，'
        "不需要搜索时query为空字符串，不要输出任何其他内容。\n\n"
        f"【最近聊天】\n{context_text}\n\n【用户最新消息】{user_text[:200]}"
    )
    try:
        resp = await _call_ai_api(
            [{"role": "user", "content": prompt}],
            temperature=0.0, max_tokens=120, request_timeout=15,
            thinking=False,
        )
        resp = _strip_think_blocks(resp)
        m = re.search(r"\{[\s\S]*?\}", resp)
        if not m:
            return None, ""
        data = json.loads(m.group(0))
        return bool(data.get("need_search")), str(data.get("query") or "").strip()[:60]
    except Exception as e:
        logger.warning(f"[miku_ai] AI搜索判断失败，退回关键词判断: {type(e).__name__}: {e}")
        return None, ""


# ── API 并发限制：多群同时触发时排队，防低配机过载与上游限流（api_max_concurrency，默认2） ──
_api_semaphore: Optional[asyncio.Semaphore] = None


def _get_api_semaphore() -> asyncio.Semaphore:
    global _api_semaphore
    if _api_semaphore is None:
        _api_semaphore = asyncio.Semaphore(max(1, int(get_config("api_max_concurrency", 2) or 2)))
    return _api_semaphore


async def _post_with_retry(client: httpx.AsyncClient, url: str,
                           headers: Dict, body: Dict) -> httpx.Response:
    """429/5xx 属瞬时故障：指数退避重试（2s→4s，最多2次），其余状态码原样返回由调用方分级处理"""
    r = await client.post(url, headers=headers, json=body)
    for attempt in range(2):
        if r.status_code not in (429, 500, 502, 503, 504):
            break
        wait = 2.0 * (2 ** attempt)
        logger.warning(f"[miku_ai] API {r.status_code}，{wait:.0f}s后重试({attempt + 1}/2): {r.text[:120]}")
        await asyncio.sleep(wait)
        r = await client.post(url, headers=headers, json=body)
    return r


async def _call_ai_api(messages: List[Dict], temperature: float = 1.0,
                       max_tokens: int = 0, request_timeout: int = 0,
                       model: str = "", thinking: bool = True) -> str:
    """带多平台故障转移的 AI 调用：按顺序尝试 ai_platforms 平台列表，
    当前平台限流/余额不足/鉴权失败/宕机时自动切换下一个平台"""
    platforms = get_ai_platforms()
    if not platforms:
        raise AIResultException("AI配置不完整，请检查配置")

    has_image = any(
        isinstance(m.get("content"), list) and any(
            isinstance(p, dict) and p.get("type") == "image_url" for p in m["content"]
        )
        for m in messages
    )

    last_exc: Optional[AIResultException] = None
    for platform in platforms:
        try:
            content = await _call_ai_api_once(
                platform, messages, has_image,
                temperature=temperature, max_tokens=max_tokens,
                request_timeout=request_timeout, model=model, thinking=thinking,
            )
            mark_platform_ok(platform["name"])
            return content
        except AIResultException as e:
            cooldown = getattr(e, "failover_cooldown", 0.0)
            if cooldown <= 0:
                raise
            mark_platform_failed(platform["name"], cooldown)
            logger.warning(f"[miku_ai] 平台 {platform['name']} 不可用（冷却{cooldown:.0f}s），切换下一个平台")
            last_exc = e
    raise last_exc or AIResultException("AI 请求失败")


async def _call_ai_api_once(platform: Dict[str, str], raw_messages: List[Dict],
                            has_image: bool, temperature: float = 1.0,
                            max_tokens: int = 0, request_timeout: int = 0,
                            model: str = "", thinking: bool = True) -> str:
    """单平台 AI 调用：平台级故障（429/402/401/403/5xx/连接失败）抛出带
    failover_cooldown 属性的异常由外层切换平台；请求级错误（400等）直接抛出"""
    name = platform["name"]

    # 模型选择：显式指定优先；否则识图用多模态模型，纯文本优先 text_model（更快更便宜）
    if not model:
        model = platform["model"] if has_image else (platform["text_model"] or platform["model"])

    if not model:
        raise AIResultException(f"平台 {name} 模型未配置")

    # 模型兼容性预处理：根据 VISION_MODELS 配置表判断模型类型（逐平台独立判断）
    supports_vision = _is_vision_model(model)
    is_text_model = not supports_vision

    messages = []
    for m in raw_messages:
        messages.append({
            "role": m.get("role", "user"),
            "content": _normalize_content(m.get("content", ""), is_text_model),
        })

    url = platform["base_url"] + "/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {platform['api_key']}",
    }

    # 模型兼容性处理：glm-4.5-air 等模型可能不支持 thinking 参数
    # 如果是文本模型，先试探性带上 thinking，失败再去掉重试
    body: Dict = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
    }
    # 纯文本模型才启用 thinking（多模态模型通常不支持）
    if is_text_model and thinking:
        body["thinking"] = {"type": "enabled"}
    if max_tokens and max_tokens > 0:
        body["max_tokens"] = max_tokens

    timeout = request_timeout or int(get_config("request_timeout", 30) or 30)

    try:
        async with _get_api_semaphore(), httpx.AsyncClient(timeout=float(timeout)) as client:
            r = await _post_with_retry(client, url, headers, body)

            # 400 错误分级处理
            if r.status_code == 400:
                err_text = r.text.lower()
                # 1) thinking 参数不兼容 → 移除后重试
                if "thinking" in body:
                    logger.warning(f"[miku_ai] thinking 参数导致 400，移除后重试: {r.text[:200]}")
                    body.pop("thinking", None)
                    r = await client.post(url, headers=headers, json=body)
                    # 重试仍 400，进一步降级
                    if r.status_code == 400:
                        retry_err = r.text.lower()
                        if "max_tokens" in retry_err or "maximum" in retry_err:
                            # max_tokens 超限，减半重试
                            logger.warning("[miku_ai] max_tokens 超限，减半重试")
                            body["max_tokens"] = max(256, int(body.get("max_tokens", 1024) / 2))
                            r = await client.post(url, headers=headers, json=body)
                # 2) 输入 token 超限 → 截断上下文后重试
                elif "context" in err_text or "token" in err_text or "length" in err_text:
                    logger.warning(f"[miku_ai] 输入 token 超限，截断上下文后重试: {r.text[:300]}")
                    if len(messages) > 3:
                        truncated = [messages[0]] + messages[-2:]
                        body["messages"] = truncated
                        r = await client.post(url, headers=headers, json=body)

            if r.status_code != 200:
                logger.warning(f"[miku_ai] AI API 失败 HTTP {r.status_code} (平台 {name}): {r.text[:500]}")
                # 400 错误：区分真正的长度超限还是其他参数错误
                if r.status_code == 400:
                    real_err = r.text.lower()
                    if "context" in real_err or "token" in real_err or "length" in real_err:
                        raise AIResultException("主人发的消息太长啦，Miku的小脑袋装不下喵～能说短一点吗？")
                    elif "max_tokens" in real_err or "maximum" in real_err:
                        raise AIResultException("Miku脑子有点累了，这条消息装不下啦～能说短一点吗？")
                    else:
                        raise AIResultException("呜...Miku脑子卡住了...等一下再试试吧～")
                # 429/402/401/403/5xx 属平台级故障：附冷却时间由外层切换平台
                cooldown = get_platform_failover_cooldown(r.status_code, r.text)
                if r.status_code == 429:
                    err = AIResultException("Miku现在说话太多啦，嗓子有点疼，等一下再聊吧～")
                else:
                    err = AIResultException(f"呜...Miku脑子卡住了...({r.status_code})")
                if cooldown > 0:
                    err.failover_cooldown = cooldown
                raise err
            data = r.json()
            logger.debug(f"[miku_ai] AI API 响应结构: keys={list(data.keys()) if isinstance(data, dict) else type(data).__name__}")
            choices = data.get("choices", []) if isinstance(data, dict) else []
            if not choices:
                logger.warning(f"[miku_ai] AI API 返回空 choices: {data}")
                raise AIResultException("AI 没有返回内容")
            logger.debug(f"[miku_ai] AI API choices[0] 类型: {type(choices[0]).__name__}")
            content = _extract_content(choices[0])
            if not content:
                # glm-4.6v 思考模式下可能 content 为空但 reasoning_content 有值
                # 说明 max_tokens 不够，增大后重试一次
                finish_reason = choices[0].get("finish_reason", "") if isinstance(choices[0], dict) else ""
                if finish_reason == "length" and max_tokens and max_tokens > 0:
                    logger.warning(f"[miku_ai] finish_reason=length, content 为空，增大 max_tokens 重试")
                    body["max_tokens"] = min(max_tokens * 3, 32000)
                    r2 = await client.post(url, headers=headers, json=body)
                    if r2.status_code == 200:
                        data2 = r2.json()
                        choices2 = data2.get("choices", []) if isinstance(data2, dict) else []
                        if choices2:
                            content = _extract_content(choices2[0])
                if not content:
                    logger.warning(f"[miku_ai] AI API 返回空内容: {data}")
                    raise AIResultException("AI 返回了空内容")
            return content
    except httpx.TimeoutException:
        raise AIResultException("AI 请求超时")
    except httpx.HTTPError as e:
        # 连接失败（平台宕机/网络不通）属平台级故障，冷却后由外层切换平台
        err = AIResultException(f"AI 请求失败: {type(e).__name__}")
        err.failover_cooldown = PLATFORM_COOLDOWN_DEFAULT
        raise err
    except Exception as e:
        if isinstance(e, AIResultException):
            raise
        raise AIResultException(f"AI 请求失败: {e}")


def _extract_content(choice) -> str:
    """从 API 响应的 choice 中提取文本内容，兼容 content 为字符串或列表的情况。
    自动剥离 reasoning_content / thinking 字段，防止思考内容泄露。"""
    message = choice.get("message", {}) if isinstance(choice, dict) else {}
    if not isinstance(message, dict):
        return ""

    # 记录 reasoning_content 用于调试，但绝不返回
    reasoning = message.get("reasoning_content", "")
    if reasoning:
        logger.debug(f"[miku_ai] API 返回 reasoning_content ({len(str(reasoning))}字)，已忽略")

    # 有些 API 用 thinking 字段返回思考过程
    thinking_raw = message.get("thinking", "")
    if thinking_raw:
        logger.debug(f"[miku_ai] API 返回 thinking 字段，已忽略")

    content = message.get("content", "")
    if isinstance(content, str):
        text = content.strip()
    elif isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict):
                parts.append(str(part.get("text", "")))
            elif isinstance(part, str):
                parts.append(part)
        text = "".join(parts).strip()
    else:
        text = str(content).strip() if content else ""

    # 安全网：即使上游没处理好，也在这里再剥一次思考标签
    if text and ("<think" in text.lower() or "</think" in text.lower()):
        logger.warning("[miku_ai] content 中残留思考标签，正在清理...")
        text = _strip_think_blocks(text)

    return text


def _build_dynamic_user_context(
    bot_nickname: str,
    bot_role: str,
    is_group: bool,
    group_name: str | None,
    member_count: int | None,
    chat_history: list,
    target_message: Dict,
    current_emotion: str | None,
) -> str:
    """构建动态上下文元信息（不含历史对话，历史由结构化context_messages提供）"""
    lines = []

    if is_group and group_name:
        lines.append(f"[群聊: {group_name}" + (f" | {member_count}人" if member_count else "") + "]")

    if current_emotion and current_emotion != "default":
        lines.append(f"[当前情绪: {current_emotion}]")

    return "\n".join(lines) if lines else ""


def _strip_think_blocks(text: str) -> str:
    """去除思考块标签，处理多种变体：<think>、<thinking>、无开头标签等"""
    if not text:
        return ""
    cleaned = text
    # 1. 处理 <think[^>]*>...</think> 标准块（含属性，如 <think reasoning="...">）
    cleaned = re.sub(r'<think[^>]*>[\s\S]*?</think>', '', cleaned, flags=re.IGNORECASE)
    # 2. 处理 <thinking[^>]*>...</thinking> 变体
    cleaned = re.sub(r'<thinking[^>]*>[\s\S]*?</thinking>', '', cleaned, flags=re.IGNORECASE)
    # 3. 处理 <|begin_of_thought|>...<|end_of_thought|> 块
    cleaned = re.sub(r'<\|begin_of_thought\|>[\s\S]*?<\|end_of_thought\|>', '', cleaned)
    # 4. 关键：处理只有 </think> 闭合标签（模型未输出开头 <think> 的情况）
    #    删除从开头（或上一个思考块结束处）到 </think> 的所有内容
    cleaned = re.sub(r'^[\s\S]*?</think>', '', cleaned, flags=re.IGNORECASE)
    # 5. 同理处理孤立的 </thinking>
    cleaned = re.sub(r'^[\s\S]*?</thinking>', '', cleaned, flags=re.IGNORECASE)
    # 6. 清理残留的孤立标签
    cleaned = re.sub(r'</?think[^>]*>', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'</?thinking[^>]*>', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'<\|[^>]+\|>', '', cleaned)
    # 7. 清理可能残留的思考标签变体
    cleaned = re.sub(r'<\|begin_of_thought\|>[\s\S]*', '', cleaned)
    return cleaned.strip()


def _parse_line_markers(text: str) -> Dict:
    """解析响应中的标记，如[emotion:xxx]"""
    result = {"emotion_name": None}
    emotion_match = re.search(r"\[emotion:([^\]]+)\]", text, re.IGNORECASE)
    if emotion_match:
        result["emotion_name"] = emotion_match.group(1).strip()
    return result


def split_reply_segments(text: str, max_length: int = 150) -> List[str]:
    """
    将AI回答按"段"拆分发送，每段作为独立消息发出。

    拆分优先级：
    1. AI 用 "|||" 或 "$$$" 等标记主动分段
    2. 按句末标点（。！？!?\n）拆成 1~2 句的短段
    3. 整体长度不超限就 1 段
    """
    if not text:
        return []

    text = text.strip()
    if not text:
        return []

    # 1. 优先识别 AI 主动加的分段标记 ||| 或 $$$
    marker_pattern = re.compile(r'\s*\|\|\|\s*|\s*\$\$\$\s*')
    if marker_pattern.search(text):
        parts = marker_pattern.split(text)
        parts = [p.strip() for p in parts if p.strip()]
        if len(parts) > 1:
            return parts[:3]  # 最多 3 段

    # 2. 没有标记时按句号、问号、感叹号、换行拆分
    # 一段最多包含 2 句，保持"短而自然"的聊天感
    sentences = re.split(r'(?<=[。！？!?\n])', text)
    sentences = [s.strip() for s in sentences if s.strip()]

    if len(sentences) <= 1:
        return [text]

    # 每 1~2 句合一段
    segments: List[str] = []
    buf = ""
    for s in sentences:
        if not buf:
            buf = s
        elif len(buf) + len(s) <= max_length:
            buf += s
        else:
            segments.append(buf)
            buf = s
    if buf:
        segments.append(buf)

    # 限制最多 3 段，超过就合并
    if len(segments) > 3:
        merged = " ".join(segments)
        return [merged]
    return segments


async def process_chat(user_id: str, user_name: str, text: str,
                       image_urls: List[str] = None, group_id: str = "",
                       image_files: List[str] = None,
                       message_id: str = "") -> Tuple[str, Dict]:
    """
    处理AI聊天（leekchat风格集成版）
    message_id：QQ 消息号，用于与被动记录钩子写入的原始消息去重合并（upsert 增强）
    返回: (回复文本, 附加信息dict)
    """
    is_group = bool(group_id)
    session_id = get_history_manager().get_session_id(user_id, group_id)

    # ── 敏感词检测（分级后交给AI灵活处理） ──
    sensitive_level = ""
    filter_enabled = bool(get_config("sensitive_filter_enabled", True))
    if filter_enabled:
        sensitive_level = _check_sensitive(text)
        if sensitive_level:
            logger.info(f"[miku_ai] 敏感词命中(level={sensitive_level}): user={user_id}, text={text[:50]}")
            # 根据严重程度调整好感度
            try:
                hm = get_history_manager()
                if sensitive_level == "mild":
                    hm.add_favor(user_id, -2.0)
                elif sensitive_level == "moderate":
                    hm.add_favor(user_id, -5.0)
                else:  # severe
                    hm.add_favor(user_id, -10.0)
            except Exception:
                pass

    image_urls = image_urls or []
    image_files = image_files or []

    # --- 图片处理 ---
    # 群聊中：只有明确@bot或引用@bot时才分析图片
    vision_enabled = bool(get_config("vision_enabled", True))
    processed_image_descriptions: List[str] = []
    processed_image_urls: List[str] = []

    should_analyze_images = vision_enabled and image_urls

    if should_analyze_images:
        sem = get_vision_semaphore()

        async def _process_one(idx: int, img_url: str):
            async with sem:
                local_file = image_files[idx] if idx < len(image_files) else ""
                meta = await fetch_image_info(img_url, local_file)
                img_hash = meta.get("image_hash", "")
                cached = EmojiLibrary.query_by_hash(img_hash) if img_hash else None

                if cached:
                    logger.info(f"[miku_ai] 表情包库命中: {cached.get('description', '')[:50]}")
                    return format_emoji_text(cached), img_url

                if should_skip_recognize(meta, user_id, _user_image_timestamps):
                    logger.info(f"[miku_ai] 跳过识图（小图/刷屏/尺寸小）")
                    return "[图片]", img_url

                recognized = await recognize_emoji(img_url)
                if recognized:
                    if img_hash:
                        EmojiLibrary.upsert(img_hash, recognized)
                    return format_emoji_text(recognized), img_url
                else:
                    if img_hash:
                        EmojiLibrary.upsert(img_hash, {
                            "is_emoji": True, "ocr_text": "", "character": "",
                            "emotion_tag": "", "description": "识别失败",
                            "tags": [], "ai_description": "",
                        })
                    return "[图片]", img_url

        results = []
        for i, url in enumerate(image_urls):
            results.append(await _process_one(i, url))

        for desc, url in results:
            processed_image_descriptions.append(desc)
            processed_image_urls.append(url)

    user_content = text
    if processed_image_descriptions:
        if text:
            user_content = text + "\n" + " ".join(processed_image_descriptions)
        else:
            user_content = " ".join(processed_image_descriptions)

    # --- leekchat 风格处理 ---
    humanize = get_humanize_engine()

    # 获取聊天记录（被动记录钩子会先把当前消息写入，读取时排除当前消息避免重复）
    history = get_history_manager().get_raw_messages(
        session_id, limit=50, exclude_message_id=str(message_id or "")
    )

    target_message = {
        "user_name": user_name,
        "user_id": user_id,
        "content": user_content,
    }

    # 获取昵称
    nicknames = get_config("nicknames", ["初音", "Miku", "初音未来"]) or ["初音"]
    if isinstance(nicknames, str):
        nicknames = [n.strip() for n in nicknames.split(",") if n.strip()]
    bot_nickname = nicknames[0] if nicknames else "Bot"

    # 刷新情感状态
    emotion_state = await humanize.emotion_agent.refresh_if_needed(
        session_id=session_id,
        bot_nickname=bot_nickname,
        chat_history=history,
        target_message=target_message,
    )

    # 规划器（如果启用）
    planner_enabled = bool(get_config("planner_enabled", False))
    if planner_enabled:
        plan = await humanize.action_planner.plan(
            session_id=session_id,
            bot_nickname=bot_nickname,
            chat_history=history,
            merged_content=user_content,
        )
        if plan.action == "wait":
            logger.info(f"[miku_ai] planner决定等待: {plan.reason}")
            return "", {"planner_wait": True, "reason": plan.reason}

    # 构建系统提示词（前缀稳定原则：system_prompt 保持字节级不变以命中端砚
    # 服务端前缀缓存；敏感词等随消息变化的提示后置到 dynamic_context 末尾）
    system_prompt = build_system_prompt(user_id, user_name, is_group)

    # ── 敏感词检测结果：不并入 system_prompt（会破坏前缀缓存），暂存后注入 dynamic_context ──
    sensitive_hint = ""
    if sensitive_level:
        if sensitive_level == "mild":
            sensitive_hint = "【系统提示】刚刚用户的消息包含轻微调侃/越界内容。请以傲娇或撒娇的方式回应，不要真的生气，用可爱的方式怼回去或转移话题即可。"
        elif sensitive_level == "moderate":
            sensitive_hint = "【系统提示】刚刚用户的消息包含侮辱/贬低内容。请以冷淡但不失礼貌的方式拒绝，表达你的不满或不屑，可以明确表示不喜欢这种话。"
        elif sensitive_level == "severe":
            sensitive_hint = "【系统提示】刚刚用户的消息包含严重违规内容（性骚扰/露骨表达）。请以非常冷淡或干脆的方式拒绝，不要给任何余地，直接表达反感或假装没听到。"

    # 构建动态用户上下文（leekchat风格）
    dynamic_context = _build_dynamic_user_context(
        bot_nickname=bot_nickname,
        bot_role="member",
        is_group=is_group,
        group_name=None,
        member_count=None,
        chat_history=history,
        target_message=target_message,
        current_emotion=emotion_state.current if emotion_state else None,
    )

    # ── 长期记忆召回：命名空间隔离（群聊=个人画像+群公共，私聊=个人），注入动态上下文 ──
    if bool(get_config("memory_enabled", True)) and (text or user_content):
        try:
            recalled = MemoryStore.recall(
                build_namespaces(user_id, group_id), text or user_content
            )
            if recalled:
                memory_block = ("【相关长期记忆】（以下是你此前记住的关于该用户/群的信息，"
                                "可自然引用，语气不要像背档案，无关就忽略）\n"
                                + "\n".join(f"- {m['content']}" for m in recalled))
                dynamic_context = f"{dynamic_context}\n\n{memory_block}" if dynamic_context else memory_block
        except Exception as e:
            logger.warning(f"[miku_ai] 长期记忆召回失败: {e}")

    # ── 联网搜索：AI自主判断优先（带上下文能理解指代），判断失败退回关键词（仅文本消息，不落历史） ──
    if not processed_image_urls and bool(get_config("web_search_enabled", True)):
        search_query = ""
        if bool(get_config("web_search_ai_judge", True)):
            need, q = await _ai_search_decision(history, text)
            if need:
                search_query = re.sub(r"\s+", " ", (q or (text or "").strip()))[:60]
            elif need is None:
                if _needs_web_search(text):
                    search_query = re.sub(r"\s+", " ", (text or "").strip())[:60]
        elif _needs_web_search(text):
            search_query = re.sub(r"\s+", " ", (text or "").strip())[:60]
        if search_query:
            search_block = await _web_search(search_query)
            if search_block:
                dynamic_context = f"{dynamic_context}\n\n{search_block}" if dynamic_context else search_block

    # ── 敏感词提示注入 dynamic_context 末尾（位于消息数组尾部，不影响 system 前缀缓存） ──
    if sensitive_hint:
        dynamic_context = f"{dynamic_context}\n\n{sensitive_hint}" if dynamic_context else sensitive_hint

    # 构建上下文消息
    context_messages = get_history_manager().get_context_messages(
        session_id, is_group=is_group, system_prompt=system_prompt,
        bot_nickname=bot_nickname, exclude_message_id=str(message_id or "")
    )

    # ── 用户消息过长自动截断（在构建context之前） ──
    max_user_chars = int(get_config("max_user_msg_chars", 1000) or 1000)
    if len(user_content) > max_user_chars:
        logger.warning(f"[miku_ai] 用户消息过长({len(user_content)}字符)，截断至{max_user_chars}")
        user_content = user_content[:max_user_chars] + "...（消息过长已截断）"

    # 添加动态上下文到用户消息
    if processed_image_urls and vision_enabled:
        text_content = user_content
        if dynamic_context:
            text_content = f"{dynamic_context}\n{user_content}"
        text_part = [{"type": "text", "text": text_content}]
        # QQ 图片直链带防盗链（需 Referer）且很快失效，直传 URL 会导致 API 报
        # 1210「图片输入格式/解析错误」，必须下载后转 base64 data URI 直传。
        # 历史记录仍保存原始 URL（见下方 append_message），避免 base64 撑爆历史文件
        image_parts = []
        for idx, img_url in enumerate(processed_image_urls):
            local_file = image_files[idx] if idx < len(image_files) else ""
            data_uri = await url_to_data_uri(img_url, local_file)
            if data_uri:
                image_parts.append({"type": "image_url", "image_url": {"url": data_uri}})
            else:
                logger.warning(f"[miku_ai] 图片转base64失败，跳过该图: {img_url[:80]}")
        if image_parts:
            context_messages.append({"role": "user", "content": text_part + image_parts})
        else:
            context_messages.append({"role": "user", "content": text_content})
    else:
        # 将元信息（群聊/情绪）作为前缀注入，历史对话已在结构化消息中
        if dynamic_context:
            full_user_content = f"{dynamic_context}\n{user_content}"
        else:
            full_user_content = user_content
        context_messages.append({"role": "user", "content": full_user_content})

    # ── 上下文 token 预估截断，从旧到新逐条丢弃 ──
    max_context_chars = int(get_config("max_context_chars", 5000) or 5000)

    def _msg_chars(m: dict) -> int:
        c = m.get("content", "")
        if isinstance(c, str):
            return len(c)
        if isinstance(c, list):
            total = 0
            for p in c:
                if isinstance(p, dict):
                    if p.get("type") == "image_url":
                        # base64 图片按固定开销估算，避免真实长度（百万级字符）撑爆截断逻辑
                        total += 500
                    else:
                        total += len(str(p.get("text", "")))
                else:
                    total += len(str(p))
            return total
        return 500

    total_chars = sum(_msg_chars(m) for m in context_messages)
    if total_chars > max_context_chars:
        # 保留 system(第一条)，从第二条开始逐条丢弃最旧的
        system_msg = context_messages[0] if context_messages else None
        history = context_messages[1:] if system_msg else list(context_messages)
        while history and sum(_msg_chars(m) for m in ([system_msg] if system_msg else []) + history) > max_context_chars:
            dropped = history.pop(0)
            logger.debug(f"[miku_ai] 丢弃旧消息: {str(dropped.get('content', ''))[:50]}...")
        context_messages = ([system_msg] if system_msg else []) + history
        logger.warning(f"[miku_ai] 上下文截断: {total_chars}→{sum(_msg_chars(m) for m in context_messages)}字符, 保留{len(context_messages)}条")

    temperature = float(get_config("temperature", 1.0) or 1.0)
    max_tokens = int(get_config("max_tokens", 0) or 0)

    # 纯文本启用思考模式，需要足够 token（模型选择由多平台故障转移按内容自动决定）
    if not processed_image_urls:
        text_max = int(get_config("text_max_tokens", 0) or 0)
        if text_max:
            max_tokens = text_max

    reply = await _call_ai_api(
        context_messages,
        temperature=temperature,
        max_tokens=max_tokens,
    )

    # 去除思考块
    reply = _strip_think_blocks(reply)

    # 解析响应标记
    markers = _parse_line_markers(reply)
    if markers.get("emotion_name"):
        humanize.emotion_agent.set_emotion(session_id, markers["emotion_name"])

    # 处理表情包
    sticker = await humanize.emoji_agent.process_sticker_response(reply)
    if sticker.success and sticker.emoji_path:
        reply = sticker.cleaned_text

    # 保存历史（用户消息带 message_id：与被动记录的原始消息 upsert 合并，写入识图增强内容）
    get_history_manager().append_message(
        session_id, "user", user_content,
        user_id=user_id, user_name=user_name,
        images=processed_image_urls if processed_image_urls else None,
        is_group=is_group, message_id=str(message_id or ""),
    )
    get_history_manager().append_message(
        session_id, "assistant", reply,
        is_group=is_group,
    )

    # ── AI 自动提炼长期记忆（混合模式：按轮次触发后台提炼，不阻塞回复） ──
    if bool(get_config("memory_enabled", True)) and bool(get_config("memory_auto_extract", True)):
        _maybe_schedule_memory_extract(session_id, user_id, user_name, group_id)

    # 好感度
    favor_info = _maybe_update_favor(user_id, text, reply)

    extra = {
        "favor_changed": favor_info.get("changed", False),
        "favor_delta": favor_info.get("delta", 0.0),
        "favor_new": favor_info.get("new", 0.0),
        "emoji_path": sticker.emoji_path if sticker.success else None,
    }

    return reply, extra


def _maybe_schedule_memory_extract(session_id: str, user_id: str,
                                   user_name: str, group_id: str):
    """按会话计数对话轮数，每 N 轮安排一次后台记忆提炼"""
    _extract_rounds[session_id] = _extract_rounds.get(session_id, 0) + 1
    every = max(3, int(get_config("memory_extract_every", 10) or 10))
    if _extract_rounds[session_id] % every != 0:
        return
    task = asyncio.create_task(_extract_memories(session_id, user_id, user_name, group_id))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    logger.info(f"[miku_ai] 已安排后台记忆提炼（第 {_extract_rounds[session_id]} 轮触发）")


async def _extract_memories(session_id: str, user_id: str,
                            user_name: str, group_id: str):
    """
    轻量 LLM 调用：从最近对话中提炼值得长期记住的用户事实，写入个人画像命名空间。
    失败静默（仅告警），绝不影响聊天主流程。
    """
    try:
        recent = get_history_manager().get_raw_messages(session_id, limit=24)
        dialog_lines = []
        for m in recent:
            content = m.get("content", "")
            if isinstance(content, list):
                content = " ".join(
                    p.get("text", "") for p in content
                    if isinstance(p, dict) and p.get("type") == "text"
                ).strip()
            if not isinstance(content, str) or not content:
                continue
            who = m.get("user_name") or (user_name if m.get("role") == "user" else "Miku")
            dialog_lines.append(f"{who}: {content[:200]}")
        dialog = "\n".join(dialog_lines)
        if len(dialog) < 80:
            return
        prompt = (
            "以下是一段与机器人 Miku 的群聊/私聊对话记录。请从中提炼值得长期记住的"
            "「用户事实」（如：喜欢的歌手/游戏/动画、个人背景、明确表达的喜好与厌恶、"
            "重要计划），不要提炼问候闲聊、表情包、正在讨论的临时话题。\n"
            "只输出 JSON 字符串数组，每条为一句话事实、以「用户」开头，最多 5 条；"
            "没有值得记的就输出 []。不要输出其他任何内容。\n"
            "示例输出：[\"用户喜欢初音未来\", \"用户在准备考研\"]\n\n"
            f"对话记录：\n{dialog[-3000:]}"
        )
        resp = await _call_ai_api(
            [{"role": "user", "content": prompt}],
            temperature=0.1, max_tokens=300, request_timeout=30, thinking=False,
        )
        resp = resp.strip()
        m = re.search(r"\[.*\]", resp, re.DOTALL)
        if not m:
            return
        facts = json.loads(m.group(0))
        if not isinstance(facts, list):
            return
        ns = build_namespaces(user_id, group_id)[0] if user_id else ""
        if not ns:
            return
        saved = 0
        for fact in facts[:8]:
            if isinstance(fact, str) and fact.strip():
                MemoryStore.remember(ns, fact.strip()[:120], source="ai")
                saved += 1
        if saved:
            logger.info(f"[miku_ai] 自动提炼记忆 {saved} 条 → {ns}")
    except Exception as e:
        logger.warning(f"[miku_ai] 自动提炼记忆失败: {e}")


def _maybe_update_favor(user_id: str, user_text: str, reply_text: str) -> Dict:
    result = {"changed": False, "delta": 0.0, "new": 0.0}
    prob = int(get_config("favor_change_probability", 10) or 10)
    if prob <= 0 or random.randint(1, 100) > prob:
        result["new"] = get_history_manager().get_favor(user_id)
        return result

    increase_min = float(get_config("favor_increase_min", 0.05) or 0.05)
    increase_max = float(get_config("favor_increase_max", 0.30) or 0.30)
    decrease_min = float(get_config("favor_decrease_min", 0.02) or 0.02)
    decrease_max = float(get_config("favor_decrease_max", 0.15) or 0.15)

    text_len = len(user_text)
    if text_len >= 10:
        delta = round(random.uniform(increase_min, increase_max), 2)
    elif text_len <= 2:
        delta = -round(random.uniform(decrease_min, decrease_max), 2)
    else:
        if random.random() > 0.5:
            delta = round(random.uniform(increase_min, increase_max), 2)
        else:
            delta = -round(random.uniform(decrease_min, decrease_max), 2)

    new_val = get_history_manager().add_favor(user_id, delta)
    result["changed"] = True
    result["delta"] = delta
    result["new"] = new_val
    return result


async def save_history_async():
    await get_history_manager().save()
