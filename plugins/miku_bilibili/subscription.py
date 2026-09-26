"""
Miku B站插件 - 订阅管理核心逻辑
UP主/直播/番剧订阅，定时检查，推送
"""

import time
import asyncio
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any, Tuple

import nonebot
from nonebot.adapters.onebot.v11 import Bot, Message, MessageSegment
from nonebot.log import logger

from .config import (
    is_subscribe_enabled, get_default_up_push_types,
    get_default_live_push_types, is_ad_filter_enabled,
    is_dynamic_image_enabled, get_new_content_threshold_minutes,
    is_at_all_enabled,
)
from .model import (
    SubscribeItem, SubType, Notification, NotificationType,
    VideoInfo, LiveInfo, SeasonInfo, UserInfo,
)
from .db import (
    create_subscription, get_subscription_by_target,
    get_subscription_by_id, get_all_subscriptions,
    update_subscription, delete_subscription,
    get_target_subscriptions, add_subscription_target,
    remove_subscription_target, get_subscription_targets,
    clean_orphaned_subscriptions,
)
from .api import (
    fetch_user_info, fetch_user_dynamics, fetch_user_videos,
    fetch_live_info, fetch_bangumi_info, search_bangumi,
)
from .cache import get_cached_avatar, get_cached_bangumi_cover, download_image
from .message import build_video_message


# ============================================================
# 订阅添加
# ============================================================

async def add_up_sub(uid: str, target_id: str) -> str:
    """添加UP主订阅"""
    existing = get_subscription_by_target(SubType.UP, uid)
    if existing:
        targets = get_subscription_targets(existing.id)
        if target_id in targets:
            return f"ℹ️ 你已经订阅过UP主「{existing.name}」(UID: {uid})"
        add_subscription_target(existing.id, target_id)
        return f"✅ 已添加订阅「{existing.name}」(UID: {uid}) 到当前会话"

    try:
        user_info = await fetch_user_info(uid)
    except Exception as e:
        return f"❌ 获取用户信息失败: {e}"

    if not user_info or not user_info.name:
        return f"❌ 未找到UID为 {uid} 的用户，请检查ID是否正确"

    room_id = ""
    if user_info.live_room_status >= 0 and user_info.live_room_url:
        import re
        m = re.search(r"(\d+)", user_info.live_room_url)
        if m:
            room_id = m.group(1)

    last_dynamic_ts = 0
    try:
        dyn_data = await fetch_user_dynamics(uid)
        if dyn_data and dyn_data.get("items"):
            for item in dyn_data["items"]:
                ts = item.get("modules", {}).get("module_author", {}).get("pub_ts", 0)
                if ts > last_dynamic_ts:
                    last_dynamic_ts = ts
                    break
    except Exception as e:
        logger.debug(f"[miku_bilibili] 获取初始动态时间失败: {e}")

    last_video_bvid = ""
    last_video_created = 0
    try:
        video_data = await fetch_user_videos(uid, ps=1)
        vlist = video_data.get("list", {}).get("vlist", [])
        if vlist:
            last_video_bvid = vlist[0].get("bvid", "")
            last_video_created = vlist[0].get("created", 0)
    except Exception as e:
        logger.debug(f"[miku_bilibili] 获取初始视频时间失败: {e}")

    push_types = get_default_up_push_types()
    item = SubscribeItem(
        sub_type=SubType.UP,
        target_id=uid,
        name=user_info.name,
        face=user_info.face,
        room_id=room_id,
        enable_dynamic="dynamic" in push_types,
        enable_video="video" in push_types,
        enable_live="live" in push_types or bool(room_id),
        last_dynamic_ts=last_dynamic_ts,
        last_video_bvid=last_video_bvid,
        last_video_created=last_video_created,
        last_live_status=user_info.live_room_status,
    )

    sub_id = create_subscription(item)
    add_subscription_target(sub_id, target_id)

    return (
        f"🎉 订阅成功！\n"
        f"UP主：{user_info.name}\n"
        f"UID：{uid}\n"
        f"直播间：{room_id or '无'}\n"
        f"订阅ID：{sub_id}"
    )


