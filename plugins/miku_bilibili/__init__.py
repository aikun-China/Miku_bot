"""
Miku B站内容解析+订阅+成分姬插件
========================
功能：
- 被动解析：自动识别消息中的B站链接（视频/直播/专栏/番剧/用户空间）
- 视频下载：bili下载 / b站下载
- 封面获取：bili封面 / b站封面
- 群组控制：开启群被动b站解析 / 关闭群被动b站解析
- 扫码登录：bili登录
- 成分查询：查成分（原 miku_ddcheck 独立插件，已并入本插件 ddcheck 子模块）
- B站订阅：B站订阅 添加/删除/列表/设置/清空
  - UP主动态/视频更新推送
  - 直播开播提醒
  - 番剧更新提醒

支持格式：
- 视频：BV号、AV号、完整链接
- 直播：room号
- 专栏：cv号
- 番剧：ss号、ep号
- 用户空间：uid、space链接
- 短链接：b23.tv
"""

import asyncio
from pathlib import Path
import sys

from curl_cffi import requests as curl_requests

from nonebot import on_message, on_command, get_driver
from nonebot.internal.matcher import Matcher
from nonebot.adapters.onebot.v11 import Bot, MessageEvent, MessageSegment, GroupMessageEvent, Message
from nonebot.plugin import PluginMetadata
from nonebot.log import logger
from nonebot.permission import SUPERUSER
from nonebot.params import CommandArg

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

try:
    from utils.plugin_registry import register_plugin_info
except ImportError:
    register_plugin_info = lambda *args, **kwargs: None

try:
    from plugins.miku_stats import record_plugin_usage
except ImportError:
    record_plugin_usage = lambda *args, **kwargs: None

from utils.screenshot import to_image_uri

from .config import (
    is_enabled, is_auto_parse, is_download_enabled, is_subscribe_enabled,
    get_subscribe_interval, get_request_timeout, DATA_DIR,
    get_response_style, is_auto_download_enabled, get_auto_download_max_duration,
)
from .model import ContentType, UrlParseError, UnsupportedUrlError
from .db import init_db, get_group_setting, set_group_setting
from .parser import (
    BilibiliUrlParser, extract_text_from_message, parse_url, extract_video_id,
)
from .api import (
    fetch_video_info, fetch_live_info, fetch_user_info,
)
from .cache import SessionCache
from .credential import (
    is_logged_in, check_login_status, clear_cookies, save_cookies,
    get_headers_with_cookie, get_credential, _HEADERS, generate_qrcode,
    poll_qrcode_status, cookies_str_to_dict,
)
from .download import download_video, get_video_cover_data, auto_download_video
from .message import (
    build_video_message, build_live_message, build_user_message,
    build_season_message, build_article_message,
)
from .subscription import (
    add_subscription_by_id, delete_subscription_from_target,
    list_subscriptions, update_sub_config, check_all_subscriptions,
    force_push_subscription,
)
from .ddcheck import ddcheck_cmd


__plugin_meta__ = PluginMetadata(
    name="MikuB站解析",
    description="B站内容解析（视频、直播、专栏、番剧、用户空间），支持被动解析、下载、封面获取与VTuber成分查询",
    usage="""📺 B站内容解析

【被动解析】
发送B站链接自动解析，支持视频/直播/专栏/番剧/用户空间

【手动命令】
  • bili下载 / b站下载 <链接> - 下载视频
  • bili封面 / b站封面 - 获取视频封面（引用消息）
  • 开启群被动b站解析 - 开启被动解析
  • 关闭群被动b站解析 - 关闭被动解析

【成分查询】
  • 查成分 <B站用户名/UID> - 查询关注列表的VTuber成分
""",
    type="application",
    supported_adapters={"~onebot.v11"},
)


# ============================================================
# 会话缓存
# ============================================================
_session_cache = SessionCache()


# ============================================================
# 工具函数
# ============================================================

def _get_session_key(event: MessageEvent) -> str:
    """获取会话标识"""
    if isinstance(event, GroupMessageEvent):
        return f"group_{event.group_id}"
    return f"private_{event.user_id}"


def _is_group_enabled(group_id: int) -> bool:
    """检查群组是否启用被动解析"""
    return get_group_setting(str(group_id), "enabled", True)


def _set_group_enabled(group_id: int, enabled: bool):
    """设置群组启用状态"""
    set_group_setting(str(group_id), "enabled", enabled)


async def _extract_url_from_event(event: MessageEvent) -> str:
    """从事件中提取B站URL（含回复）"""
    url = ""
    try:
        if hasattr(event, "reply") and event.reply:
            reply_text = extract_text_from_message(event.reply.message)
            items = BilibiliUrlParser.extract_all(reply_text)
            if items:
                return items[0][0]
    except Exception:
        pass

    msg_text = extract_text_from_message(event.get_message())
    items = BilibiliUrlParser.extract_all(msg_text)
    if items:
        return items[0][0]

    return ""


# ============================================================
# 被动解析处理器
# ============================================================

bili_listener = on_message(priority=9, block=False)


@bili_listener.handle()
async def _handle_bilibili(matcher: Matcher, bot: Bot, event: MessageEvent):
    if not is_enabled() or not is_auto_parse():
        return

    if isinstance(event, GroupMessageEvent):
        if not _is_group_enabled(event.group_id):
            return

    message_text = extract_text_from_message(event.get_message())
    if not message_text:
        logger.debug("[miku_bilibili] 消息文本为空，跳过")
        return

    logger.info(f"[miku_bilibili] 收到消息，提取文本: {message_text[:100]}...")

    items = BilibiliUrlParser.extract_all(message_text)
    if not items:
        logger.debug("[miku_bilibili] 未识别到B站链接")
        return

    # 识别到B站链接，阻止事件继续传播（避免AI插件抢话）
    matcher.stop_propagation()
    logger.info(f"[miku_bilibili] 识别到 {len(items)} 个B站链接")

    for url_or_id, content_type in items[:2]:
        if not _session_cache.should_parse(event, url_or_id):
            logger.debug(f"[miku_bilibili] 缓存命中，跳过: {url_or_id[:50]}")
            continue

        try:
            logger.info(f"[miku_bilibili] 开始处理链接: {url_or_id[:50]}, 类型: {content_type}")
            await _handle_parse(bot, event, url_or_id, content_type)
        except Exception as e:
            logger.error(f"[miku_bilibili] 解析失败: {url_or_id[:50]} - {e}", exc_info=True)
        finally:
            # 无论成功还是失败，都添加缓存，避免短时间内重复解析
            _session_cache.add(event, url_or_id)


