"""Miku 群聊总结：基于 zhenxun_plugin_summary_group 功能适配 MikuBot。"""

import asyncio
import re
import time
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from nonebot import get_bots, get_driver, on_command
from nonebot.adapters.onebot.v11 import (
    Bot,
    GroupMessageEvent,
    Message,
    MessageEvent,
    MessageSegment,
    PrivateMessageEvent,
)
from nonebot.log import logger
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER
from nonebot.plugin import PluginMetadata

from utils.config_manager import config_manager
from . import core, state

try:
    from utils.plugin_registry import register_plugin_info
except ImportError:
    register_plugin_info = lambda *args, **kwargs: None

try:
    from plugins.miku_stats import record_plugin_usage
except ImportError:
    record_plugin_usage = lambda *args, **kwargs: None

_SUMMARY_TEMPLATE = """\
miku_summary_group:
  # 启用群聊总结
  enabled: true
  # 手动/定时总结允许获取的最大消息数
  max_messages: 1000
  # 手动总结允许请求的最少消息数
  min_messages: 50
  # 每个用户触发手动总结后的冷却秒数，0 表示禁用
  cooldown_seconds: 60
  # 群历史缓存秒数，0 表示禁用
  message_cache_ttl_seconds: 300
  # 输出类型：image 或 text
  summary_output_type: image
  # 图片失败时是否回退为文本
  summary_fallback_enabled: true
  # 图片主题：dark / light / cyber
  summary_theme: dark
  # 是否排除机器人自己发送的消息
  exclude_bot_messages: false
"""

config_manager.register_plugin(
    "miku_summary_group",
    defaults={
        "enabled": True,
        "max_messages": 1000,
        "min_messages": 50,
        "cooldown_seconds": 60,
        "message_cache_ttl_seconds": 300,
        "summary_output_type": "image",
        "summary_fallback_enabled": True,
        "summary_theme": "dark",
        "exclude_bot_messages": False,
    },
    template_str=_SUMMARY_TEMPLATE,
    description="群聊总结配置；AI 平台及模型使用 config/ai_models.yaml",
)

__plugin_meta__ = PluginMetadata(
    name="Miku群聊总结",
    description="群聊总结，支持用户/关键词筛选、风格、定时任务和图片输出",
    usage=(
        "总结 <数量> [@用户] [关键词] [-p 风格]\n"
        "定时总结 <HH:MM|HHMM> [数量] [-p 风格]\n"
        "定时总结取消\n总结配置 [风格 设置|移除]\n"
        "总结风格 设置|移除（仅超管）\n总结模型 列表"
    ),
    type="application",
    supported_adapters={"~onebot.v11"},
)

_last_summary_time: dict[str, float] = {}
_scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")
_SCHEDULE_ID_PREFIX = "miku_summary_group:"
_ARG_GROUP_RE = re.compile(r"(?:^|\s)-g\s+(\d+)(?=\s|$)")
_ARG_STYLE_RE = re.compile(
    r"(?:^|\s)(?:-p|--prompt)(?:\s+)(.*?)(?=\s+(?:-g\s+\d+|-all)\s*|$)"
)
_ARG_COUNT_RE = re.compile(r"^\s*(\d+)(?:\s+|$)")


def _config(key: str, default: Any = None) -> Any:
    return config_manager.get("miku_summary_group", key, default)


def _parse_arguments(
    args: Message,
) -> tuple[int | None, str | None, str | None, int | None, set[str]]:
    plain = args.extract_plain_text().strip()
    count: int | None = None
    count_match = _ARG_COUNT_RE.match(plain)
    if count_match:
        count = int(count_match.group(1))
        plain = plain[count_match.end():]

    style = None
    style_match = _ARG_STYLE_RE.search(plain)
    if style_match:
        style = style_match.group(1).strip()
        plain = _ARG_STYLE_RE.sub(" ", plain, count=1)

    group_id = None
    group_match = _ARG_GROUP_RE.search(plain)
    if group_match:
        group_id = int(group_match.group(1))
        plain = _ARG_GROUP_RE.sub(" ", plain, count=1)

    user_ids = {
        str(segment.data.get("qq"))
        for segment in args
        if segment.type == "at"
        and segment.data.get("qq")
        and str(segment.data.get("qq")) != "all"
    }
    content_filter = re.sub(r"\s+", " ", plain).strip() or None
    return count, style or None, content_filter, group_id, user_ids