async def add_live_sub(room_id: str, target_id: str) -> str:
    """添加直播订阅"""
    existing = get_subscription_by_target(SubType.LIVE, room_id)
    if existing:
        targets = get_subscription_targets(existing.id)
        if target_id in targets:
            return f"ℹ️ 你已经订阅过直播间「{existing.name}」(房间号: {room_id})"
        add_subscription_target(existing.id, target_id)
        return f"✅ 已添加订阅「{existing.name}」(房间号: {room_id}) 到当前会话"

    try:
        live_info = await fetch_live_info(room_id)
    except Exception as e:
        return f"❌ 获取直播间信息失败: {e}"

    if not live_info or not live_info.uname:
        return f"❌ 未找到房间号 {room_id} 的信息，请检查ID是否正确"

    item = SubscribeItem(
        sub_type=SubType.LIVE,
        target_id=room_id,
        name=live_info.uname,
        face=live_info.cover,
        room_id=room_id,
        enable_dynamic=False,
        enable_video=False,
        enable_live=True,
        last_live_status=live_info.live_status,
    )

    sub_id = create_subscription(item)
    add_subscription_target(sub_id, target_id)

    return (
        f"🎉 订阅成功！\n"
        f"主播：{live_info.uname}\n"
        f"房间号：{room_id}\n"
        f"订阅ID：{sub_id}"
    )


async def add_bangumi_sub(season_id: str, target_id: str) -> str:
    """添加番剧订阅"""
    existing = get_subscription_by_target(SubType.SEASON, season_id)
    if existing:
        targets = get_subscription_targets(existing.id)
        if target_id in targets:
            return f"ℹ️ 你已经订阅过番剧「{existing.name}」(Season ID: {season_id})"
        add_subscription_target(existing.id, target_id)
        return f"✅ 已添加订阅「{existing.name}」(Season ID: {season_id}) 到当前会话"

    try:
        season_info = await fetch_bangumi_info(season_id=int(season_id))
    except Exception as e:
        return f"❌ 获取番剧信息失败: {e}"

    if not season_info or not season_info.title:
        return f"❌ 未找到Season ID为 {season_id} 的番剧"

    last_ep_id = 0
    if season_info.total_ep > 0:
        try:
            ep_data = await fetch_bangumi_info(season_id=int(season_id))
            episodes = []
            if hasattr(ep_data, 'total_ep') and ep_data.total_ep > 0:
                last_ep_id = season_info.total_ep
        except Exception:
            pass

    item = SubscribeItem(
        sub_type=SubType.SEASON,
        target_id=season_id,
        name=season_info.title,
        face=season_info.cover,
        enable_dynamic=False,
        enable_video=True,
        enable_live=False,
        last_season_ep_id=last_ep_id,
    )

    sub_id = create_subscription(item)
    add_subscription_target(sub_id, target_id)

    return (
        f"🎉 订阅成功！\n"
        f"番剧：{season_info.title}\n"
        f"Season ID：{season_id}\n"
        f"订阅ID：{sub_id}"
    )


async def add_subscription_by_id(
    id_str: str,
    target_id: str,
    is_live: bool = False,
) -> str:
    """根据ID字符串添加订阅（自动判断类型）"""
    id_str = id_str.strip()

    if id_str.lower().startswith("ss"):
        return await add_bangumi_sub(id_str[2:], target_id)

    if id_str.lower().startswith("ep"):
        try:
            ep_id = int(id_str[2:])
            season_info = await fetch_bangumi_info(ep_id=ep_id)
            if season_info and season_info.season_id:
                return await add_bangumi_sub(str(season_info.season_id), target_id)
        except Exception as e:
            return f"❌ 解析番剧EP失败: {e}"
        return f"❌ 未能找到 ep{id_str[2:]} 对应的番剧信息"

    if not id_str.isdigit():
        results = await search_bangumi(id_str)
        if not results:
            return f"❌ 未搜索到名为「{id_str}」的番剧，请输入正确的ID或名称"
        if len(results) == 1:
            return await add_bangumi_sub(str(results[0]["season_id"]), target_id)
        msg_parts = [f"🔍 找到 {len(results)} 个相关番剧，请使用 Season ID 订阅："]
        for i, item in enumerate(results[:5], 1):
            import re
            title = re.sub(r"<em.*?>(.*?)</em>", r"\1", item.get("title", ""))
            msg_parts.append(f"  {i}. {title} (ss{item.get('season_id', '?')})")
        msg_parts.append("\n使用方式：B站订阅 add ss<Season ID>")
        return "\n".join(msg_parts)

    if is_live:
        return await add_live_sub(id_str, target_id)

    return await add_up_sub(id_str, target_id)


