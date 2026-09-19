"""
Miku 拍一拍回复插件
===================
- 监听 OneBot V11 的 notice.notify.poke 事件
- 当用户在群聊或私聊中拍一拍 Bot 时，随机回复一条消息
- 数据来源：utils/anime_quotes.py 的随机语录
- 支持配置：启用开关、回复风格（文字/图片卡片）
"""

from nonebot import on_notice, get_driver
from nonebot.adapters.onebot.v11 import (
    Bot, MessageEvent, MessageSegment, PokeNotifyEvent, GroupMessageEvent, PrivateMessageEvent
)
from nonebot.log import logger
from nonebot.exception import FinishedException
from pathlib import Path
import sys
import random

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from utils.config_manager import config_manager
from utils.anime_quotes import random_quote


try:
    from plugins.miku_stats import record_plugin_usage
except ImportError:
    try:
        from utils.plugin_stats import record_plugin_usage
    except ImportError:
        record_plugin_usage = lambda *args, **kwargs: None


try:
    from plugins.miku_menu import register_plugin_info
except ImportError:
    register_plugin_info = lambda *args, **kwargs: None


_POKE_TEMPLATE = (
    "\n"
    "miku_poke:\n"
    "  # 是否启用拍一拍回复\n"
    "  enabled: true\n"
    "  # 回复风格：text（纯文本）/ card（图片卡片）\n"
    "  response_style: text\n"
)

_cfg = config_manager.register_plugin(
    "miku_poke",
    defaults={
        "enabled": True,
        "response_style": "text",
    },
    template_str=_POKE_TEMPLATE,
    description="拍一拍回复插件配置",
)


def _is_enabled() -> bool:
    raw = config_manager.get("miku_poke", "enabled", True)
    return str(raw).strip().lower() not in ("false", "0", "no", "")


# 拍一拍随机回复语料库（初音未来口吻）
_POKE_REPLIES = [
    "唔……被拍到了啦~",
    "诶嘿，是在叫我吗？",
    "拍一拍的话，我会害羞的啦~",
    "喵？（假装自己是猫）",
    "再拍就要唱歌给你听了哦~",
    "好痒好痒，别拍啦~",
    "嘿嘿，感受到你的存在了呢~",
    "拍一拍，能量+1！",
    "嘛嘛嘛，想聊天直接说嘛~",
    "被拍醒了呢，早安~",
    "拍一拍是召唤Miku的仪式吗？",
    "接收到拍一拍信号，Miku待机中~",
    "别拍啦，再拍就要脸红了……",
    "拍一拍已读，正在思考回复……",
    "这是新型的打招呼方式吗？学会了！",
]


poke_handler = on_notice(priority=5, block=False)


@poke_handler.handle()
async def _(bot: Bot, event: PokeNotifyEvent):
    if not _is_enabled():
        return

    # 只响应拍一拍 Bot 自己的事件
    # PokeNotifyEvent 中：group_id 存在表示群聊，user_id 是拍的人，target_id 是被拍的人
    target_id = getattr(event, "target_id", None)
    if target_id is None:
        return
    if str(target_id) != str(bot.self_id):
        return

    # 随机选择一条回复
    text = random.choice(_POKE_REPLIES)

    # 10% 概率使用随机动漫语录（如果有）
    if random.random() < 0.1:
        try:
            quote, _ = random_quote()
            if quote:
                text = quote
        except Exception:
            pass

    # 发送回复
    try:
        if isinstance(event, GroupMessageEvent) or getattr(event, "group_id", None):
            await bot.send_group_msg(group_id=event.group_id, message=text)
        else:
            await bot.send_private_msg(user_id=event.user_id, message=text)
        # 记录统计
        record_plugin_usage("miku_poke", user_id=str(event.user_id), command_name="拍一拍")
    except Exception as e:
        logger.warning(f"[miku_poke] 发送拍一拍回复失败: {e}")


register_plugin_info(
    "miku_poke",
    name="拍一拍回复",
    icon="👋",
    order=10,
    description="当用户拍一拍 Bot 时，Miku 会随机回复",
    commands=[],
    usage="""👋 拍一拍回复

在群聊或私聊中双击 Bot 头像（拍一拍），Miku 会随机回复一条消息。

【配置】
- 在 config/bot.yaml 的 miku_poke 区块中配置
- enabled: true/false — 是否启用
- response_style: text/card — 回复风格（目前仅支持 text）
""",
)

logger.info("[miku_poke] 拍一拍回复插件已加载")
