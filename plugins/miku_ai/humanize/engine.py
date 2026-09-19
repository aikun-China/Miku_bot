"""
Humanize 引擎 - 从leekchat适配
整合情感、表情、规划器、记忆、话题等模块
"""

from .emoji import EmojiAgent
from .emotion import EmotionAgent
from .expression import ExpressionLearner
from .memory import MemoryRetrieval
from .planner import ActionPlanner
from .topic import TopicTracker


class HumanizeEngine:
    def __init__(self) -> None:
        self.memory_retrieval = MemoryRetrieval()
        self.topic_tracker = TopicTracker()
        self.action_planner = ActionPlanner()
        self.emotion_agent = EmotionAgent()
        self.emoji_agent = EmojiAgent()
        self.expression_learner = ExpressionLearner()

    async def init(self) -> None:
        return None