async def _is_admin(bot: Bot, event: MessageEvent) -> bool:
    if await SUPERUSER(bot, event):
        return True
    if not isinstance(event, GroupMessageEvent):
        return False
    try:
        member = await bot.get_group_member_info(
            group_id=event.group_id,
            user_id=event.user_id,
        )
        return member.get("role") in ("admin", "owner")
    except Exception as e:
        logger.warning(f"[miku_summary_group] 无法校验群管理员权限: {e}")
        return False


async def _run_summary(
    bot: Bot,
    group_id: int,
    count: int,
    style: str | None = None,
    content_filter: str | None = None,
    target_user_ids: set[str] | None = None,
    require_minimum_messages: bool = False,
) -> str:
    messages = await core.get_group_messages(
        bot,
        group_id,
        count,
        target_user_ids=target_user_ids,
        content_filter=content_filter,
    )
    if not messages:
        raise ValueError("最近消息中没有符合条件的有效聊天内容")
    if require_minimum_messages:
        minimum = int(_config("min_messages", 50) or 50)
        if len(messages) < minimum:
            raise ValueError(f"有效消息不足 {minimum} 条（当前 {len(messages)} 条）")
    names = sorted({item["name"] for item in messages}) if target_user_ids else None
    return await core.summarize(
        messages,
        group_id,
        style=style,
        content_filter=content_filter,
        target_user_names=names,
    )


summary_cmd = on_command(
    "总结",
    aliases={"群聊总结", "summarize"},
    priority=5,
    block=True,
)


@summary_cmd.handle()
async def _summary_handler(
    bot: Bot,
    event: MessageEvent,
    args: Message = CommandArg(),
) -> None:
    count, style, content_filter, requested_group, user_ids = _parse_arguments(args)
    is_superuser = await SUPERUSER(bot, event)

    if requested_group is not None and not is_superuser:
        await summary_cmd.finish("❌ 只有超级用户可以使用 -g 指定群聊")
    if requested_group is not None:
        group_id = requested_group
    elif isinstance(event, GroupMessageEvent):
        group_id = event.group_id
    else:
        await summary_cmd.finish("❌ 请在群聊中使用，或由超级用户通过 -g <群号> 指定目标群")

    minimum = int(_config("min_messages", 50) or 50)
    maximum = int(_config("max_messages", 1000) or 1000)
    count = count if count is not None else maximum
    if not minimum <= count <= maximum:
        await summary_cmd.finish(f"❌ 消息数量需在 {minimum} 到 {maximum} 之间")

    cooldown = max(0, int(_config("cooldown_seconds", 60) or 0))
    cooldown_key = str(event.user_id)
    now = time.monotonic()
    remaining = cooldown - (now - _last_summary_time.get(cooldown_key, 0))
    if cooldown and remaining > 0:
        await summary_cmd.finish(f"⏳ 总结冷却中，请 {int(remaining) + 1} 秒后再试")

    if not style:
        style = state.get_style(group_id)
    _last_summary_time[cooldown_key] = now
    record_plugin_usage(
        "miku_summary_group",
        user_id=str(event.user_id),
        command_name="总结",
    )
    await summary_cmd.send(f"📝 正在总结群 {group_id} 最近 {count} 条消息...")

    try:
        summary = await _run_summary(
            bot,
            group_id,
            count,
            style=style,
            content_filter=content_filter,
            target_user_ids=user_ids,
        )
        response_target = (
            event
            if isinstance(event, (GroupMessageEvent, PrivateMessageEvent))
            else None
        )
        await core.send_summary(bot, group_id, summary, count, target=response_target)
    except Exception as e:
        logger.warning(f"[miku_summary_group] 总结失败 (group={group_id}): {e}")
        await summary_cmd.finish(f"❌ 总结失败：{e}")


def _parse_time(value: str) -> tuple[int, int]:
    value = value.strip()
    if re.fullmatch(r"\d{1,2}:\d{2}", value):
        hour_s, minute_s = value.split(":")
    elif re.fullmatch(r"\d{3,4}", value):
        normalized = value.zfill(4)
        hour_s, minute_s = normalized[:2], normalized[2:]
    else:
        raise ValueError("时间格式请使用 HH:MM 或 HHMM")
    hour, minute = int(hour_s), int(minute_s)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError("时间需在 00:00 到 23:59 之间")
    return hour, minute


