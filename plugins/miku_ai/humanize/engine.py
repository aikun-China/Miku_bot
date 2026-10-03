"""
Humanize 引擎 - 从leekchat适配
整合情感、表情、规划器、记忆、话题等模块
"""

from .emoji import EmojiAgent
from .emotion import EmotionAgent
from .expression import ExpressionLearner
from .planner import ActionPlanner


class HumanizeEngine:
    def __init__(self) -> None:
        self.action_planner = ActionPlanner()
        self.emotion_agent = EmotionAgent()
        self.emoji_agent = EmojiAgent()
        self.expression_learner = ExpressionLearner()

    async def init(self) -> None:
        return None
