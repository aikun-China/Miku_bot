"""
Miku 群聊总结 (summary_group 适配版)
====================================
基于 zhenxun_plugin_summary_group 重构，适配 MikuBot 架构：

- 配置：摒弃 Config.add_plugin_config()，配置（默认AI模型、最大消息数、冷却等）
        写入 config/bot.yaml 的 miku_summary_group 区
- LLM：移除 zhenxun.services.llm，用 httpx 直接请求 OpenAI/DeepSeek 兼容接口
- 历史：用 OneBot 的 get_group_msg_history API 获取群聊历史
- 渲染：接入 MikuBot 的 utils/screenshot.py 渲染成图片
- 命令：on_command 注册「总结」命令，带群冷却
"""

import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from nonebot import on_command
from nonebot.adapters.onebot.v11 import (
    Bot,
    MessageEvent,
    Message,
    MessageSegment,
    GroupMessageEvent,
)
from nonebot.plugin import PluginMetadata
from nonebot.log import logger
from nonebot.params import CommandArg
import httpx

try:
    from utils.plugin_registry import register_plugin_info
except ImportError:
    register_plugin_info = lambda *args, **kwargs: None

try:
    from plugins.miku_stats import record_plugin_usage
except ImportError:
    record_plugin_usage = lambda *args, **kwargs: None

try:
    from utils.config_manager import config_manager
except ImportError:
    config_manager = None


__plugin_meta__ = PluginMetadata(
    name="Miku群聊总结",
    description="总结群聊历史（httpx 直连 LLM + 截图渲染）",
    usage="总结 [最近N条]",
    type="application",
    supported_adapters={"~onebot.v11"},
)


# 每群冷却时间记录
_last_summary_time: Dict[int, float] = {}


def _get_cfg(key: str, default=None):
    if config_manager is None:
        return default
    try:
        return config_manager.get("miku_summary_group", key, default)
    except Exception:
        return default


def _get_api_config():
    """获取 LLM API 配置。优先用 miku_summary_group 自己的，回退 miku_ai 的。"""
    api_key = _get_cfg("api_key", "")
    base_url = _get_cfg("base_url", "")
    model = _get_cfg("model", "")

    if not api_key and config_manager is not None:
        try:
            api_key = config_manager.get("miku_ai", "cloud_api_key", "") or ""
        except Exception:
            pass
    if not base_url and config_manager is not None:
        try:
            base_url = config_manager.get("miku_ai", "cloud_base_url", "") or ""
        except Exception:
            pass
    if not model and config_manager is not None:
        try:
            model = config_manager.get("miku_ai", "cloud_text_model", "") or ""
        except Exception:
            pass

    if not base_url:
        base_url = "https://open.bigmodel.cn/api/paas/v4"
    if not model:
        model = "glm-4.5-air"
    return api_key, base_url.rstrip("/"), model


def _build_prompt(transcript: str) -> str:
    return (
        "你是Miku，请把以下QQ群聊天记录整理成一份简洁的中文总结。\n"
        "要求：\n"
        "1. 提炼大家讨论的主题与结论\n"
        "2. 分条列出要点，语气轻松可爱\n"
        "3. 控制在150字以内\n"
        "4. 只输出总结正文，不要额外解释\n\n"
        "聊天记录：\n"
        + transcript
    )


async def _summarize(transcript: str) -> Optional[str]:
    """用 httpx 直连 LLM 生成总结。"""
    api_key, base_url, model = _get_api_config()
    if not api_key:
        return None
    try:
        url = f"{base_url}/chat/completions"
        payload = {
            "model": model,
            "messages": [
                {"role": "user", "content": _build_prompt(transcript)},
            ],
            "temperature": 0.6,
            "max_tokens": 400,
        }
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(url, json=payload, headers=headers)
            if r.status_code == 200:
                data = r.json()
                content = data.get("choices", [{}])[0].get("message", {}).get("content")
                if content:
                    return content.strip()
            else:
                logger.warning(f"[miku_summary_group] LLM 请求失败: {r.status_code} {r.text[:200]}")
    except Exception as e:
        logger.warning(f"[miku_summary_group] LLM 调用异常: {e}")
    return None


async def _get_group_history(bot: Bot, group_id: int, count: int) -> List[dict]:
    """用 OneBot get_group_msg_history 获取群历史（返回按时间倒序）。"""
    try:
        result = await bot.call_api(
            "get_group_msg_history",
            group_id=group_id,
            message_seq=0,
            count=count,
        )
        return result.get("messages") or []
    except Exception as e:
        logger.warning(f"[miku_summary_group] 获取群历史失败: {e}")
        return []