async def _send_video_file(bot: Bot, event: MessageEvent, file_path: str, info_title: str):
    """下载完成后自动发送视频文件（复用4种回退方式）"""
    try:
        p = Path(file_path)
        if not p.exists():
            logger.warning(f"[miku_bilibili] 自动发送失败：文件不存在 {file_path}")
            return
        file_size = p.stat().st_size
        logger.info(f"[miku_bilibili] 准备自动发送视频: {file_path}, 大小: {file_size / 1024 / 1024:.1f}MB")

        # QQ视频限制约100MB
        if file_size > 95 * 1024 * 1024:
            logger.info(f"[miku_bilibili] 视频过大({file_size / 1024 / 1024:.1f}MB)，跳过自动发送")
            return

        record_plugin_usage("miku_bilibili", user_id=str(event.user_id), command_name="自动下载发送")
        sent = False

        # 方式1: file:/// URI
        try:
            video_uri = f"file:///{file_path.replace(chr(92), '/')}"
            await bot.send(event, MessageSegment.video(video_uri))
            sent = True
            logger.info("[miku_bilibili] 自动发送方式1成功")
        except Exception as e1:
            logger.warning(f"[miku_bilibili] 自动发送方式1失败: {e1}")

        # 方式2: 本地路径
        if not sent:
            try:
                await bot.send(event, MessageSegment.video(file_path))
                sent = True
                logger.info("[miku_bilibili] 自动发送方式2成功")
            except Exception as e2:
                logger.warning(f"[miku_bilibili] 自动发送方式2失败: {e2}")

        # 方式3: base64 (<30MB)
        if not sent and file_size < 30 * 1024 * 1024:
            try:
                import base64
                with open(file_path, "rb") as f:
                    video_b64 = base64.b64encode(f.read()).decode("ascii")
                await bot.send(event, MessageSegment.video(f"base64://{video_b64}"))
                sent = True
                logger.info("[miku_bilibili] 自动发送方式3成功")
            except Exception as e3:
                logger.warning(f"[miku_bilibili] 自动发送方式3失败: {e3}")

        # 方式4: upload_file
        if not sent:
            try:
                if hasattr(bot, 'upload_file'):
                    result = await bot.upload_file(file=str(p.resolve()))
                    if result and isinstance(result, dict):
                        file_id = result.get('file_id') or result.get('file', '')
                        if file_id:
                            await bot.send(event, MessageSegment.video(file_id))
                            sent = True
                            logger.info("[miku_bilibili] 自动发送方式4成功")
            except Exception as e4:
                logger.warning(f"[miku_bilibili] 自动发送方式4失败: {e4}")

        if sent:
            logger.info(f"[miku_bilibili] 被动解析：成功解析并发送视频文件: {info_title}")
            # 60秒后删除临时发送文件（缓存文件保留）
            asyncio.get_running_loop().call_later(60, lambda: _safe_delete_file(file_path))
        else:
            logger.warning(f"[miku_bilibili] 所有自动发送方式均失败: {file_path}")
    except Exception as e:
        logger.error(f"[miku_bilibili] 自动发送视频异常: {e}", exc_info=True)


async def _auto_download_and_send(bot: Bot, event: MessageEvent, bvid: str, info_title: str):
    """后台任务：自动下载视频并发送"""
    try:
        logger.info(f"[miku_bilibili] 触发自动下载: {info_title} (BV={bvid})")
        file_path = await auto_download_video(bvid, 1)
        if file_path:
            logger.info(f"[miku_bilibili] 自动下载完成，开始发送: {info_title}")
            await _send_video_file(bot, event, file_path, info_title)
        else:
            logger.warning(f"[miku_bilibili] 自动下载失败，不发送 BV={bvid}")
    except Exception as e:
        logger.error(f"[miku_bilibili] 自动下载发送任务异常: {e}", exc_info=True)


