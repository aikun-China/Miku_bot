"""
Miku AI 插件配置模块
"""

import time

from pathlib import Path
from typing import Any, Dict, List, Optional

from utils.config_manager import config_manager
from utils.ai_model_config import LEGACY_AI_CONFIG_MIGRATED, get_ai_model_config

if LEGACY_AI_CONFIG_MIGRATED:
    config_manager.reload()

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)

PROJECT_ROOT = BASE_DIR.parent.parent

BALANCE_CACHE_DIR = PROJECT_ROOT / "data" / "api_balance_cache"
BALANCE_CACHE_DIR.mkdir(parents=True, exist_ok=True)

_AI_TEMPLATE = (
    "\n"
    "miku_ai:\n"
    "  # 是否启用 AI 功能（true/false）\n"
    "  enabled: true\n"
    "  # 响应风格：card（图片卡片）/ text（纯文本）\n"
    "  response_style: card\n"
    "\n"
    "  # 文本模型最大输出 token（推理模型需给 reasoning_content 留余量）\n"
    "  text_max_tokens: 8192\n"
    "\n"
    "  # ── 识图配置 ──\n"
    "  # 识图并发数\n"
    "  vision_concurrency: 2\n"
    "\n"
    "  # ── 触发配置 ──\n"
    "  # 群聊 @Bot 时是否自动回复（true/false）\n"
    "  group_reply_on_mention: true\n"
    "  # 单聊是否每条都回复（true/false）\n"
    "  private_reply_every: true\n"
    "\n"
    "  # ── 聊天记录配置 ──\n"
    "  # 群聊最多保存的历史消息数（每条群聊独立保存）\n"
    "  group_history_max: 1000\n"
    "  # 单聊最多保存的历史消息数（每条私聊独立保存）\n"
    "  private_history_max: 500\n"
    "  # 群聊 AI 上下文条数（发给模型的消息数，避免 token 超限）\n"
    "  group_context_max: 25\n"
    "  # 单聊 AI 上下文条数\n"
    "  private_context_max: 30\n"
    "  # AI 单次请求最大上下文字符数（约1字符=1.5token，防止输入超限）\n"
    "  max_context_chars: 8000\n"
    "  # 单次请求用户消息最大字符数（超过自动截断）\n"
    "  max_user_msg_chars: 1000\n"
    "  # 引用消息最大字符数（单独限制，防止引用长文炸token）\n"
    "  max_quoted_chars: 300\n"
    "  # 群聊消息处理队列深度上限（同会话最多排队条数）\n"
    "  group_max_queue: 3\n"
    "  # 私聊消息处理队列深度上限\n"
    "  private_max_queue: 5\n"
    "  # 聊天记录数据库路径（SQLite，相对项目根目录）\n"
    "  history_db_path: data/ai_chat_history.db\n"
    "  # 上下文时间窗口（分钟，0=禁用）：只把最近N分钟内的历史消息发给AI，省token且不接隔夜旧话题\n"
    "  context_time_window_minutes: 0\n"
    "  # AI API 最大并发数（多群同时触发时限流排队，防低配机过载与API限流）\n"
    "  api_max_concurrency: 2\n"
    "\n"
    "  # ── 好感度系统 ──\n"
    "  # 与 AI 聊天时，好感度变化的触发概率（0-100，例如 5 = 5%）\n"
    "  # 触发后：好感度变化 = 根据聊天内容质量决定增减\n"
    "  favor_change_probability: 10\n"
    "  # 好感度增加范围（最小值-最大值，随机浮点数，精度 2 位）\n"
    "  favor_increase_min: 0.05\n"
    "  favor_increase_max: 0.30\n"
    "  # 好感度减少范围（最小值-最大值，随机浮点数，精度 2 位）\n"
    "  favor_decrease_min: 0.02\n"
    "  favor_decrease_max: 0.15\n"
    "  # 是否显示好感度变化提示（true/false）\n"
    "  show_favor_change: true\n"
    "  # 是否让AI根据好感度调整语气（true/false）\n"
    "  favor_affect_reply: true\n"
    "\n"
    "  # ── 人格提示词 ──\n"
    "  # 提示词文件路径（相对项目根目录，或留空使用默认：plugins/miku_ai/personality.txt）\n"
    "  personality_file: plugins/miku_ai/personality.txt\n"
    "  # 是否在每次回复前重新读取提示词（方便调试，true=热更新 / false=只加载一次）\n"
    "  personality_reload: false\n"
    "\n"
    "  # ── 联网搜索 ──\n"
    "  # 是否启用：命中关键词（天气/新闻/最新/搜一下等）时自动搜索并注入结果\n"
    "  web_search_enabled: true\n"
    "  # AI自主判断是否需要搜索（true=带上下文智能判断，能理解指代；false=仅关键词触发）\n"
    "  web_search_ai_judge: true\n"
    "  # 搜索结果条数上限\n"
    "  web_search_max_results: 5\n"
    "  # 单个搜索引擎超时（秒）\n"
    "  web_search_timeout: 8\n"
    "\n"
    "  # ── 特殊人物识别 ──\n"
    "  # 通过 QQ 号和昵称识别特定的人，给 AI 加上身份设定\n"
    "  # 每个条目包含：qq号列表、昵称关键词、身份描述、称呼方式、语气调整\n"
    "  special_users:\n"
    "    - qq:\n"
    "        - '761695424'\n"
    "      nicknames:\n"
    "        - 愛君\n"
    "        - 愛君_aikun\n"
    "        - aikun\n"
    "      identity: 我的开发者兼主人，愛君_aikun\n"
    "      description: 是个即将进入高三的苦逼高中生，天天作业考试一大堆，还抽时间写代码，很厉害的。\n"
    "      address_as: ご主人様 / 狗修金 / 愛君酱\n"
    "      tone: 稍微乖巧一点，带点女仆感但不要太夸张，保持开朗可爱，偶尔可以傲娇一下\n"
    "\n"
    "  # ── 高级参数 ──\n"
    "  # 采样温度（0-2，值越高越发散，默认 1.0）\n"
    "  temperature: 1.0\n"
    "  # 单次回复最大 token 数（留空=不限；推理模型建议 8192）\n"
    "  max_tokens: 8192\n"
    "  # 请求超时时间（秒）\n"
    "  request_timeout: 30\n"
    "  # 是否启用识图功能（需要模型支持 vision）\n"
    "  vision_enabled: true\n"
    "  # 图片卡片宽度\n"
    "  card_width: 600\n"
    "  # 图片卡片高度（0=自适应）\n"
    "  card_height: 0\n"
    "\n"
    "  # ── 表情包库与智能识图（节省 token 成本） ──\n"
    "  # 表情包哈希去重窗口（小时），内存缓存最近 N 小时的查询结果\n"
    "  emoji_hash_window_hours: 24\n"
    "  # 小图跳过阈值（KB），小于此大小的图片直接当表情包跳过识图\n"
    "  emoji_skip_size_kb: 20\n"
    "  # 小尺寸跳过阈值（像素），宽高都小于此值的图片跳过识图\n"
    "  emoji_skip_dimension: 200\n"
    "  # 小 GIF 跳过阈值（KB），GIF 且小于此大小时跳过识图\n"
    "  emoji_gif_skip_size_kb: 50\n"
    "  # 刷屏保护：同用户 N 秒内最多识别 M 张图，其余跳过\n"
    "  emoji_spam_max_count: 3\n"
    "  emoji_spam_window_seconds: 60\n"
    "  # 表情包库路径（相对路径或绝对路径）\n"
    "  emoji_library_path: data/emoji_library.db\n"
    "  # 是否自动清理冷门表情包\n"
    "  emoji_auto_cleanup: true\n"
    "  # 清理阈值：total_uses=1 且 first_seen 超过 N 天的记录将被清理\n"
    "  emoji_cleanup_days: 90\n"
    "  # 图片链接默认过期天数（QQ 图片链接到期后用结构化描述代替）\n"
    "  image_url_default_ttl_days: 7\n"
    "  # 链接过期后是否用识别描述代替（true=显示描述，false=只显示图片已过期）\n"
    "  image_expiry_use_description: true\n"
    "  # 识图 prompt 模板（要求返回 JSON 格式）\n"
    "  vision_prompt_template: '分析这张图片，按JSON返回：{\"is_emoji\":true/false,\"ocr_text\":\"图上文字\",\"character\":\"角色名\",\"emotion_tag\":\"嘲讽/震惊/可爱/无语/悲伤/兴奋/其他\",\"description\":\"一句话描述\",\"tags\":[\"标签1\",\"标签2\"]}'\n"
    "\n"
    "  # ── leekchat 适配配置 ──\n"
    "  # 机器人昵称（逗号分隔，用于leekchat风格识别）\n"
    "  nicknames: 初音,Miku,初音未来\n"
    "  # 是否启用流式输出\n"
    "  stream: false\n"
    "  # 回复后冷却时间（毫秒）\n"
    "  cooldown_after_reply_ms: 5000\n"
    "  # 是否启用打字延迟模拟\n"
    "  enable_typing_delay: false\n"
    "  # 打字延迟最大总时长（毫秒）\n"
    "  typing_delay_max_total_ms: 10000\n"
    "  # 是否启用 Markdown 长消息自动截图\n"
    "  enable_markdown_screenshot: false\n"
    "  # 是否启用调试模式\n"
    "  debug: false\n"
    "  # 单次回复最大工具调用轮次\n"
    "  max_iterations: 5\n"
    "  # 输出长度约束强度（low/medium/high）\n"
    "  output_length_constraint_strength: medium\n"
    "  # 工具调用约束强度（low/medium/high）\n"
    "  tool_call_constraint_strength: medium\n"
    "  # 表情包使用约束强度（low/medium/high）\n"
    "  emoji_usage_constraint_strength: medium\n"
    "  # 音频使用约束强度（low/medium/high）\n"
    "  audio_usage_constraint_strength: medium\n"
    "  # Markdown 使用约束强度（low/medium/high）\n"
    "  markdown_usage_constraint_strength: medium\n"
    "\n"
    "  # 情感系统配置\n"
    "  emotion_default: default\n"
    "  emotion_update_interval_ms: 3600000\n"
    "  # 回复风格配置\n"
    "  reply_style_base: \"\"\n"
    "  reply_style_multiple: \"\"\n"
    "  reply_style_multiple_probability: 0.2\n"
    "  # 规划器配置\n"
    "  planner_enabled: false\n"
    "  planner_idle_threshold_ms: 1800000\n"
    "  planner_idle_message_count: 100\n"
    "  # 表情包配置\n"
    "  emoji_characters: \"\"\n"
    "  emoji_stickers: \"\"\n"
    "  # 表达学习配置\n"
    "  expression_enabled: false\n"
    "  expression_learn_after_messages: 100\n"
    "  expression_sample_size: 3\n"
    "  # ── 长期记忆系统 ──\n"
    "  # 是否启用长期记忆（回复时召回注入 + AI自动提炼 + 记住/忘记命令）\n"
    "  memory_enabled: true\n"
    "  # 被动记录所有群聊/私聊消息进历史库（AI能得知没@它的群聊内容）\n"
    "  passive_record_enabled: true\n"
    "  # 长期记忆数据库路径（SQLite，相对项目根目录）\n"
    "  memory_db_path: data/ai_memory.db\n"
    "  # 记忆保留天数（0=永久保留）\n"
    "  memory_ttl_days: 0\n"
    "  # 每个命名空间（某人/某群）最多记忆条数，超出淘汰低权重旧记忆\n"
    "  memory_max_per_namespace: 200\n"
    "  # 每次回复最多注入的相关记忆条数\n"
    "  memory_recall_top_k: 4\n"
    "  # 是否让AI每隔N轮对话自动从聊天中提炼记忆\n"
    "  memory_auto_extract: true\n"
    "  memory_extract_every: 10\n"
    "\n"
    "  # ── 敏感词与内容安全 ──\n"
    "  # 是否启用敏感词过滤（true=后端直接拦截，不调用AI）\n"
    "  sensitive_filter_enabled: true\n"
    "  # 自定义敏感词列表（逗号分隔，内置常见侮辱/性骚扰词汇）\n"
    "  sensitive_words_extra: \"\"\n"
    "  # 敏感词触发后的拒绝回复模板（随机抽取）\n"
    "  sensitive_replies:\n"
    "    - \"呜...这种话Miku不喜欢...\"\n"
    "    - \"哎呀，不要说这种奇怪的话啦～\"\n"
    "    - \"Miku假装没听见！换个别的话题吧～\"\n"
    "    - \"哼，Miku才不想理你呢...\"\n"
    "    - \"这种话题不太好哦，我们说点别的吧！\"\n"
    "  # 是否将敏感词检测也交给AI（false=后端拦截，true=后端+AI双重过滤）\n"
    "  ai_content_filter: false\n"
)