def _build_transcript(messages: List[dict]) -> str:
    """将历史消息转成纯文本对话记录（正序）。"""
    lines: List[str] = []
    for msg in reversed(messages):  # 倒序 -> 正序
        sender = msg.get("sender") or {}
        nickname = sender.get("card") or sender.get("nickname") or "未知用户"
        message = msg.get("message")
        text = message if isinstance(message, str) else _extract_text(message)
        if text:
            lines.append(f"{nickname}: {text}")
    return "\n".join(lines)


def _extract_text(segments) -> str:
    """从消息段列表提取纯文本。"""
    parts = []
    if isinstance(segments, list):
        for seg in segments:
            if isinstance(seg, dict) and seg.get("type") == "text":
                parts.append(seg.get("data", {}).get("text", ""))
    return "".join(parts).strip()


def _render_to_html(summary: str, model_name: str) -> str:
    """把总结渲染成 HTML（供截图引擎出图）。"""
    esc = summary.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>")
    return f"""<!DOCTYPE html>
<html lang="zh">
<head><meta charset="utf-8"><style>
  body {{ margin:0; padding:24px 28px; background:linear-gradient(135deg,#1e1e2e,#2d2d44);
         font-family:'Microsoft YaHei',sans-serif; width:560px; color:#fff; }}
  h1 {{ font-size:20px; margin:0 0 4px; color:#89b4fa; }}
  .sub {{ font-size:12px; color:#a0a0c0; margin-bottom:14px; }}
  .box {{ background:rgba(255,255,255,.06); border-radius:10px; padding:16px 18px;
          font-size:15px; line-height:1.7; }}
</style></head>
<body>
  <h1>📝 群聊总结</h1>
  <div class="sub">由 {model_name} 生成</div>
  <div class="box">{esc}</div>
</body>
</html>"""


summary_cmd = on_command(
    "总结",
    aliases={"群聊总结", "summarize"},
    priority=10,
    block=True,
)


@summary_cmd.handle()
async def _summary_handler(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    if not isinstance(event, GroupMessageEvent):
        await summary_cmd.finish("❌ 此指令仅限群聊使用")

    group_id = event.group_id

    # 冷却
    cooldown = int(_get_cfg("cooldown_seconds", 60) or 60)
    now = time.time()
    last = _last_summary_time.get(group_id, 0)
    if now - last < cooldown:
        remain = int(cooldown - (now - last))
        await summary_cmd.finish(f"⏳ 总结冷却中，请 {remain} 秒后再试")

    # 消息数
    arg_str = args.extract_plain_text().strip()
    max_messages = int(_get_cfg("max_messages", 50) or 50)
    count = max_messages
    if arg_str.isdigit():
        count = min(int(arg_str), max_messages * 2)

    record_plugin_usage("miku_summary_group", user_id=str(event.user_id), command_name="总结")

    await summary_cmd.send(f"📝 正在总结最近 {count} 条消息...")

    messages = await _get_group_history(bot, group_id, count)
    if not messages:
        await summary_cmd.finish("❌ 获取群聊历史失败（可能无机器人管理权限）")

    transcript = _build_transcript(messages)
    if not transcript:
        await summary_cmd.finish("❌ 最近消息中没有可总结的文本内容")

    summary = await _summarize(transcript)
    if not summary:
        await summary_cmd.finish("❌ 总结生成失败（请检查 LLM API 配置）")

    _last_summary_time[group_id] = now

    # 渲染成图片发送
    try:
        _, _, model_name = _get_api_config()
        html = _render_to_html(summary, model_name)
        from utils.screenshot import screenshot_html
        img_path = await screenshot_html(html, width=600, height=400)
        from utils.screenshot import to_image_uri
        await summary_cmd.send(MessageSegment.image(to_image_uri(str(img_path))))
    except Exception as e:
        logger.warning(f"[miku_summary_group] 渲染图片失败，改用文本: {e}")
        await summary_cmd.finish(summary)


register_plugin_info(
    "miku_summary_group",
    name="Miku群聊总结",
    icon="📝",
    order=11,
    description="总结群聊历史（httpx 直连 LLM + 截图渲染）",
    commands=["总结"],
    usage="总结 [最近N条]",
)

logger.info("[miku_summary_group] 群聊总结插件已加载")