async def _handle_parse(bot: Bot, event: MessageEvent, url_or_id: str, content_type: ContentType):
    """处理单个URL解析"""
    logger.info(f"[miku_bilibili] 开始解析: url={url_or_id[:60] if url_or_id else 'empty'}, type={content_type}")
    try:
        if content_type in (ContentType.VIDEO,):
            logger.info(f"[miku_bilibili] 解析视频URL...")
            _, resource_id = await parse_url(url_or_id)
            logger.info(f"[miku_bilibili] 解析成功, resource_id={resource_id}")
            info = await fetch_video_info(resource_id)
            logger.info(f"[miku_bilibili] 获取视频信息成功: {info.title}")
            record_plugin_usage("miku_bilibili", user_id=str(event.user_id), command_name="B站解析")

            # 先尝试卡片图片，失败则用纯文本
            sent = False
            if get_response_style() == "card":
                try:
                    from .message import render_video_card, build_video_text
                    from utils.screenshot import to_image_uri
                    img_path = await render_video_card(info)
                    if img_path:
                        try:
                            await bot.send(event, MessageSegment.image(to_image_uri(img_path)))
                            sent = True
                            logger.info(f"[miku_bilibili] 卡片图片发送成功")
                        except Exception as e1:
                            logger.warning(f"[miku_bilibili] 卡片图片发送失败: {e1}")
                except Exception as e2:
                    logger.warning(f"[miku_bilibili] 卡片渲染失败: {e2}")

            if not sent:
                # 纯文本回退
                from .message import build_video_text
                text = build_video_text(info)
                try:
                    await bot.send(event, text)
                    logger.info(f"[miku_bilibili] 纯文本发送成功")
                except Exception as e3:
                    logger.warning(f"[miku_bilibili] 纯文本发送失败: {e3}")
                    # 最后尝试：只发标题
                    try:
                        await bot.send(event, f"🎬 {info.title}\n🔗 {info.url}")
                    except Exception:
                        pass

            # 解析消息发送后，检查是否自动下载
            if is_download_enabled() and is_auto_download_enabled():
                max_dur = get_auto_download_max_duration()
                if max_dur <= 0 or info.duration <= max_dur * 60:
                    # 后台启动自动下载和发送，不阻塞当前解析
                    asyncio.create_task(
                        _auto_download_and_send(bot, event, info.bvid, info.title),
                        name=f"bili_auto_dl_{info.bvid}"
                    )
                    logger.info(f"[miku_bilibili] 已调度自动下载任务: {info.title} (BV={info.bvid})")
                else:
                    logger.info(f"[miku_bilibili] 视频时长{info.duration}秒超过自动下载限制{max_dur*60}秒，跳过自动下载: {info.title}")

        elif content_type == ContentType.LIVE:
            _, resource_id = await parse_url(url_or_id)
            info = await fetch_live_info(resource_id)
            record_plugin_usage("miku_bilibili", user_id=str(event.user_id), command_name="直播解析")

            sent = False
            if get_response_style() == "card":
                try:
                    from .message import render_live_card, build_live_text
                    from utils.screenshot import to_image_uri
                    img_path = await render_live_card(info)
                    if img_path:
                        try:
                            await bot.send(event, MessageSegment.image(to_image_uri(img_path)))
                            sent = True
                        except Exception:
                            pass
                except Exception:
                    pass

            if not sent:
                from .message import build_live_text
                try:
                    await bot.send(event, build_live_text(info))
                except Exception:
                    pass

        elif content_type == ContentType.USER:
            _, resource_id = await parse_url(url_or_id)
            info = await fetch_user_info(resource_id)
            record_plugin_usage("miku_bilibili", user_id=str(event.user_id), command_name="用户解析")

            sent = False
            if get_response_style() == "card":
                try:
                    from .message import render_user_card, build_user_text
                    from utils.screenshot import to_image_uri
                    img_path = await render_user_card(info)
                    if img_path:
                        try:
                            await bot.send(event, MessageSegment.image(to_image_uri(img_path)))
                            sent = True
                        except Exception:
                            pass
                except Exception:
                    pass

            if not sent:
                from .message import build_user_text
                try:
                    await bot.send(event, build_user_text(info))
                except Exception:
                    pass

        elif content_type == ContentType.SEASON:
            await bot.send(event, "🎬 检测到B站番剧链接，番剧解析功能开发中...")
        elif content_type == ContentType.ARTICLE:
            await bot.send(event, "📝 检测到B站专栏链接，专栏解析功能开发中...")
        elif content_type == ContentType.OPUS:
            await bot.send(event, "📢 检测到B站动态链接，动态解析功能开发中...")
    except (UrlParseError, UnsupportedUrlError) as e:
        logger.warning(f"[miku_bilibili] URL解析失败: {url_or_id[:60]} - {e}")
        try:
            await bot.send(event, f"❌ B站链接解析失败: {e}")
        except Exception:
            pass
    except Exception as e:
        logger.error(f"[miku_bilibili] 解析异常: {e}", exc_info=True)
        try:
            await bot.send(event, f"❌ B站解析出错: {type(e).__name__}: {e}")
        except Exception:
            pass


# ============================================================
# 命令：视频下载
# ============================================================

download_cmd = on_command(
    "bili下载",
    aliases={"b站下载", "B站下载", "bilibili下载"},
    priority=10,
    block=True,
)


@download_cmd.handle()
async def _download_handler(bot: Bot, event: MessageEvent, args: MessageSegment = CommandArg()):
    if not is_enabled():
        return

    if not is_download_enabled():
        await download_cmd.finish("❌ 视频下载功能已禁用")
        return

    msg = str(args).strip()
    if not msg:
        msg = await _extract_url_from_event(event)

    if not msg:
        await download_cmd.finish("❌ 用法：bili下载 <链接/BV号/AV号>\n或引用视频消息后发送此命令")
        return

    msg = msg.strip("`")

    bvid = extract_video_id(msg)
    if not bvid:
        await download_cmd.finish("❌ 未识别到有效的B站视频链接")
        return

    try:
        _, resource_id = await parse_url(bvid)
        bvid = resource_id
    except Exception:
        pass

    try:
        await download_cmd.send("📥 正在准备下载...")
        file_path = await download_video(bvid, bot, event)

        if file_path and Path(file_path).exists():
            file_size = Path(file_path).stat().st_size
            logger.info(f"[miku_bilibili] 视频文件准备发送: {file_path}, 大小: {file_size / 1024 / 1024:.1f}MB")

            # 检查文件大小 (QQ视频限制约100MB)
            if file_size > 95 * 1024 * 1024:
                await download_cmd.send(f"❌ 视频文件过大({file_size / 1024 / 1024:.1f}MB)，无法发送")
                _safe_delete_file(file_path)
                return

            record_plugin_usage("miku_bilibili", user_id=str(event.user_id), command_name="视频下载")

            # 尝试多种方式发送视频
            sent = False

            # 方式1: file:/// URI (标准方式)
            try:
                video_uri = f"file:///{file_path.replace(chr(92), '/')}"
                logger.info(f"[miku_bilibili] 尝试方式1(file_uri)发送视频: {video_uri[:100]}")
                await bot.send(event, MessageSegment.video(video_uri))
                sent = True
                logger.info("[miku_bilibili] 方式1发送成功")
            except Exception as e1:
                logger.warning(f"[miku_bilibili] 方式1(file_uri)失败: {e1}")

            # 方式2: 直接本地路径 (部分协议端支持)
            if not sent:
                try:
                    logger.info(f"[miku_bilibili] 尝试方式2(本地路径)发送视频: {file_path}")
                    await bot.send(event, MessageSegment.video(file_path))
                    sent = True
                    logger.info("[miku_bilibili] 方式2发送成功")
                except Exception as e2:
                    logger.warning(f"[miku_bilibili] 方式2(本地路径)失败: {e2}")

            # 方式3: base64 (兼容性最好但大文件可能超限)
            if not sent and file_size < 30 * 1024 * 1024:
                try:
                    import base64
                    with open(file_path, "rb") as f:
                        video_b64 = base64.b64encode(f.read()).decode("ascii")
                    logger.info(f"[miku_bilibili] 尝试方式3(base64)发送视频, 长度:{len(video_b64)}")
                    await bot.send(event, MessageSegment.video(f"base64://{video_b64}"))
                    sent = True
                    logger.info("[miku_bilibili] 方式3发送成功")
                except Exception as e3:
                    logger.warning(f"[miku_bilibili] 方式3(base64)失败: {e3}")

            # 方式4: 通过upload_file上传再发送
            if not sent:
                try:
                    logger.info(f"[miku_bilibili] 尝试方式4(upload_file)发送视频")
                    # 尝试使用协议端的upload_file接口
                    if hasattr(bot, 'upload_file'):
                        result = await bot.upload_file(file=str(Path(file_path).resolve()))
                        if result and isinstance(result, dict):
                            file_id = result.get('file_id') or result.get('file', '')
                            if file_id:
                                await bot.send(event, MessageSegment.video(file_id))
                                sent = True
                                logger.info("[miku_bilibili] 方式4发送成功")
                except Exception as e4:
                    logger.warning(f"[miku_bilibili] 方式4(upload_file)失败: {e4}")

            if not sent:
                logger.error(f"[miku_bilibili] 所有发送方式均失败, 文件: {file_path}")
                await download_cmd.send(
                    f"❌ 视频发送失败\n"
                    f"📁 文件路径: {file_path}\n"
                    f"📦 文件大小: {file_size / 1024 / 1024:.1f}MB\n"
                    f"💡 请检查协议端是否支持视频发送"
                )
            else:
                asyncio.get_running_loop().call_later(
                    60, lambda: _safe_delete_file(file_path)
                )
        else:
            logger.warning(f"[miku_bilibili] 下载未成功完成: {bvid}")
    except Exception as e:
        logger.error(f"[miku_bilibili] 下载失败: {e}", exc_info=True)
        try:
            await download_cmd.send(f"❌ 下载失败: {e}")
        except Exception:
            pass