# ============================================================
# 订阅删除和管理
# ============================================================

def delete_subscription_from_target(sub_id: int, target_id: str) -> str:
    """从目标会话中删除订阅"""
    sub = get_subscription_by_id(sub_id)
    if not sub:
        return f"❌ 未找到ID为 {sub_id} 的订阅"

    targets = get_subscription_targets(sub.id)
    if target_id not in targets:
        return f"❌ 你没有订阅过「{sub.name}」(ID: {sub_id})"

    remove_subscription_target(sub.id, target_id)
    clean_orphaned_subscriptions()

    return f"✅ 成功取消订阅「{sub.name}」(ID: {sub_id})"


def list_subscriptions(target_id: str) -> List[SubscribeItem]:
    """获取指定会话的订阅列表"""
    return get_target_subscriptions(target_id)


def update_sub_config(sub_id: int, target_id: str, updates: dict) -> str:
    """更新订阅配置"""
    sub = get_subscription_by_id(sub_id)
    if not sub:
        return f"❌ 未找到ID为 {sub_id} 的订阅"

    targets = get_subscription_targets(sub.id)
    if target_id not in targets:
        return f"❌ 你没有权限配置ID为 {sub_id} 的订阅"

    for key, value in updates.items():
        if hasattr(sub, key):
            setattr(sub, key, value)

    update_subscription(sub)
    return f"✅ 已更新订阅「{sub.name}」的推送设置"


# ============================================================
# 更新检查逻辑
# ============================================================

async def check_up_updates(sub: SubscribeItem, force_push: bool = False) -> List[Notification]:
    """检查UP主更新，返回通知列表"""
    notifications = []
    uid = sub.target_id
    time_threshold = datetime.now() - timedelta(minutes=get_new_content_threshold_minutes())

    if sub.enable_dynamic:
        try:
            dyn_data = await fetch_user_dynamics(uid)
            if dyn_data and dyn_data.get("items"):
                latest = dyn_data["items"][0]
                pub_ts = latest.get("modules", {}).get("module_author", {}).get("pub_ts", 0)

                if pub_ts > sub.last_dynamic_ts and pub_ts > 0:
                    pub_time = datetime.fromtimestamp(pub_ts)
                    is_recent = pub_time > time_threshold

                    if force_push or is_recent:
                        dyn_id = latest.get("id_str", "")
                        text = ""
                        images = []

                        modules = latest.get("modules", {})
                        mod_dynamic = modules.get("module_dynamic", {})
                        desc = mod_dynamic.get("desc", {})
                        if desc:
                            text = desc.get("text", "")

                        major = mod_dynamic.get("major", {})
                        if major:
                            if major.get("type") == "MAJOR_TYPE_DRAW":
                                for item in major.get("draw", {}).get("items", []):
                                    src = item.get("src", "")
                                    if src:
                                        images.append(src)
                            elif major.get("type") == "MAJOR_TYPE_ARCHIVE":
                                archive = major.get("archive", {})
                                text = f"投稿了新视频：{archive.get('title', '')}\n{archive.get('desc', '')}"
                                cover = archive.get("cover", "")
                                if cover:
                                    images.append(cover)

                        msg_parts = [f"📢 {sub.name} 发布了动态！"]
                        if text:
                            msg_parts.append(text[:500])
                        msg_parts.append(f"🔗 https://t.bilibili.com/{dyn_id}")

                        content = ["\n".join(msg_parts)]
                        if images:
                            content.extend(images[:3])

                        notifications.append(Notification(
                            content=content,
                            type=NotificationType.DYNAMIC,
                            at_all=sub.at_all_dynamic,
                        ))

                    sub.last_dynamic_ts = pub_ts
                    update_subscription(sub)
        except Exception as e:
            logger.warning(f"[miku_bilibili] 检查UP动态失败 UID={uid}: {e}")

    if sub.enable_video:
        try:
            video_data = await fetch_user_videos(uid, ps=5)
            vlist = video_data.get("list", {}).get("vlist", [])
            if vlist:
                latest_video = vlist[0]
                bvid = latest_video.get("bvid", "")
                created = latest_video.get("created", 0)

                if bvid and bvid != sub.last_video_bvid and created > sub.last_video_created:
                    created_time = datetime.fromtimestamp(created) if created else datetime.min
                    is_recent = created_time > time_threshold

                    if force_push or is_recent:
                        title = latest_video.get("title", "未知标题")
                        pic = latest_video.get("pic", "")
                        author = latest_video.get("author", sub.name)

                        msg_parts = [
                            f"🎉 {author} 投稿了新视频！",
                            f"标题：{title}",
                            f"BV号：{bvid}",
                            f"🔗 https://www.bilibili.com/video/{bvid}",
                        ]

                        content = ["\n".join(msg_parts)]
                        if pic:
                            content.append(pic)

                        notifications.append(Notification(
                            content=content,
                            type=NotificationType.VIDEO,
                            at_all=sub.at_all_video,
                        ))

                    sub.last_video_bvid = bvid
                    sub.last_video_created = created
                    update_subscription(sub)
        except Exception as e:
            logger.warning(f"[miku_bilibili] 检查UP视频失败 UID={uid}: {e}")

    if sub.enable_live and sub.room_id:
        try:
            live_info = await fetch_live_info(sub.room_id)
            live_status = live_info.live_status

            if sub.last_live_status != 1 and live_status == 1:
                title = live_info.title
                cover = live_info.cover

                msg_parts = [
                    f"🔴 {sub.name} 开播啦！",
                    f"标题：{title}",
                    f"🔗 https://live.bilibili.com/{sub.room_id}",
                ]

                content = ["\n".join(msg_parts)]
                if cover:
                    content.append(cover)

                notifications.append(Notification(
                    content=content,
                    type=NotificationType.LIVE,
                    at_all=sub.at_all_live,
                ))

            sub.last_live_status = live_status
            update_subscription(sub)
        except Exception as e:
            logger.warning(f"[miku_bilibili] 检查直播状态失败 room_id={sub.room_id}: {e}")

    return notifications


