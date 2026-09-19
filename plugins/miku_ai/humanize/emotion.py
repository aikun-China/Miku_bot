"""
情感系统 - 从leekchat适配
"""

import re
import time
from dataclasses import dataclass

from nonebot.log import logger

from ..config import get_config

_EMOTION_RE = re.compile(r"\[emotion:([^\]]+)\]", re.IGNORECASE)
_EMOTION_TAG_RE = re.compile(r"\[emotion:[^\]]+\]", re.IGNORECASE)


@dataclass
class EmotionState:
    current: str
    updated_at: int


class EmotionAgent:
    def __init__(self) -> None:
        self._state: dict[str, EmotionState] = {}

    def _default_emotion(self) -> str:
        return str(get_config("emotion_default", "default") or "default")

    def get_emotion(self, session_id: str) -> str | None:
        st = self._state.get(session_id)
        return st.current if st else None

    def set_emotion(self, session_id: str, emotion: str) -> None:
        self._state[session_id] = EmotionState(current=emotion, updated_at=int(time.time() * 1000))

    def parse_emotion_intent(self, text: str) -> str | None:
        match = _EMOTION_RE.search(text or "")
        return match.group(1).strip() if match else None

    def clean_emotion_markers(self, text: str) -> str:
        return _EMOTION_TAG_RE.sub("", text or "").strip()

    async def refresh_if_needed(self, session_id: str, bot_nickname: str, chat_history: list, target_message) -> EmotionState:
        interval_ms = int(get_config("emotion_update_interval_ms", 3600000) or 3600000)
        now = int(time.time() * 1000)
        existing = self._state.get(session_id)
        if existing and now - existing.updated_at < interval_ms:
            return existing

        current = await self._decide_emotion(bot_nickname, chat_history, target_message)
        state = EmotionState(current=current, updated_at=now)
        self._state[session_id] = state
        return state

    async def _decide_emotion(self, bot_nickname: str, chat_history: list, target_message) -> str:
        emotions = {}  # 简化：暂时不支持自定义情绪配置
        if not emotions:
            return self._default_emotion()

        history_lines = []
        for msg in chat_history[-10:]:
            role = msg.get("role", "user")
            name = msg.get("user_name", "") if role != "assistant" else bot_nickname
            history_lines.append(f"{name}: {msg.get('content', '')}")
        history_text = "\n".join(history_lines) or "(no recent history)"

        target_text = target_message.get("content", "") if isinstance(target_message, dict) else ""
        target_name = target_message.get("user_name", "") if isinstance(target_message, dict) else ""

        emotion_names = ", ".join(emotions.keys())
        prompt = (
            f"Based on the chat history and the latest message from {target_name}, "
            f"pick the most fitting emotion for {bot_nickname} to use in the reply.\n"
            f"Available emotions: {emotion_names}.\n"
            f"Reply with ONLY the emotion name, nothing else.\n\n"
            f"--- HISTORY ---\n{history_text}\n--- TARGET ---\n{target_text}"
        )
        try:
            from ..data_source import _call_ai_api
            model = str(get_config("cloud_model", "") or get_config("local_model", ""))
            messages = [{"role": "user", "content": prompt}]
            resp_text = await _call_ai_api(messages, temperature=0.2, max_tokens=20)
            picked = resp_text.strip().split()[0:1]
            if picked:
                name = picked[0].lower()
                if name in emotions:
                    return name
        except Exception as e:
            logger.warning(f"[EmotionAgent] decide failed: {e}")
        return self._default_emotion()