def _safe_delete_file(file_path: str):
    try:
        p = Path(file_path)
        if p.exists():
            p.unlink()
    except Exception:
        pass


# ============================================================
# 命令：封面获取
# ============================================================

cover_cmd = on_command(
    "bili封面",
    aliases={"b站封面", "B站封面", "bilibili封面", "封面"},
    priority=10,
    block=True,
)


@cover_cmd.handle()
async def _cover_handler(bot: Bot, event: MessageEvent, args: MessageSegment = CommandArg()):
    if not is_enabled():
        return

    msg = str(args).strip()
    if not msg:
        msg = await _extract_url_from_event(event)

    if not msg:
        await cover_cmd.finish("❌ 用法：bili封面 <链接/BV号> 或引用包含视频链接的消息")
        return

    bvid = extract_video_id(msg)
    if not bvid:
        await cover_cmd.finish(f"❌ 未识别到有效的B站视频链接: {msg}")
        return

    try:
        _, resource_id = await parse_url(bvid)
        bvid = resource_id
    except Exception:
        pass

    try:
        info = await fetch_video_info(bvid)
        cover_data = await get_video_cover_data(bvid)

        if cover_data:
            record_plugin_usage("miku_bilibili", user_id=str(event.user_id), command_name="封面获取")
            await bot.send(event, f"📷 {info.title}\n{MessageSegment.image(cover_data)}")
        else:
            await cover_cmd.send("❌ 封面获取失败")
    except Exception as e:
        await cover_cmd.send(f"❌ 封面获取失败: {e}")


# ============================================================
# 命令：B站登录/状态/退出登录
# ============================================================

login_cmd = on_command(
    "bili登录",
    aliases={"B站登录", "b站登录", "bilibili登录"},
    priority=10,
    block=True,
)

status_cmd = on_command(
    "bili状态",
    aliases={"B站状态", "b站状态", "bilibili状态"},
    priority=10,
    block=True,
)

logout_cmd = on_command(
    "bili退出登录",
    aliases={"B站退出登录", "b站退出登录"},
    priority=10,
    block=True,
)


