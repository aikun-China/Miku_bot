"""
规划器 - 从leekchat适配
"""

import json
from dataclasses import dataclass

from nonebot.log import logger

from ..config import get_config


@dataclass
class PlannerResult:
    action: str
    reason: str
    wait_ms: int | None = None


class ActionPlanner:
    def __init__(self) -> None:
        pass

    async def plan(
        self,
        session_id: str,
        bot_nickname: str,
        chat_history: list,
        merged_content: str,
        is_idle_debug: bool = False,
    ) -> PlannerResult:
        enabled = bool(get_config("planner_enabled", False))
        if not enabled:
            return PlannerResult(action="reply", reason="planner disabled")

        history_lines = []
        for msg in chat_history[-20:]:
            name = msg.get("user_name", "") if msg.get("role") != "assistant" else bot_nickname
            history_lines.append(f"{name}: {msg.get('content', '')}")
        history_text = "\n".join(history_lines) or "(no history)"

        prompt = (
            "You are a planner deciding whether the bot should reply in a chat. "
            'Output JSON only: {"action": "reply"|"wait"|"complete", '
            '"reason": "<short>", "waitMs": <int|null>}.\n\n'
            f"Bot nickname: {bot_nickname}\n"
            f"Recent chat:\n{history_text}\n\n"
            f"Latest content (idle debug={is_idle_debug}):\n{merged_content}\n"
        )
        try:
            from ..data_source import _call_ai_api
            messages = [{"role": "user", "content": prompt}]
            resp_text = await _call_ai_api(messages, temperature=0.2, max_tokens=200)
            data = json.loads((resp_text or "{}").strip())
            action = str(data.get("action", "reply")).lower()
            if action not in {"reply", "wait", "complete"}:
                action = "reply"
            return PlannerResult(
                action=action,
                reason=str(data.get("reason", "")),
                wait_ms=int(data["waitMs"]) if data.get("waitMs") else None,
            )
        except Exception as e:
            logger.warning(f"[ActionPlanner] plan failed: {e}")
            return PlannerResult(action="reply", reason="planner failed, default reply")