async def check_live_updates(sub: SubscribeItem) -> List[Notification]:
    """检查直播订阅更新"""
    notifications = []
    room_id = sub.target_id

    try:
        live_info = await fetch_live_info(room_id)
        live_status = live_info.live_status

        if sub.last_live_status != 1 and live_status == 1:
            title = live_info.title
            cover = live_info.cover
            uname = live_info.uname or sub.name

            msg_parts = [
                f"🔴 {uname} 开播啦！",
                f"标题：{title}",
                f"🔗 https://live.bilibili.com/{room_id}",
            ]

            content = ["\n".join(msg_parts)]
            if cover:
                content.append(cover)

            notifications.append(Notification(
                content=content,
                type=NotificationType.LIVE,
                at_all=sub.at_all_live,
            ))

        sub.last_live_status = live_status
        update_subscription(sub)
    except Exception as e:
        logger.warning(f"[miku_bilibili] 检查直播订阅失败 room_id={room_id}: {e}")

    return notifications


async def check_bangumi_updates(sub: SubscribeItem, force_push: bool = False) -> List[Notification]:
    """检查番剧更新"""
    notifications = []
    season_id = sub.target_id

    try:
        season_info = await fetch_bangumi_info(season_id=int(season_id))
        if not season_info:
            return notifications

        ep_data = await fetch_bangumi_info(season_id=int(season_id))
        if not ep_data:
            return notifications

        try:
            full_info = await fetch_bangumi_info(season_id=int(season_id))
        except Exception:
            full_info = season_info

        latest_ep_id = full_info.total_ep if hasattr(full_info, 'total_ep') else 0

        if latest_ep_id > sub.last_season_ep_id and latest_ep_id > 0:
            if force_push or sub.last_season_ep_id > 0:
                msg_parts = [
                    f"🎉 《{sub.name}》更新啦！",
                    f"最新话：第 {latest_ep_id} 话",
                    f"🔗 https://www.bilibili.com/bangumi/play/ss{season_id}",
                ]

                content = ["\n".join(msg_parts)]
                if full_info.cover:
                    content.append(full_info.cover)

                notifications.append(Notification(
                    content=content,
                    type=NotificationType.VIDEO,
                    at_all=sub.at_all_video,
                ))

            sub.last_season_ep_id = latest_ep_id
            update_subscription(sub)
    except Exception as e:
        logger.warning(f"[miku_bilibili] 检查番剧更新失败 season_id={season_id}: {e}")

    return notifications