@login_cmd.handle()
async def _login_handler(bot: Bot, event: MessageEvent):
    if not is_enabled():
        return

    is_login, uname = await check_login_status()
    if is_login:
        await login_cmd.finish(f"✅ 已登录B站账号：{uname}")
        return

    driver = get_driver()
    if str(event.user_id) not in driver.config.superusers:
        await login_cmd.finish("❌ 只有超级用户可以执行此操作")
        return

    qr_url, qrcode_key = await generate_qrcode()
    if not qr_url or not qrcode_key:
        await login_cmd.finish("❌ 获取登录二维码失败\n请稍后重试或手动配置Cookie")
        return

    import urllib.parse
    qrcode_api = f"https://api.qrserver.com/v1/create-qr-code/?size=300x300&data={urllib.parse.quote(qr_url)}"
    
    import os
    from pathlib import Path as _Path
    qrcode_dir = os.path.join(DATA_DIR, "qrcode")
    os.makedirs(qrcode_dir, exist_ok=True)
    qrcode_path = os.path.join(qrcode_dir, f"bili_qrcode_{qrcode_key[:8]}.png")
    
    # 🔴 Bug 修复：二维码此前用 file:/// URI 发送（部分协议端无法打开）
    #    改为先下载到本地，再用 base64 发送（兼容性最好），失败回退本地路径 Path 对象
    try:
        async with curl_requests.AsyncSession(impersonate="chrome131", timeout=10.0) as client:
            r = await client.get(qrcode_api)
            if r.status_code == 200 and r.content:
                with open(qrcode_path, "wb") as f:
                    f.write(r.content)
    except Exception as e:
        logger.warning(f"[miku_bilibili] 下载二维码失败: {e}")

    image_segment = None
    # 方案A（推荐）：本地二维码转 base64 发送，协议端无需访问本地文件系统
    if Path(qrcode_path).exists():
        try:
            import base64 as _b64
            with open(qrcode_path, "rb") as f:
                _data = _b64.b64encode(f.read()).decode("ascii")
            image_segment = MessageSegment.image(f"base64://{_data}")
            logger.info("[miku_bilibili] 二维码已用 base64 发送")
        except Exception as e:
            logger.warning(f"[miku_bilibili] base64 发送二维码失败: {e}")
    # 方案B：直接用本地路径 Path 对象发送
    if image_segment is None:
        try:
            image_segment = MessageSegment.image(_Path(qrcode_path))
        except Exception as e:
            logger.warning(f"[miku_bilibili] 本地路径发送二维码失败: {e}")
    # 方案C：兜底发在线二维码 API
    if image_segment is None:
        image_segment = MessageSegment.image(qrcode_api)

    await login_cmd.send(
        f"🔐 B站扫码登录\n\n"
        f"请使用B站APP扫描下方二维码登录\n"
        f"⏰ 二维码有效时间：180秒\n\n"
        f"{image_segment}\n\n"
        f"💡 登录成功后会自动保存凭证"
    )

    poll_count = 0
    max_poll = 36
    scanned = False

    while poll_count < max_poll:
        await asyncio.sleep(5)
        poll_count += 1

        try:
            status_code, status_msg, cookies = await poll_qrcode_status(qrcode_key)

            if status_code == 2:
                if cookies:
                    save_cookies(cookies)
                    logger.info(f"[miku_bilibili] 保存了 {len(cookies)} 个Cookie")
                else:
                    logger.warning("[miku_bilibili] 登录成功但未获取到Cookie")

                is_login, uname = await check_login_status()
                if is_login:
                    await bot.send(event, f"✅ 登录成功！\n👤 已登录：{uname}\n💡 现可下载720P+高清视频")
                else:
                    await bot.send(event, "⚠️ Cookie已保存，但登录验证失败\n请重新尝试")
                return

            elif status_code == 1:
                if not scanned:
                    scanned = True
                    await bot.send(event, "📱 已扫码，请在手机上确认登录")

            elif status_code == 3:
                await bot.send(event, "❌ 二维码已过期，请重新发送 bili登录")
                return

            elif status_code == 4:
                await bot.send(event, "❌ 用户取消登录")
                return

            if poll_count % 6 == 0 and status_code == 0:
                remaining = 180 - poll_count * 5
                await bot.send(event, f"⏳ 等待扫码中...（剩余 {remaining} 秒）")

        except Exception as e:
            logger.warning(f"[miku_bilibili] 检查登录状态失败: {e}")
            continue

    await bot.send(event, "❌ 登录超时，请重新发送 bili登录")


@status_cmd.handle()
async def _status_handler(bot: Bot, event: MessageEvent):
    if not is_enabled():
        return

    is_login, uname = await check_login_status()

    if is_login:
        try:
            async with curl_requests.AsyncSession(impersonate="chrome131", timeout=get_request_timeout(), headers=get_headers_with_cookie()) as client:
                r = await client.get("https://api.bilibili.com/x/web-interface/nav/stat")
                data = r.json()
                if data.get("code") == 0:
                    stat = data.get("data", {})
                    text = (
                        f"✅ B站已登录\n"
                        f"👤 用户名：{uname}\n"
                        f"🎬 关注：{stat.get('following', 0)}\n"
                        f"👥 粉丝：{stat.get('follower', 0)}\n"
                        f"📺 动态：{stat.get('dynamic_count', 0)}"
                    )
                    await status_cmd.finish(text)
                    return
        except Exception as e:
            logger.warning(f"[miku_bilibili] 获取用户统计失败: {e}")

        await status_cmd.finish(f"✅ B站已登录\n👤 用户名：{uname}")
    else:
        await status_cmd.finish("❌ 未登录B站\n发送 bili登录 查看登录方式")


# 命令：查看统一凭证（b站凭证）
cred_cmd = on_command(
    "b站凭证",
    aliases={"B站凭证", "bili凭证", "bilibili凭证"},
    priority=10,
    block=True,
)


@cred_cmd.handle()
async def _cred_handler(event: MessageEvent):
    if not is_enabled():
        return

    is_login, uname = await check_login_status()

    if not is_login:
        await cred_cmd.finish(
            "❌ 未登录B站\n"
            "请发送 b站登录 扫码获取凭证，或 bili设置Cookie 手动设置"
        )

    cred = get_credential()
    # 🔒 安全：绝不明文输出完整 SESSDATA，只显示末 4 位 + 掩码
    sessdata = cred.get("SESSDATA") or cred.get("sessdata") or ""
    uid = cred.get("uid") or cred.get("DedeUserID") or "未知"
    bili_jct = cred.get("bili_jct") or ""

    def _mask(v: str) -> str:
        return f"••••{v[-4:]}" if v and len(v) >= 4 else "（空）"

    await cred_cmd.finish(
        "\n".join([
            "✅ B站凭证信息",
            f"👤 用户名：{uname}",
            f"🆔 UID：{uid}",
            f"🔑 SESSDATA：{_mask(sessdata)}",
            f"🔑 bili_jct：{_mask(bili_jct)}",
            "",
            "💡 凭证已持久化到 config/bot.yaml，重启无需重新扫码",
            "💡 「查成分」命令将复用此凭证",
        ])
    )


@logout_cmd.handle()
async def _logout_handler(bot: Bot, event: MessageEvent):
    if not is_enabled():
        return

    driver = get_driver()
    if str(event.user_id) not in driver.config.superusers:
        await logout_cmd.finish("❌ 只有超级用户可以执行此操作")
        return

    clear_cookies()
    await logout_cmd.send("✅ 已退出B站登录")


cookie_login_cmd = on_command(
    "bili设置Cookie",
    aliases={"B站设置Cookie", "b站设置Cookie", "biliCookie", "bili_cookie"},
    priority=10,
    block=True,
)


