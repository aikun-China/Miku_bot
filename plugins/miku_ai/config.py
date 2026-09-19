"""
Miku AI 插件配置模块
"""

from pathlib import Path
from typing import Any, Dict, Optional

from utils.config_manager import config_manager

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
    "  # 接入方式：cloud（云端）/ local（本地 Ollama 等 OpenAI 兼容服务）\n"
    "  # 提示：如果不配置 cloud_api_key，会自动尝试使用本地模式\n"
    "  api_mode: cloud\n"
    "\n"
    "  # ── 云端 AI 配置 ──\n"
    "  # API Key（选填，不填则自动切换到本地模式）\n"
    "  # 智谱AI: https://open.bigmodel.cn/（glm-4.6v 支持文本+图片）\n"
    "  cloud_api_key: ''\n"
    "  # API Base URL（OpenAI 兼容接口）\n"
    "  #   智谱AI:   https://open.bigmodel.cn/api/paas/v4\n"
    "  #   DeepSeek: https://api.deepseek.com/v1\n"
    "  #   通义千问:  https://dashscope.aliyuncs.com/compatible-mode/v1\n"
    "  #   OpenAI:   https://api.openai.com/v1\n"
    "  cloud_base_url: https://open.bigmodel.cn/api/paas/v4\n"
    "  # 识图/多模态模型名（glm-4.6v 支持文本+图片）\n"
    "  cloud_model: glm-4.6v\n"
    "  # 纯文本模型名（不用深度思考，响应更快更短）\n"
    "  cloud_text_model: glm-4.5-air\n"
    "  # 文本模型最大输出 token（比多模态模型小很多）\n"
    "  text_max_tokens: 2048\n"
    "\n"
    "  # ── 本地 AI 配置（Ollama / LM Studio 等 OpenAI 兼容服务） ──\n"
    "  # 本地 API Base URL（例如 Ollama 默认 http://localhost:11434/v1）\n"
    "  local_base_url: http://localhost:11434/v1\n"
    "  # 本地模型名（例如 qwen2.5:7b / llama3:8b / mistral:latest）\n"
    "  local_model: qwen2.5:7b\n"
    "  # 本地 API Key（通常 Ollama 不需要，留空即可）\n"
    "  local_api_key: ''\n"
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
    "  # 聊天记录持久化文件路径（相对项目根目录）\n"
    "  history_file: data/ai_chat_history.json\n"
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
    "  # ── 特殊人物识别 ──\n"
    "  # 通过 QQ 号和昵称识别特定的人，给 AI 加上身份设定\n"
    "  # 每个条目包含：qq号列表、昵称关键词、身份描述、称呼方式、语气调整\n"
    "  special_users: []\n"
    "\n"
    "  # ── 高级参数 ──\n"
    "  # 采样温度（0-2，值越高越发散，默认 1.0）\n"
    "  temperature: 1.0\n"
    "  # 单次回复最大 token 数（留空=不限）\n"
    "  max_tokens: 800\n"
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
    "  # 记忆配置\n"
    "  memory_enabled: true\n"
    "  memory_group_history_limit: 800\n"
    "  memory_user_history_limit: 100\n"
    "  # 话题配置\n"
    "  topic_enabled: true\n"
  "  topic_window_hours: 5\n"
    "  topic_history_window_count: 3\n"
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
        "api_mode": "cloud",
        "cloud_api_key": "",
        "cloud_base_url": "https://open.bigmodel.cn/api/paas/v4",
        "cloud_model": "glm-4.6v",
        "cloud_text_model": "glm-4.5-air",
        "text_max_tokens": 2048,
        "local_base_url": "http://localhost:11434/v1",
        "local_model": "qwen2.5:7b",
        "local_api_key": "",
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
        "history_dir": "data/ai_chat_history",
        "history_file": "data/ai_chat_history.json",
        "favor_change_probability": 10,
        "favor_increase_min": 0.05,
        "favor_increase_max": 0.30,
        "favor_decrease_min": 0.02,
        "favor_decrease_max": 0.15,
        "show_favor_change": True,
        "favor_affect_reply": True,
        "personality_file": "plugins/miku_ai/personality.txt",
        "personality_reload": False,
        "special_users": [],
        "temperature": 1.0,
        "max_tokens": 800,
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
        "memory_group_history_limit": 800,
        "memory_user_history_limit": 100,
        "topic_enabled": True,
        "topic_window_hours": 5,
        "topic_history_window_count": 3,
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