async def check_subscription_updates(sub: SubscribeItem, force_push: bool = False) -> List[Notification]:
    """检查单个订阅的所有更新"""
    try:
        if sub.sub_type == SubType.UP:
            return await check_up_updates(sub, force_push)
        elif sub.sub_type == SubType.LIVE:
            return await check_live_updates(sub)
        elif sub.sub_type == SubType.SEASON:
            return await check_bangumi_updates(sub, force_push)
    except Exception as e:
        logger.error(f"[miku_bilibili] 检查订阅失败 {sub.name}: {e}")
    return []


# ============================================================
# 通知发送
# ============================================================

async def send_notification(bot: Bot, target_id: str, notification: Notification) -> bool:
    """发送通知到目标会话"""
    try:
        msg = Message()

        if notification.at_all and target_id.startswith("group_") and is_at_all_enabled():
            msg.append(MessageSegment.at_all())

        for i, item in enumerate(notification.content):
            if isinstance(item, str):
                if item.startswith("http") and (item.endswith((".jpg", ".png", ".jpeg", ".gif", ".webp")) or "i0.hdslb.com" in item or "hdslb.com" in item):
                    msg.append(MessageSegment.image(item))
                else:
                    if i > 0 and msg:
                        msg.append("\n")
                    msg.append(item)
            else:
                msg.append(item)

        if target_id.startswith("group_"):
            group_id = int(target_id.replace("group_", ""))
            await bot.send_group_msg(group_id=group_id, message=msg)
        else:
            user_id = int(target_id.replace("private_", ""))
            await bot.send_private_msg(user_id=user_id, message=msg)

        return True
    except Exception as e:
        logger.warning(f"[miku_bilibili] 发送订阅通知失败 {target_id}: {e}")
        return False


# ============================================================
# 全量检查（定时任务调用）
# ============================================================

async def check_all_subscriptions() -> None:
    """检查所有订阅并推送更新（定时任务调用）"""
    if not is_subscribe_enabled():
        return

    bots = nonebot.get_bots()
    if not bots:
        return

    bot = next(iter(bots.values()))

    all_subs = get_all_subscriptions()
    if not all_subs:
        return

    logger.info(f"[miku_bilibili] 开始检查 {len(all_subs)} 个订阅")

    for sub in all_subs:
        try:
            notifications = await asyncio.wait_for(
                check_subscription_updates(sub),
                timeout=45
            )

            if notifications:
                targets = get_subscription_targets(sub.id)
                for notif in notifications:
                    for target_id in targets:
                        await send_notification(bot, target_id, notif)

                logger.info(f"[miku_bilibili] 订阅「{sub.name}」检测到 {len(notifications)} 个更新")
        except asyncio.TimeoutError:
            logger.warning(f"[miku_bilibili] 订阅检查超时: {sub.name}")
        except Exception as e:
            logger.error(f"[miku_bilibili] 检查订阅异常 {sub.name}: {e}")

    logger.info("[miku_bilibili] 订阅检查完成")


async def force_push_subscription(sub_id: int) -> Tuple[bool, str]:
    """强制推送指定订阅的最新内容"""
    sub = get_subscription_by_id(sub_id)
    if not sub:
        return False, f"❌ 未找到ID为 {sub_id} 的订阅"

    bots = nonebot.get_bots()
    if not bots:
        return False, "❌ 没有机器人实例在线"

    bot = next(iter(bots.values()))

    try:
        notifications = await asyncio.wait_for(
            check_subscription_updates(sub, force_push=True),
            timeout=45
        )

        if notifications:
            targets = get_subscription_targets(sub.id)
            for notif in notifications:
                for target_id in targets:
                    await send_notification(bot, target_id, notif)
            return True, f"✅ 已推送「{sub.name}」的最新内容"
        else:
            return True, f"ℹ️ 「{sub.name}」暂无新内容可推送"
    except Exception as e:
        return False, f"❌ 推送失败: {e}"