@cookie_login_cmd.handle()
async def _cookie_login_handler(bot: Bot, event: MessageEvent, args: Message = CommandArg()):
    if not is_enabled():
        return

    driver = get_driver()
    if str(event.user_id) not in driver.config.superusers:
        await cookie_login_cmd.finish("❌ 只有超级用户可以执行此操作")
        return

    cookie_text = args.extract_plain_text().strip()
    if not cookie_text:
        await cookie_login_cmd.finish(
            "💡 手动设置Cookie登录\n\n"
            "使用方法：\n"
            "bili设置Cookie SESSDATA=xxx; bili_jct=xxx; buvid3=xxx\n\n"
            "获取Cookie方法：\n"
            "1. 浏览器打开 bilibili.com 并登录\n"
            "2. 按F12打开开发者工具\n"
            "3. 切换到Application(应用程序)标签\n"
            "4. 左侧找到Cookies → https://www.bilibili.com\n"
            "5. 复制 SESSDATA、bili_jct、buvid3 等的值\n"
            "6. 按格式发送给机器人"
        )
        return

    try:
        cookies = cookies_str_to_dict(cookie_text)
        if not cookies:
            await cookie_login_cmd.finish("❌ Cookie格式错误\n请使用格式：key1=value1; key2=value2")
            return

        save_cookies(cookies)

        is_login, uname = await check_login_status()
        if is_login:
            await cookie_login_cmd.finish(
                f"✅ Cookie设置成功！\n"
                f"👤 已登录：{uname}\n"
                f"💡 共保存 {len(cookies)} 个Cookie"
            )
        else:
            await cookie_login_cmd.finish(
                f"⚠️ Cookie已保存（{len(cookies)}个），但登录验证失败\n"
                f"请检查Cookie是否正确且未过期"
            )
    except Exception as e:
        logger.warning(f"[miku_bilibili] 设置Cookie失败: {e}")
        await cookie_login_cmd.finish(f"❌ 设置Cookie失败: {e}")


# ============================================================
# 命令：群组开关控制
# ============================================================

toggle_cmd = on_command(
    "开启群被动b站解析",
    aliases={"关闭群被动b站解析", "开启b站解析", "关闭b站解析"},
    priority=10,
    block=True,
)


@toggle_cmd.handle()
async def _toggle_handler(bot: Bot, event: MessageEvent):
    if not is_enabled():
        return

    if not isinstance(event, GroupMessageEvent):
        await toggle_cmd.finish("❌ 此指令仅限群聊使用")
        return

    is_admin = False
    try:
        member_info = await bot.get_group_member_info(
            group_id=event.group_id,
            user_id=event.user_id
        )
        if member_info.get("role") in ("owner", "admin"):
            is_admin = True
    except Exception:
        pass

    driver = get_driver()
    if str(event.user_id) in driver.config.superusers:
        is_admin = True

    if not is_admin:
        await toggle_cmd.finish("❌ 只有群管理员可以执行此操作")
        return

    cmd_text = str(event.get_message()).strip()
    clean_text = cmd_text
    for prefix in ("/", "!", "！"):
        if clean_text.startswith(prefix):
            clean_text = clean_text[len(prefix):].lstrip()
            break

    if clean_text.startswith("开启"):
        _set_group_enabled(event.group_id, True)
        await toggle_cmd.send("✅ 已开启本群B站被动解析")
    else:
        _set_group_enabled(event.group_id, False)
        await toggle_cmd.send("✅ 已关闭本群被动解析")


# ============================================================
# 命令：超级用户启停控制
# ============================================================

bili_toggle_cmd = on_command(
    "bili启停",
    aliases={"b站启停", "B站启停", "bilibili启停"},
    priority=10,
    block=True,
    permission=SUPERUSER,
)


@bili_toggle_cmd.handle()
async def _bili_toggle_handler(event: MessageEvent, args: MessageSegment = CommandArg()):
    if not is_enabled():
        return

    if isinstance(event, GroupMessageEvent):
        await bili_toggle_cmd.finish("❌ 此指令仅限私聊使用，请在私聊中发送")
        return

    arg_str = str(args).strip()
    if not arg_str or not arg_str.isdigit():
        await bili_toggle_cmd.finish(
            "❌ 用法：bili启停 <群号>\n\n"
            "示例：bili启停 123456789\n\n"
            "💡 此指令用于切换指定群的B站被动解析开关"
        )
        return

    group_id = arg_str
    current = _is_group_enabled(int(group_id))
    new_state = not current
    _set_group_enabled(int(group_id), new_state)

    status = "开启" if new_state else "关闭"
    await bili_toggle_cmd.finish(f"✅ 已{status}群 {group_id} 的B站被动解析")


# ============================================================
# B站订阅命令
# ============================================================

sub_add_cmd = on_command("B站订阅添加", aliases={"b站订阅添加", "bili订阅添加"}, priority=10, block=True)
sub_del_cmd = on_command("B站订阅删除", aliases={"b站订阅删除", "bili订阅删除"}, priority=10, block=True)
sub_list_cmd = on_command("B站订阅列表", aliases={"b站订阅列表", "bili订阅列表", "B站订阅"}, priority=10, block=True)
sub_config_cmd = on_command("B站订阅设置", aliases={"b站订阅设置", "bili订阅设置"}, priority=10, block=True)
sub_clear_cmd = on_command("B站订阅清空", aliases={"b站订阅清空", "bili订阅清空"}, priority=10, block=True)
checkall_cmd = on_command("bili检查全部", aliases={"b站检查全部", "检测b站"}, priority=10, block=True)