_cfg = config_manager.register_plugin(
    "miku_ai",
    defaults={
        "enabled": True,
        "response_style": "card",
        "text_max_tokens": 8192,
        "group_reply_on_mention": True,
        "private_reply_every": True,
        "group_history_max": 1000,
        "private_history_max": 500,
        "group_context_max": 25,
        "private_context_max": 30,
        "max_context_chars": 8000,
        "max_user_msg_chars": 1000,
        "max_quoted_chars": 300,
        "group_max_queue": 3,
        "private_max_queue": 5,
        "history_db_path": "data/ai_chat_history.db",
        "context_time_window_minutes": 0,
        "api_max_concurrency": 2,
        "favor_change_probability": 10,
        "favor_increase_min": 0.05,
        "favor_increase_max": 0.30,
        "favor_decrease_min": 0.02,
        "favor_decrease_max": 0.15,
        "show_favor_change": True,
        "favor_affect_reply": True,
        "personality_file": "plugins/miku_ai/personality.txt",
        "personality_reload": False,
        "web_search_enabled": True,
        "web_search_max_results": 5,
        "web_search_timeout": 8,
        "special_users": [
            {
                "qq": ["761695424"],
                "nicknames": ["愛君", "愛君_aikun", "aikun"],
                "identity": "我的开发者兼主人，愛君_aikun",
                "description": "是个即将进入高三的苦逼高中生，天天作业考试一大堆，还抽时间写代码，很厉害的。",
                "address_as": "ご主人様 / 狗修金 / 愛君酱",
                "tone": "稍微乖巧一点，带点女仆感但不要太夸张，保持开朗可爱，偶尔可以傲娇一下",
            }
        ],
        "temperature": 1.0,
        "max_tokens": 8192,
        "request_timeout": 30,
        "vision_enabled": True,
        "card_width": 600,
        "card_height": 0,
        "balance_cache_minutes": 5,
        "emoji_hash_window_hours": 24,
        "emoji_skip_size_kb": 20,
        "emoji_skip_dimension": 200,
        "emoji_gif_skip_size_kb": 50,
        "emoji_spam_max_count": 3,
        "emoji_spam_window_seconds": 60,
        "emoji_library_path": "data/emoji_library.db",
        "emoji_auto_cleanup": True,
        "emoji_cleanup_days": 90,
        "image_url_default_ttl_days": 7,
        "image_expiry_use_description": True,
        "vision_concurrency": 2,
        "vision_prompt_template": '分析这张图片，按JSON返回：{"is_emoji":true/false,"ocr_text":"图上文字","character":"角色名","emotion_tag":"嘲讽/震惊/可爱/无语/悲伤/兴奋/其他","description":"一句话描述","tags":["标签1","标签2"]}',
        # leekchat 适配配置
        "nicknames": ["初音", "Miku", "初音未来"],
        "stream": False,
        "cooldown_after_reply_ms": 5000,
        "enable_typing_delay": False,
        "typing_delay_max_total_ms": 10000,
        "enable_markdown_screenshot": False,
        "debug": False,
        "max_iterations": 5,
        "output_length_constraint_strength": "medium",
        "tool_call_constraint_strength": "medium",
        "emoji_usage_constraint_strength": "medium",
        "audio_usage_constraint_strength": "medium",
        "markdown_usage_constraint_strength": "medium",
        "emotion_default": "default",
        "emotion_update_interval_ms": 3600000,
        "reply_style_base": "",
        "reply_style_multiple": [],
        "reply_style_multiple_probability": 0.2,
        "planner_enabled": False,
        "planner_idle_threshold_ms": 1800000,
        "planner_idle_message_count": 100,
        "emoji_characters": [],
        "emoji_stickers": [],
        "expression_enabled": False,
        "expression_learn_after_messages": 100,
        "expression_sample_size": 3,
        "memory_enabled": True,
        "passive_record_enabled": True,
        "memory_db_path": "data/ai_memory.db",
        "memory_ttl_days": 0,
        "memory_max_per_namespace": 200,
        "memory_recall_top_k": 4,
        "memory_auto_extract": True,
        "memory_extract_every": 10,
        # 敏感词与内容安全
        "sensitive_filter_enabled": True,
        "sensitive_words_extra": "",
        "sensitive_replies": [
            "呜...这种话Miku不喜欢...",
            "哎呀，不要说这种奇怪的话啦～",
            "Miku假装没听见！换个别的话题吧～",
            "哼，Miku才不想理你呢...",
            "这种话题不太好哦，我们说点别的吧！",
        ],
        "ai_content_filter": False,
    },
    template_str=_AI_TEMPLATE,
    description="AI 聊天插件配置（含余额查询）",
)


