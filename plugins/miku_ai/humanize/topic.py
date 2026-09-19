"""
话题系统 - 从leekchat适配（简化版）
"""


class TopicTracker:
    """话题追踪功能"""

    def __init__(self) -> None:
        pass

    def get_topic_context(self, *_args, **_kwargs) -> str:
        return ""

    async def on_message(self, *_args, **_kwargs) -> None:
        return None