@sub_add_cmd.handle()
async def _sub_add_handler(event: MessageEvent, args: MessageSegment = CommandArg()):
    if not is_enabled() or not is_subscribe_enabled():
        await sub_add_cmd.finish("❌ B站订阅功能未启用")
        return

    session_key = _get_session_key(event)
    arg_str = str(args).strip()

    if not arg_str:
        await sub_add_cmd.finish(
            "📝 添加订阅\n\n"
            "用法：B站订阅添加 <类型> <ID>\n\n"
            "类型：\n"
            "  UP   - UP主（UID）\n"
            "  主播 - 直播间（房间号）\n"
            "  番剧 - 番剧（Season ID 或名称）\n\n"
            "示例：\n"
            "  B站订阅添加 UP 732482333\n"
            "  B站订阅添加 主播 21452505\n"
            "  B站订阅添加 番剧 葬送的芙莉莲\n"
            "  B站订阅添加 番剧 ss12345"
        )
        return

    parts = arg_str.split(None, 1)
    if len(parts) < 2:
        await sub_add_cmd.finish("❌ 格式错误，请使用：B站订阅添加 <类型> <ID>")
        return

    sub_type_str = parts[0].lower()
    target = parts[1].strip()

    is_live = False
    if sub_type_str in ("up", "UP主"):
        if not target.isdigit():
            await sub_add_cmd.finish("❌ UP主UID必须是数字")
            return
    elif sub_type_str in ("主播", "live", "直播"):
        if not target.isdigit():
            await sub_add_cmd.finish("❌ 直播间房间号必须是数字")
            return
        is_live = True
    elif sub_type_str in ("番剧", "bangumi", "season"):
        pass
    else:
        await sub_add_cmd.finish("❌ 未知类型，请使用：UP / 主播 / 番剧")
        return

    await sub_add_cmd.send("⏳ 正在处理订阅请求...")
    result = await add_subscription_by_id(target, session_key, is_live=is_live)
    await sub_add_cmd.finish(result)


@sub_del_cmd.handle()
async def _sub_del_handler(event: MessageEvent, args: MessageSegment = CommandArg()):
    if not is_enabled() or not is_subscribe_enabled():
        await sub_del_cmd.finish("❌ B站订阅功能未启用")
        return

    session_key = _get_session_key(event)
    arg_str = str(args).strip()

    if not arg_str or not arg_str.isdigit():
        await sub_del_cmd.finish("❌ 请提供订阅ID（数字）\n使用 B站订阅列表 查看订阅ID")
        return

    sub_id = int(arg_str)
    result = delete_subscription_from_target(sub_id, session_key)
    await sub_del_cmd.finish(result)


@sub_list_cmd.handle()
async def _sub_list_handler(event: MessageEvent):
    if not is_enabled() or not is_subscribe_enabled():
        await sub_list_cmd.finish("❌ B站订阅功能未启用")
        return

    session_key = _get_session_key(event)
    sub_list = list_subscriptions(session_key)

    if not sub_list:
        await sub_list_cmd.finish("📭 当前会话没有任何订阅")
        return

    from .model import SubType

    msg_lines = ["📋 B站订阅列表\n"]
    for sub in sorted(sub_list, key=lambda x: x.id):
        type_name = {"up": "UP主", "live": "直播", "season": "番剧"}.get(sub.sub_type.value, "未知")
        msg_lines.append(f"[{sub.id}] {type_name} | {sub.name}")

        if sub.sub_type == SubType.UP:
            room_info = f" | 直播间: {sub.room_id}" if sub.room_id else ""
            msg_lines.append(f"   UID: {sub.target_id}{room_info}")
            flags = []
            if sub.enable_dynamic: flags.append("动态")
            if sub.enable_video: flags.append("视频")
            if sub.enable_live: flags.append("直播")
            at_flags = []
            if sub.at_all_dynamic: at_flags.append("动态@")
            if sub.at_all_video: at_flags.append("视频@")
            if sub.at_all_live: at_flags.append("直播@")
            msg_lines.append(f"   推送: {', '.join(flags) or '无'}" + (f" | @全体: {', '.join(at_flags)}" if at_flags else ""))
        elif sub.sub_type == SubType.LIVE:
            msg_lines.append(f"   房间号: {sub.target_id}")
            msg_lines.append(f"   推送: {'直播' if sub.enable_live else '无'}" + (" | @全体" if sub.at_all_live else ""))
        elif sub.sub_type == SubType.SEASON:
            msg_lines.append(f"   Season ID: {sub.target_id}")
            msg_lines.append(f"   推送: {'剧集' if sub.enable_video else '无'}" + (" | @全体" if sub.at_all_video else ""))

        msg_lines.append("")

    msg_lines.append("💡 使用 B站订阅删除 <ID> 删除订阅")
    msg_lines.append("💡 使用 B站订阅设置 <ID> <选项> 修改推送设置")

    await sub_list_cmd.finish("\n".join(msg_lines))


@sub_config_cmd.handle()
async def _sub_config_handler(event: MessageEvent, args: MessageSegment = CommandArg()):
    if not is_enabled() or not is_subscribe_enabled():
        await sub_config_cmd.finish("❌ B站订阅功能未启用")
        return

    session_key = _get_session_key(event)
    arg_str = str(args).strip()

    if not arg_str:
        await sub_config_cmd.finish(
            "📝 订阅设置\n\n"
            "用法：B站订阅设置 <ID> <选项>\n\n"
            "选项：\n"
            "  +动态 / -动态    开启/关闭动态推送\n"
            "  +视频 / -视频    开启/关闭视频推送\n"
            "  +直播 / -直播    开启/关闭直播推送\n"
            "  +全部 / -全部    开启/关闭全部推送\n"
            "  +@动态 / -@动态  动态推送@全体\n"
            "  +@视频 / -@视频  视频推送@全体\n"
            "  +@直播 / -@直播  直播推送@全体\n\n"
            "示例：B站订阅设置 1 +直播 +@直播"
        )
        return

    parts = arg_str.split()
    sub_id = None
    settings = []
    for p in parts:
        if p.isdigit() and sub_id is None:
            sub_id = int(p)
        else:
            settings.append(p)

    if sub_id is None:
        await sub_config_cmd.finish("❌ 请提供订阅ID")
        return

    updates = {}
    from .model import SubType

    sub_list = list_subscriptions(session_key)
    sub = None
    for s in sub_list:
        if s.id == sub_id:
            sub = s
            break

    if not sub:
        await sub_config_cmd.finish(f"❌ 未找到订阅ID {sub_id}")
        return

    for setting in settings:
        if not setting:
            continue
        enable = setting.startswith("+")
        if not enable and not setting.startswith("-"):
            continue

        key = setting[1:]

        if key in ("动态", "dynamic") and sub.sub_type == SubType.UP:
            updates["enable_dynamic"] = enable
        elif key in ("视频", "video", "剧集"):
            updates["enable_video"] = enable
        elif key in ("直播", "live"):
            updates["enable_live"] = enable
        elif key in ("全部", "all"):
            updates["enable_dynamic"] = enable
            updates["enable_video"] = enable
            updates["enable_live"] = enable
        elif key in ("@动态", "at:dynamic") and sub.sub_type == SubType.UP:
            updates["at_all_dynamic"] = enable
        elif key in ("@视频", "at:video"):
            updates["at_all_video"] = enable
        elif key in ("@直播", "at:live"):
            updates["at_all_live"] = enable
        elif key in ("@全部", "at:all"):
            updates["at_all_dynamic"] = enable
            updates["at_all_video"] = enable
            updates["at_all_live"] = enable

    result = update_sub_config(sub_id, session_key, updates)
    await sub_config_cmd.finish(result)