def _schedule_key(bot_id: str, group_id: str) -> str:
    return f"{_SCHEDULE_ID_PREFIX}{bot_id}:{group_id}"


def _install_schedule(schedule_id: str, spec: dict[str, Any]) -> None:
    _scheduler.add_job(
        _scheduled_job,
        trigger="cron",
        hour=int(spec["hour"]),
        minute=int(spec["minute"]),
        id=schedule_id,
        replace_existing=True,
        args=[schedule_id],
        misfire_grace_time=3600,
        coalesce=True,
        max_instances=1,
    )


async def _scheduled_job(schedule_id: str) -> None:
    spec = state.schedules().get(schedule_id)
    if not spec:
        return
    bots = get_bots()
    bot = bots.get(str(spec.get("bot_id")))
    if bot is None:
        logger.warning(f"[miku_summary_group] 定时任务找不到 Bot: {spec.get('bot_id')}")
        return
    group_value = str(spec.get("group_id"))
    if group_value == "all":
        try:
            groups = await bot.get_group_list()
        except Exception as e:
            logger.error(f"[miku_summary_group] 获取群列表失败: {e}")
            return
        group_ids = [int(group["group_id"]) for group in groups if group.get("group_id")]
    else:
        group_ids = [int(group_value)]

    semaphore = asyncio.Semaphore(2)

    async def summarize_group(group_id: int) -> None:
        async with semaphore:
            try:
                summary = await _run_summary(
                    bot,
                    group_id,
                    int(spec["count"]),
                    style=spec.get("style") or state.get_style(group_id),
                    require_minimum_messages=True,
                )
                await core.send_summary(bot, group_id, summary, int(spec["count"]))
            except Exception as e:
                logger.error(
                    f"[miku_summary_group] 定时总结失败 (group={group_id}): {e}"
                )

    await asyncio.gather(*(summarize_group(group_id) for group_id in group_ids))


def _schedule_argument(
    args: Message,
) -> tuple[str | None, int | None, str | None, int | None, bool]:
    plain = args.extract_plain_text().strip()
    parts = plain.split()
    time_str = parts[0] if parts else None
    count = None
    if len(parts) > 1 and parts[1].isdigit():
        count = int(parts[1])
        plain = plain.replace(parts[1], "", 1).strip()
    else:
        plain = " ".join(parts[1:])
    group_id = None
    match = _ARG_GROUP_RE.search(plain)
    if match:
        group_id = int(match.group(1))
        plain = _ARG_GROUP_RE.sub(" ", plain, count=1)
    style = None
    match_style = _ARG_STYLE_RE.search(plain)
    if match_style:
        style = match_style.group(1).strip() or None
    all_groups = bool(re.search(r"(?:^|\s)-all(?:\s|$)", plain))
    return time_str, count, style, group_id, all_groups


schedule_cmd = on_command(
    "定时总结",
    priority=5,
    block=True,
)


@schedule_cmd.handle()
async def _schedule_handler(
    bot: Bot,
    event: MessageEvent,
    args: Message = CommandArg(),
) -> None:
    if not await _is_admin(bot, event):
        await schedule_cmd.finish("❌ 仅群管理员及超级用户可以设置定时总结")
    time_str, count, style, requested_group, all_groups = _schedule_argument(args)
    if not time_str:
        await schedule_cmd.finish(
            "用法：定时总结 <HH:MM|HHMM> [数量] [-p 风格] [-g 群号|-all]"
        )
    is_superuser = await SUPERUSER(bot, event)
    if (requested_group is not None or all_groups) and not is_superuser:
        await schedule_cmd.finish("❌ -g 和 -all 仅限超级用户使用")
    if requested_group is not None and all_groups:
        await schedule_cmd.finish("❌ -g 和 -all 不能同时使用")
    if requested_group is not None:
        group_key = str(requested_group)
    elif all_groups:
        group_key = "all"
    elif isinstance(event, GroupMessageEvent):
        group_key = str(event.group_id)
    else:
        await schedule_cmd.finish("❌ 私聊设置时请由超级用户指定 -g 群号或 -all")
    try:
        hour, minute = _parse_time(time_str)
    except ValueError as e:
        await schedule_cmd.finish(f"❌ {e}")

    minimum = int(_config("min_messages", 50) or 50)
    maximum = int(_config("max_messages", 1000) or 1000)
    count = count if count is not None else maximum
    if not minimum <= count <= maximum:
        await schedule_cmd.finish(f"❌ 消息数量需在 {minimum} 到 {maximum} 之间")
    schedule_id = _schedule_key(str(bot.self_id), group_key)
    spec = {
        "bot_id": str(bot.self_id),
        "group_id": group_key,
        "hour": hour,
        "minute": minute,
        "count": count,
        "style": style,
    }
    try:
        state.set_schedule(schedule_id, spec)
    except Exception as e:
        logger.exception(f"[miku_summary_group] 保存定时总结失败: {e}")
        await schedule_cmd.finish("❌ 保存定时任务失败，请检查日志和数据目录权限")
    try:
        _install_schedule(schedule_id, spec)
    except Exception as e:
        logger.exception(f"[miku_summary_group] 注册定时任务失败: {e}")
        try:
            state.set_schedule(schedule_id, None)
        except OSError:
            logger.exception("[miku_summary_group] 回滚失败的定时任务配置时发生错误")
        await schedule_cmd.finish("❌ 注册定时任务失败，请检查日志")
    await schedule_cmd.finish(
        f"✅ 已设置每天 {hour:02d}:{minute:02d} 总结"
        f"{'所有群' if group_key == 'all' else f'群 {group_key}'}，最近 {count} 条消息"
    )