def get_config(key: str, default: Any = None) -> Any:
    """动态读取 miku_ai 配置。"""
    live_cfg = config_manager.get_plugin_config("miku_ai")
    if live_cfg and key in live_cfg:
        return live_cfg[key]
    if _cfg and key in _cfg:
        return _cfg[key]
    return default


def is_enabled() -> bool:
    """检查插件是否启用"""
    raw = get_config("enabled", True)
    return str(raw).strip().lower() not in ("false", "0", "no", "")


# ── 多平台故障转移 ──
# 平台失败后的冷却记录（到期时间戳）：冷却期内该平台排到尝试顺序末尾，
# 避免主平台故障时每条消息都先撞一次注定失败的请求
_platform_cooldown: Dict[str, float] = {}
# 冷却时长：限流恢复快（60s），余额不足/鉴权失败/宕机恢复慢（300s）
PLATFORM_COOLDOWN_RATE_LIMIT = 60.0
PLATFORM_COOLDOWN_DEFAULT = 300.0

# 余额不足关键词（大小写不敏感匹配，覆盖常见网关返回；部分网关用 429 返回 insufficient_quota）
_BALANCE_KEYWORDS = (
    "insufficient balance", "insufficient_quota", "insufficient quota",
    "余额不足", "欠费", "账户余额", "balance is insufficient", "arrears",
)
# 鉴权失败关键词
_AUTH_KEYWORDS = (
    "invalid api key", "invalid_api_key", "unauthorized", "authentication",
    "api key not valid", "令牌无效", "鉴权失败", "无权限",
)