@sub_clear_cmd.handle()
async def _sub_clear_handler(event: MessageEvent):
    if not is_enabled() or not is_subscribe_enabled():
        await sub_clear_cmd.finish("❌ B站订阅功能未启用")
        return

    session_key = _get_session_key(event)
    sub_list = list_subscriptions(session_key)

    if not sub_list:
        await sub_clear_cmd.finish("📭 当前会话没有任何订阅")
        return

    count = len(sub_list)
    for sub in sub_list:
        delete_subscription_from_target(sub.id, session_key)

    await sub_clear_cmd.finish(f"✅ 已清空当前会话的 {count} 个订阅")


@checkall_cmd.handle()
async def _checkall_handler(bot: Bot, event: MessageEvent):
    if not is_enabled() or not is_subscribe_enabled():
        await checkall_cmd.finish("❌ B站订阅功能未启用")
        return

    driver = get_driver()
    if str(event.user_id) not in driver.config.superusers:
        await checkall_cmd.finish("❌ 只有超级用户可以执行此操作")
        return

    await checkall_cmd.send("⏳ 开始检查所有订阅...")
    await check_all_subscriptions()
    await checkall_cmd.finish("✅ 订阅检查完成！")


# ============================================================
# 菜单注册
# ============================================================

register_plugin_info(
    "miku_bilibili",
    name="B站解析+成分姬",
    icon="📺",
    order=8,
    description="B站内容解析（视频/直播/专栏/用户空间）+ 订阅推送 + VTuber成分查询（原成分姬已并入）",
    commands=[
        "bili下载", "b站下载",
        "bili封面", "b站封面",
        "bili登录", "bili状态", "bili退出登录", "bili设置Cookie", "b站凭证",
        "B站订阅添加", "B站订阅删除", "B站订阅列表", "B站订阅设置", "B站订阅清空",
        "bili检查全部",
        "开启群被动b站解析", "关闭群被动b站解析",
        "bili启停",
        "查成分",
    ],
    usage="""📺 B站解析+成分姬

【被动解析】
发送B站链接自动解析视频/直播/用户信息

【视频下载】
  bili下载 <链接/BV号>
  💡 登录后可下载720P+高清视频

【获取封面】
  bili封面 <链接/BV号>

【成分查询】
  查成分 <B站用户名/UID> - 查询关注列表的VTuber成分
  💡 复用统一B站凭证，未登录先发 b站登录

【账号管理】（超级用户）
  bili登录 - 扫码登录B站账号
  bili设置Cookie - 手动输入Cookie登录
  bili状态 - 查看当前登录状态
  bili退出登录 - 退出登录

【B站订阅】
  B站订阅添加 <类型> <ID>
    类型: UP(UID) / 主播(房间号) / 番剧(ss ID或名称)
    示例: B站订阅添加 UP 732482333
    示例: B站订阅添加 主播 21452505
    示例: B站订阅添加 番剧 ss12345
    示例: B站订阅添加 番剧 葬送的芙莉莲

  B站订阅列表 - 查看当前会话的订阅
  B站订阅删除 <ID> - 删除指定订阅
  B站订阅设置 <ID> <选项>
    选项: +动态/-动态 +视频/-视频 +直播/-直播 +全部/-全部
         +@动态/-@动态 +@视频/-@视频 +@直播/-@直播
    示例: B站订阅设置 1 +直播 +@直播
  B站订阅清空 - 清空当前会话的所有订阅

【超级用户】
  bili检查全部 - 立即检查所有订阅更新
  bili启停 <群号> - 私聊切换指定群的B站被动解析开关

【群组控制】（管理员）
  开启群被动b站解析
  关闭群被动b站解析
""",
)


# ============================================================
# 初始化
# ============================================================

init_db()
logger.info("[miku_bilibili] B站解析插件已加载")


# ============================================================
# 订阅定时检查任务
# ============================================================

try:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.triggers.interval import IntervalTrigger

    _sub_scheduler = None
    _sub_driver = get_driver()

    @_sub_driver.on_bot_connect
    async def _start_sub_check():
        global _sub_scheduler
        if _sub_scheduler is not None and _sub_scheduler.running:
            return

        if not is_subscribe_enabled():
            return

        interval = get_subscribe_interval()
        _sub_scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")
        _sub_scheduler.add_job(
            check_all_subscriptions,
            trigger=IntervalTrigger(minutes=max(1, interval)),
            id="bili_sub_check",
            name="B站订阅检查",
            max_instances=1,
            coalesce=True,
            misfire_grace_time=60,
            replace_existing=True,
        )
        _sub_scheduler.start()
        logger.info(f"[miku_bilibili] 订阅定时检查已启动，间隔 {max(1, interval)} 分钟")
except ImportError:
    logger.warning("[miku_bilibili] APScheduler 未安装，订阅定时检查不可用")