cancel_schedule_cmd = on_command("定时总结取消", priority=5, block=True)


@cancel_schedule_cmd.handle()
async def _cancel_schedule_handler(
    bot: Bot,
    event: MessageEvent,
    args: Message = CommandArg(),
) -> None:
    if not await _is_admin(bot, event):
        await cancel_schedule_cmd.finish("❌ 仅群管理员及超级用户可以取消定时总结")
    plain = args.extract_plain_text().strip()
    match = _ARG_GROUP_RE.search(plain)
    requested_group = int(match.group(1)) if match else None
    all_groups = bool(re.search(r"(?:^|\s)-all(?:\s|$)", plain))
    is_superuser = await SUPERUSER(bot, event)
    if (requested_group is not None or all_groups) and not is_superuser:
        await cancel_schedule_cmd.finish("❌ -g 和 -all 仅限超级用户使用")
    if requested_group is not None and all_groups:
        await cancel_schedule_cmd.finish("❌ -g 和 -all 不能同时使用")
    if requested_group is not None:
        group_key = str(requested_group)
    elif all_groups:
        group_key = "all"
    elif isinstance(event, GroupMessageEvent):
        group_key = str(event.group_id)
    else:
        await cancel_schedule_cmd.finish("❌ 私聊取消时请由超级用户指定 -g 群号或 -all")
    schedule_id = _schedule_key(str(bot.self_id), group_key)
    if schedule_id not in state.schedules():
        await cancel_schedule_cmd.finish("当前没有对应的定时总结任务")
    try:
        state.set_schedule(schedule_id, None)
        if _scheduler.running:
            _scheduler.remove_job(schedule_id)
    except Exception as e:
        logger.exception(f"[miku_summary_group] 取消定时总结失败: {e}")
        await cancel_schedule_cmd.finish("❌ 取消失败，请检查日志")
    await cancel_schedule_cmd.finish("✅ 定时总结已取消")


group_config_cmd = on_command("总结配置", priority=5, block=True)