def get_ai_platforms() -> List[Dict[str, str]]:
    """读取 AI 平台列表（多平台故障转移用）。
    优先使用 ai_platforms 列表配置；未配置时使用本地 AI 配置作为兜底。
    返回前按冷却状态排序：可用平台在前（保持配置顺序），冷却中的垫底；
    全部处于冷却时仍全部返回（兜底总得试试）。"""
    model_config = get_ai_model_config()
    raw = model_config.get("ai_platforms")
    platforms: List[Dict[str, str]] = []
    if isinstance(raw, list):
        for p in raw:
            if not isinstance(p, dict):
                continue
            base_url = str(p.get("base_url", "") or "").strip().rstrip("/")
            model = str(p.get("model", "") or "").strip()
            if not base_url or not model:
                continue
            platforms.append({
                "name": str(p.get("name", "") or "").strip() or base_url,
                "api_key": str(p.get("api_key", "") or ""),
                "base_url": base_url,
                "model": model,
                "text_model": str(p.get("text_model", "") or "").strip(),
            })
    if not platforms:
        local_url = str(model_config.get("local_base_url", "") or "").rstrip("/")
        local_model = str(model_config.get("local_model", "") or "")
        if local_url and local_model:
            platforms.append({
                "name": "本地",
                "api_key": str(model_config.get("local_api_key", "") or ""),
                "base_url": local_url,
                "model": local_model,
                "text_model": "",
            })
    if not platforms:
        return []
    now = time.time()
    ready = [p for p in platforms if _platform_cooldown.get(p["name"], 0.0) <= now]
    cooling = [p for p in platforms if _platform_cooldown.get(p["name"], 0.0) > now]
    return ready + cooling if ready else cooling


