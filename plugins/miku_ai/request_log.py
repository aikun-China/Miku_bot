"""AI 请求日志：记录 用户QQ / 发送内容 / thinking / 输出 / 耗时。

日志目录：<项目根>/logs/ai_requests/ai_YYYY-MM-DD.log
格式：
2026-10-31 21:30:00 用户(761695424)：晚上好
thinking: （无）
输出: 晚上好呀～
time: 2.31s | platform=本地Qwen3-VL model=qwen3-vl:2b-instruct-gpu24
"""

import re
import time
from contextvars import ContextVar
from pathlib import Path

from nonebot.log import logger

from .config import PROJECT_ROOT

LOG_DIR = Path(PROJECT_ROOT) / "logs" / "ai_requests"
_THINK_RE = re.compile(r"<think(?:ing)?[^>]*>([\s\S]*?)</think(?:ing)?>", re.IGNORECASE)

# 每个请求（asyncio Task）独立的思考内容，避免并发请求串台
_last_thinking_var: ContextVar[str] = ContextVar("miku_ai_last_thinking", default="")


def set_last_thinking(text: str) -> None:
    _last_thinking_var.set(str(text or "").strip())


def get_last_thinking(raw_reply: str = "") -> str:
    """优先取 API 返回的思考字段；没有则尝试从 <think> 块里提取。"""
    thinking = _last_thinking_var.get()
    if thinking:
        return thinking
    if raw_reply:
        matched = _THINK_RE.search(raw_reply)
        if matched:
            return matched.group(1).strip()
    return ""


def _timestamp() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def log_ai_request(*, user_id: str, user_text: str, thinking: str,
                   output: str, elapsed: float,
                   platform: str = "", model: str = "") -> None:
    """把一次 AI 请求追加写入按天切分的日志文件；任何异常都只告警不影响回复。"""
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        fname = LOG_DIR / f"ai_{time.strftime('%Y-%m-%d')}.log"

        text = (user_text or "").strip() or "[图片/空消息]"
        thinking = (thinking or "").strip()
        output = (output or "").strip()

        meta = ""
        if platform or model:
            meta = f" | platform={platform} model={model}"

        block = "\n".join([
            f"{_timestamp()} 用户({user_id})：{text}",
            f"thinking: {thinking}" if thinking else "thinking: （无）",
            f"输出: {output}",
            f"time: {elapsed:.2f}s{meta}",
            "",
        ])
        with fname.open("a", encoding="utf-8") as fp:
            fp.write(block + "\n")
    except Exception as exc:  # pragma: no cover - 日志失败绝不影响主流程
        logger.warning(f"[miku_ai] 写请求日志失败: {exc}")