@group_config_cmd.handle()
async def _group_config_handler(
    bot: Bot,
    event: MessageEvent,
    args: Message = CommandArg(),
) -> None:
    plain = args.extract_plain_text().strip()
    target_match = _ARG_GROUP_RE.search(plain)
    target_group = int(target_match.group(1)) if target_match else None
    is_superuser = await SUPERUSER(bot, event)
    if target_group is not None and not is_superuser:
        await group_config_cmd.finish("❌ 只有超级用户可以使用 -g 指定群")
    if target_group is not None:
        group_id = target_group
    elif isinstance(event, GroupMessageEvent):
        group_id = event.group_id
    else:
        await group_config_cmd.finish("❌ 私聊中查看配置请由超级用户使用 -g 群号")
    if not is_superuser and not await _is_admin(bot, event):
        await group_config_cmd.finish("❌ 仅群管理员及超级用户可以管理群总结配置")

    command_text = _ARG_GROUP_RE.sub(" ", plain).strip()
    if not command_text:
        global_style = state.get_style(group_id)
        own = state.get_group_style(group_id)
        await group_config_cmd.finish(
            f"群 {group_id} 总结配置：\n"
            f"本群默认风格：{own or '未设置'}\n"
            f"当前生效风格：{global_style or '默认'}\n"
            "AI 模型由 config/ai_models.yaml 中的多平台配置决定。"
        )

    match = re.fullmatch(r"风格\s+(设置)\s+(.+)", command_text)
    if match:
        style = match.group(2).strip()
        if len(style) > 300:
            await group_config_cmd.finish("❌ 风格说明不能超过 300 个字符")
        try:
            state.set_group_style(group_id, style)
        except OSError as e:
            logger.error(f"[miku_summary_group] 保存群风格失败: {e}")
            await group_config_cmd.finish("❌ 保存群风格失败，请检查数据目录权限")
        await group_config_cmd.finish(f"✅ 群 {group_id} 默认风格已更新")
    if re.fullmatch(r"风格\s+移除", command_text):
        try:
            state.set_group_style(group_id, None)
        except OSError as e:
            logger.error(f"[miku_summary_group] 移除群风格失败: {e}")
            await group_config_cmd.finish("❌ 移除群风格失败，请检查数据目录权限")
        await group_config_cmd.finish(f"✅ 已移除群 {group_id} 的默认风格")
    await group_config_cmd.finish(
        "用法：总结配置；总结配置 风格 设置 <风格>；总结配置 风格 移除"
    )


global_style_cmd = on_command("总结风格", priority=5, block=True, permission=SUPERUSER)


@global_style_cmd.handle()
async def _global_style_handler(args: Message = CommandArg()) -> None:
    plain = args.extract_plain_text().strip()
    match = re.fullmatch(r"设置\s+(.+)", plain)
    if match:
        style = match.group(1).strip()
        if len(style) > 300:
            await global_style_cmd.finish("❌ 风格说明不能超过 300 个字符")
        try:
            state.set_global_style(style)
        except OSError as e:
            logger.error(f"[miku_summary_group] 保存全局风格失败: {e}")
            await global_style_cmd.finish("❌ 保存全局风格失败，请检查数据目录权限")
        await global_style_cmd.finish("✅ 总结全局默认风格已更新")
    if plain == "移除":
        try:
            state.set_global_style(None)
        except OSError as e:
            logger.error(f"[miku_summary_group] 移除全局风格失败: {e}")
            await global_style_cmd.finish("❌ 移除全局风格失败，请检查数据目录权限")
        await global_style_cmd.finish("✅ 总结全局默认风格已移除")
    await global_style_cmd.finish("用法：总结风格 设置 <风格>；总结风格 移除")


model_list_cmd = on_command("总结模型", priority=5, block=True, permission=SUPERUSER)


@model_list_cmd.handle()
async def _model_list_handler(args: Message = CommandArg()) -> None:
    from plugins.miku_ai.config import get_ai_platforms

    plain = args.extract_plain_text().strip()
    if plain != "列表":
        await model_list_cmd.finish(
            "总结模型直接复用 miku_ai 的 ai_platforms。"
            "请编辑 config/ai_models.yaml 管理平台；用“总结模型 列表”查看当前顺序。"
        )
    platforms = get_ai_platforms()
    if not platforms:
        await model_list_cmd.finish("当前没有可用的 AI 平台配置")
    lines = [
        f"{index}. {item['name']}：{item['text_model'] or item['model']}"
        for index, item in enumerate(platforms, 1)
    ]
    await model_list_cmd.finish("当前总结模型候选（按故障切换顺序）：\n" + "\n".join(lines))


@get_driver().on_startup
async def _start_scheduler() -> None:
    for schedule_id, spec in state.schedules().items():
        try:
            _install_schedule(schedule_id, spec)
        except Exception as e:
            logger.exception(f"[miku_summary_group] 加载定时任务失败 ({schedule_id}): {e}")
    if not _scheduler.running:
        _scheduler.start()
    logger.info("[miku_summary_group] 群聊总结插件已加载，定时任务调度器已启动")


@get_driver().on_shutdown
async def _stop_scheduler() -> None:
    if _scheduler.running:
        _scheduler.shutdown(wait=False)


register_plugin_info(
    "miku_summary_group",
    name="Miku群聊总结",
    icon="📝",
    order=11,
    description="支持按用户/关键词筛选、风格、定时总结和图片输出",
    commands=["总结", "定时总结", "定时总结取消", "总结配置", "总结风格", "总结模型"],
    usage="总结 <数量> [@用户] [关键词] [-p 风格]",
)