def mark_platform_failed(name: str, cooldown_seconds: float = PLATFORM_COOLDOWN_DEFAULT) -> None:
    """标记平台故障，cooldown_seconds 秒内降低其尝试优先级"""
    _platform_cooldown[name] = time.time() + max(1.0, cooldown_seconds)


def mark_platform_ok(name: str) -> None:
    """平台请求成功，清除冷却状态"""
    _platform_cooldown.pop(name, None)


def get_platform_failover_cooldown(status_code: int, body_text: str) -> float:
    """判断 HTTP 错误是否为平台级故障（应切换平台）。
    返回 0.0 = 请求级错误（换平台也无法解决，不切换）；>0 = 故障转移并冷却的秒数。"""
    lower = (body_text or "").lower()
    # 余额不足/欠费 → 切平台（冷却久，短期内不会恢复）
    if status_code == 402 or any(kw in lower for kw in _BALANCE_KEYWORDS):
        return PLATFORM_COOLDOWN_DEFAULT
    # 鉴权失败：Key 无效/未授权/无权限
    if status_code in (401, 403) or any(kw in lower for kw in _AUTH_KEYWORDS):
        return PLATFORM_COOLDOWN_DEFAULT
    # 限流：内置重试（2s→4s×2）耗尽后仍 429 → 切平台（冷却短，限流恢复快）
    if status_code == 429:
        return PLATFORM_COOLDOWN_RATE_LIMIT
    # 服务端故障
    if status_code >= 500:
        return PLATFORM_COOLDOWN_DEFAULT
    return 0.0
