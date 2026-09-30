"""
Miku B站插件 - 下载服务模块
视频下载功能
参考: nonebot-plugin-parser-lite 的下载架构
支持双下载引擎：
  1. 原生 B站 API 下载（默认优先）
  2. yt-dlp 备用方案（支持更高兼容性，自动回退）
"""

import re
import asyncio
from pathlib import Path
from typing import Optional, Tuple, Dict

from curl_cffi import requests as curl_requests
from nonebot.adapters.onebot.v11 import Bot, MessageEvent
from nonebot.log import logger

from .config import (
    DOWNLOAD_DIR, get_download_quality, get_max_download_duration,
    get_request_timeout, CACHE_DIR, DATA_DIR,
)
from .credential import is_logged_in, get_headers_with_cookie, get_cookies_dict
from .api import fetch_video_info, fetch_video_download_url
from .model import VideoInfo, DownloadError


def safe_filename(name: str) -> str:
    """生成安全的文件名"""
    name = re.sub(r'[\\/:*?"<>|]', '_', name)
    name = name.strip()
    if len(name) > 50:
        name = name[:50]
    return name


def format_file_size(size_bytes: int) -> str:
    """格式化文件大小"""
    if size_bytes >= 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f}MB"
    elif size_bytes >= 1024:
        return f"{size_bytes / 1024:.1f}KB"
    return f"{size_bytes}B"


# 视频缓存目录（自动下载视频缓存到此处）
VIDEO_CACHE_DIR = CACHE_DIR / "video_cache"
VIDEO_CACHE_DIR.mkdir(parents=True, exist_ok=True)

# 自动下载并发信号量（不同视频之间最多 2 个并发）
_auto_download_semaphore = asyncio.Semaphore(2)

# 进行中下载任务表（key: "BV号_P页码"）
# 防止同一视频被并发重复下载时，多个 yt-dlp 进程写同一个 .part 临时文件导致文件损坏
_inflight_downloads: Dict[str, "asyncio.Task"] = {}


# ============================================================
# ffmpeg 工具
# ============================================================
_ffmpeg_available: Optional[bool] = None


async def _has_ffmpeg() -> bool:
    """检查系统是否安装了ffmpeg"""
    global _ffmpeg_available
    if _ffmpeg_available is not None:
        return _ffmpeg_available
    try:
        proc = await asyncio.create_subprocess_shell(
            "ffmpeg -version",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.communicate()
        _ffmpeg_available = proc.returncode == 0
        if _ffmpeg_available:
            logger.info("[miku_bilibili] 检测到ffmpeg，支持DASH音视频合并")
        else:
            logger.warning("[miku_bilibili] 未检测到ffmpeg，DASH格式将无法合并音频")
    except Exception:
        _ffmpeg_available = False
        logger.warning("[miku_bilibili] ffmpeg检测失败，DASH格式将无法合并音频")
    return _ffmpeg_available


async def _merge_av(v_path: Path, a_path: Path, output_path: Path) -> bool:
    """使用ffmpeg合并视频和音频流"""
    try:
        cmd = (
            f'ffmpeg -y -hide_banner -loglevel error '
            f'-i "{v_path}" -i "{a_path}" '
            f'-c:v copy -c:a copy '
            f'-map 0:v:0 -map 1:a:0 '
            f'-movflags +faststart "{output_path}"'
        )
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            error_msg = stderr.decode(errors="ignore").strip()
            logger.error(f"[miku_bilibili] ffmpeg合并失败: {error_msg}")
            return False
        logger.info(f"[miku_bilibili] ffmpeg合并成功: {output_path.name}")
        return True
    except Exception as e:
        logger.error(f"[miku_bilibili] ffmpeg执行异常: {e}")
        return False


# ============================================================
# 下载核心
# ============================================================
async def download_video_file(
    download_url: str,
    save_path: Path,
) -> bool:
    """
    下载视频文件
    返回是否成功
    """
    try:
        download_headers = get_headers_with_cookie()
        download_headers["Referer"] = "https://www.bilibili.com/"

        logger.info(f"[miku_bilibili] 开始下载: {download_url[:80]}...")

        async with curl_requests.AsyncSession(
            impersonate="chrome131",
            timeout=(10.0, 300.0),
            headers=download_headers,
            allow_redirects=True,  # 关键：B站CDN会重定向
        ) as client:
            async with client.stream("GET", download_url) as response:
                response.raise_for_status()
                save_path.parent.mkdir(parents=True, exist_ok=True)
                total = 0
                with open(save_path, "wb") as f:
                    async for chunk in response.aiter_bytes(chunk_size=1024 * 1024):
                        f.write(chunk)
                        total += len(chunk)
                logger.info(f"[miku_bilibili] 下载完成，大小: {format_file_size(total)}")
        return save_path.exists() and save_path.stat().st_size > 0
    except Exception as e:
        logger.error(f"[miku_bilibili] 下载视频失败: {e}")
        if save_path.exists():
            try:
                save_path.unlink()
            except Exception:
                pass
        return False


async def get_download_url(bvid: str) -> Tuple[Optional[str], Optional[str], str]:
    """
    获取视频下载链接
    返回: (视频下载URL, 音频下载URL(None表示无独立音频), 画质描述)
    参考: nonebot-plugin-parser-lite 的 VideoDownloadURLDataDetecter
    """
    requested_quality = get_download_quality()
    logged_in = is_logged_in()

    if not logged_in and requested_quality > 32:
        requested_quality = 32
        logger.info("[miku_bilibili] 未登录，限制最高画质为480P")

    # 优先尝试MP4格式(fnval=16)，单文件含音频，无需ffmpeg合并
    # 如果MP4失败，再尝试DASH格式(fnval=4048)，需分别下载视频和音频
    for fnval in [16, 4048]:
        try:
            data = await fetch_video_download_url(bvid, qn=requested_quality, fnval=fnval)

            accept_quality = data.get("accept_quality", [])
            accept_description = data.get("accept_description", [])
            actual_quality = data.get("quality", 0)

            quality_desc = "未知"
            if accept_quality and accept_description:
                try:
                    idx = accept_quality.index(actual_quality)
                    if idx < len(accept_description):
                        quality_desc = accept_description[idx]
                except ValueError:
                    quality_desc = f"{actual_quality}p"

            if fnval == 16:
                # MP4格式：durl列表，单文件含音频
                durl_list = data.get("durl", [])
                if durl_list:
                    download_url = durl_list[0].get("url", "")
                    backup_url = durl_list[0].get("backup_url", [])
                    if not download_url and backup_url:
                        download_url = backup_url[0]
                    if download_url:
                        logger.info(f"[miku_bilibili] 获取MP4下载链接成功 (画质: {quality_desc})")
                        return download_url, None, quality_desc
                logger.debug("[miku_bilibili] MP4格式未获取到下载链接，尝试DASH")
            else:
                # DASH格式：dash.video和dash.audio分开
                # 修复：之前代码用 data.get("video") 是错误的，应为 data.get("dash", {}).get("video")
                dash_data = data.get("dash", {})
                if not dash_data:
                    logger.debug("[miku_bilibili] DASH数据为空")
                    continue

                video_list = dash_data.get("video", [])
                audio_list = dash_data.get("audio", [])

                if not video_list:
                    logger.debug("[miku_bilibili] DASH视频流为空")
                    continue

                # 选择第一个视频流（通常是最高画质）
                video_url = video_list[0].get("base_url", "")
                video_backup = video_list[0].get("backup_url", [])
                if not video_url and video_backup:
                    video_url = video_backup[0]

                # 选择第一个音频流
                audio_url = None
                if audio_list:
                    audio_url = audio_list[0].get("base_url", "")
                    audio_backup = audio_list[0].get("backup_url", [])
                    if not audio_url and audio_backup:
                        audio_url = audio_backup[0]

                if video_url:
                    has_audio = "有" if audio_url else "无"
                    logger.info(f"[miku_bilibili] 获取DASH下载链接成功 (画质: {quality_desc}, 音频: {has_audio})")
                    return video_url, audio_url, quality_desc

        except Exception as e:
            logger.debug(f"[miku_bilibili] 获取下载链接失败 (fnval={fnval}): {e}")
            continue

    return None, None, ""


async def download_video(
    bvid: str,
    bot: Bot,
    event: MessageEvent,
) -> Optional[str]:
    """
    下载视频，返回文件路径
    失败返回None
    """
    try:
        info = await fetch_video_info(bvid)
        logger.info(f"[miku_bilibili] 视频信息: {info.title}, 时长: {info.duration}秒")

        max_duration = get_max_download_duration()
        if max_duration > 0 and info.duration > max_duration * 60:
            await bot.send(event, f"❌ 视频时长超过{max_duration}分钟，暂不支持下载")
            return None

        video_url, audio_url, quality_desc = await get_download_url(bvid)
        if not video_url:
            raise DownloadError("无法获取下载链接")

        login_hint = "" if is_logged_in() else "（未登录，最高480P）"

        if audio_url:
            # DASH格式：需要下载视频和音频后合并
            await bot.send(event, f"📥 正在下载: {info.title}\n🎬 画质: {quality_desc}{login_hint}\n⏳ DASH格式，需合并音视频...")

            filename = f"{info.bvid}_{safe_filename(info.title)}"
            video_path = DOWNLOAD_DIR / f"{filename}-video.m4s"
            audio_path = DOWNLOAD_DIR / f"{filename}-audio.m4s"
            output_path = DOWNLOAD_DIR / f"{filename}.mp4"

            # 并行下载视频和音频
            logger.info(f"[miku_bilibili] 开始并行下载 2 个视频媒体流...")
            logger.info(f"[miku_bilibili] 开始下载文件: {video_path.name} (使用 AsyncHttpx)")
            v_task = download_video_file(video_url, video_path)
            logger.info(f"[miku_bilibili] 开始下载文件: {audio_path.name} (使用 AsyncHttpx)")
            a_task = download_video_file(audio_url, audio_path)
            v_ok, a_ok = await asyncio.gather(v_task, a_task)

            if not v_ok:
                raise DownloadError("视频流下载失败")
            if not a_ok:
                logger.warning("[miku_bilibili] 音频流下载失败，将只保留视频")

            if a_ok and await _has_ffmpeg():
                # 使用ffmpeg合并
                await bot.send(event, "🔄 正在合并音视频...")
                logger.info(f"[miku_bilibili] 尝试快速合并 (stream copy)...")
                success = await _merge_av(video_path, audio_path, output_path)
                # 清理临时文件
                for p in [video_path, audio_path]:
                    try:
                        p.unlink()
                    except Exception:
                        pass
                if not success:
                    # 合并失败，使用视频文件
                    logger.warning("[miku_bilibili] 合并失败，使用纯视频文件")
                    video_path.rename(output_path)
                else:
                    logger.info(f"[miku_bilibili] 媒体文件处理成功: {output_path.name}")
                    logger.info(f"[miku_bilibili] 视频处理成功: {output_path.name}")
            elif a_ok:
                # 无ffmpeg，尝试直接用视频文件
                logger.warning("[miku_bilibili] 无ffmpeg，DASH格式只能保存纯视频(无音频)")
                video_path.rename(output_path)
            else:
                video_path.rename(output_path)

        else:
            # MP4格式：单文件含音频，直接下载
            await bot.send(event, f"📥 正在下载: {info.title}\n🎬 画质: {quality_desc}{login_hint}\n⏳ 请稍候...")

            filename = f"{info.bvid}_{safe_filename(info.title)}.mp4"
            output_path = DOWNLOAD_DIR / filename

            success = await download_video_file(video_url, output_path)
            if not success:
                raise DownloadError("视频下载失败")

        final_size = output_path.stat().st_size
        size_str = format_file_size(final_size)
        await bot.send(event, f"✅ 下载完成（{size_str}），正在发送视频...")

        return str(output_path)

    except DownloadError as de:
        # 原生下载失败，尝试 yt-dlp 回退
        logger.warning(f"[miku_bilibili] 原生下载失败，尝试 yt-dlp 回退: {de}")
        fallback = await _ytdlp_download_bili(bvid, bot, event)
        if fallback:
            return fallback
        raise
    except Exception as e:
        logger.error(f"[miku_bilibili] 视频下载失败: {e}", exc_info=True)
        # 回退到 yt-dlp
        try:
            fallback = await _ytdlp_download_bili(bvid, bot, event)
            if fallback:
                return fallback
        except Exception as fb_err:
            logger.warning(f"[miku_bilibili] yt-dlp 回退也失败: {fb_err}")
        raise DownloadError(f"下载失败: {e}")


async def get_video_cover_data(bvid: str) -> Optional[str]:
    """
    获取视频封面（返回data URI）
    """
    try:
        info = await fetch_video_info(bvid)
        if not info.pic:
            return None

        headers = get_headers_with_cookie()
        headers["Referer"] = "https://www.bilibili.com/"

        async with curl_requests.AsyncSession(impersonate="chrome131", timeout=get_request_timeout(), headers=headers, allow_redirects=True) as client:
            r = await client.get(info.pic)
            r.raise_for_status()
            import base64
            content_type = r.headers.get("content-type", "image/jpeg")
            b64 = base64.b64encode(r.content).decode("ascii")
            return f"data:{content_type};base64,{b64}"
    except Exception as e:
        logger.warning(f"[miku_bilibili] 封面获取失败: {e}")
        return None


# ============================================================
# 自动下载（解析成功后后台静默下载，不发送中间消息）
# ============================================================

async def auto_download_video(bvid: str, page: int = 1) -> Optional[str]:
    """
    解析成功后自动下载视频（后台静默模式）- 带并发去重
    - 同一视频（BV号+页码）并发请求时只下载一次，其余调用者等待并复用结果
    - 不同视频之间使用信号量控制并发
    - 下载到 VIDEO_CACHE_DIR，文件名用 BV号_P页码.mp4
    - 命中缓存直接返回路径
    - 失败返回 None
    """
    key = f"{bvid}_P{page}"
    existing = _inflight_downloads.get(key)
    if existing is not None and not existing.done():
        logger.info(f"[miku_bilibili] BV{bvid} P{page} 正在下载中，等待复用进行中的下载结果")
        try:
            return await existing
        except Exception:
            return None

    task = asyncio.create_task(_auto_download_video_impl(bvid, page))
    _inflight_downloads[key] = task
    try:
        return await task
    finally:
        if _inflight_downloads.get(key) is task:
            _inflight_downloads.pop(key, None)


async def _auto_download_video_impl(bvid: str, page: int = 1) -> Optional[str]:
    """
    实际执行自动下载（仅供 auto_download_video 内部调用）
    - 使用信号量控制并发
    - 下载到 VIDEO_CACHE_DIR，文件名用 BV号_P页码.mp4
    - 命中缓存直接返回路径
    - 失败返回 None
    """
    logger.info(f"[miku_bilibili] 任务: 自动下载视频, 等待信号量... (BV={bvid}, P{page})")

    async with _auto_download_semaphore:
        logger.info(f"[miku_bilibili] 信号量已获取，开始处理任务: 自动下载 BV{bvid} P{page}")

        try:
            info = await fetch_video_info(bvid)
            logger.info(f"[miku_bilibili] 开始处理视频: {info.title} (ID: {bvid}, P{page})")

            # 缓存路径：video_cache/BV1xxxxx_P1.mp4
            cache_filename = f"{bvid}_P{page}.mp4"
            cache_path = VIDEO_CACHE_DIR / cache_filename

            # 命中缓存
            if cache_path.exists() and cache_path.stat().st_size > 0:
                logger.info(f"[miku_bilibili] 命中视频缓存: {cache_path}")
                logger.info(f"[miku_bilibili] [B站解析] 视频已保存到缓存: {bvid}_{page-1} -> {cache_path}")
                return str(cache_path)

            video_url, audio_url, quality_desc = await get_download_url(bvid)
            if not video_url:
                logger.warning(f"[miku_bilibili] 自动下载：原生链接获取失败，尝试 yt-dlp BV{bvid}")
                ytdlp_result = await _ytdlp_auto_download(bvid, page)
                if ytdlp_result:
                    return ytdlp_result
                logger.warning(f"[miku_bilibili] 自动下载失败：yt-dlp 也无法下载 BV{bvid}")
                return None

            login_status = "已登录" if is_logged_in() else "未登录"
            logger.info(f"[miku_bilibili] 自动下载画质: {quality_desc} ({login_status})")

            if audio_url:
                # DASH格式
                video_tmp = VIDEO_CACHE_DIR / f"{bvid}_P{page}-video.m4s"
                audio_tmp = VIDEO_CACHE_DIR / f"{bvid}_P{page}-audio.m4s"

                logger.info(f"[miku_bilibili] 开始并行下载 2 个视频媒体流...")
                logger.info(f"[miku_bilibili] 开始下载文件: {video_tmp.name} (使用 AsyncHttpx)")
                v_task = download_video_file(video_url, video_tmp)
                logger.info(f"[miku_bilibili] 开始下载文件: {audio_tmp.name} (使用 AsyncHttpx)")
                a_task = download_video_file(audio_url, audio_tmp)
                v_ok, a_ok = await asyncio.gather(v_task, a_task)

                if not v_ok:
                    logger.error(f"[miku_bilibili] 视频流下载失败 BV{bvid}")
                    return None

                if a_ok and await _has_ffmpeg():
                    logger.info(f"[miku_bilibili] 尝试快速合并 (stream copy)...")
                    success = await _merge_av(video_tmp, audio_tmp, cache_path)
                    for p in [video_tmp, audio_tmp]:
                        try:
                            p.unlink()
                        except Exception:
                            pass
                    if not success:
                        logger.warning(f"[miku_bilibili] 合并失败，重命名纯视频 BV{bvid}")
                        video_tmp.rename(cache_path)
                    else:
                        logger.info(f"[miku_bilibili] 媒体文件处理成功: {cache_path.name}")
                        logger.info(f"[miku_bilibili] 视频处理成功: {cache_path.name}")
                else:
                    if not a_ok:
                        logger.warning(f"[miku_bilibili] 音频下载失败，仅保存视频 BV{bvid}")
                    video_tmp.rename(cache_path)
                    try:
                        audio_tmp.unlink()
                    except Exception:
                        pass

            else:
                # MP4格式：直接下载到缓存
                logger.info(f"[miku_bilibili] 开始下载文件: {cache_path.name} (使用 AsyncHttpx)")
                success = await download_video_file(video_url, cache_path)
                if not success:
                    logger.error(f"[miku_bilibili] MP4下载失败 BV{bvid}")
                    return None

            if cache_path.exists() and cache_path.stat().st_size > 0:
                final_size = cache_path.stat().st_size
                logger.info(f"[miku_bilibili] [B站解析] 视频已保存到缓存: {bvid}_{page-1} -> {cache_path} ({format_file_size(final_size)})")
                return str(cache_path)
            return None

        except Exception as e:
            logger.error(f"[miku_bilibili] 自动下载异常 BV{bvid}: {e}", exc_info=True)
            # 回退到 yt-dlp
            logger.info(f"[miku_bilibili] 回退到 yt-dlp 自动下载 BV{bvid}")
            return await _ytdlp_auto_download(bvid, page)


# ============================================================
# yt-dlp 备用下载引擎
# ============================================================

# yt-dlp Netscape Cookie 文件缓存路径
_YTDLP_COOKIE_FILE = DATA_DIR / "ytdlp_bili_cookies.txt"


def _ensure_ytdlp_cookie_file() -> Optional[str]:
    """确保 yt-dlp 可用的 Netscape 格式 Cookie 文件存在，返回路径或 None"""
    cookies = get_cookies_dict()
    if not cookies:
        return None
    try:
        from utils.ytdlp_helper import convert_bili_cookies_to_netscape
        # 这里 cookies 是 dict 格式，我们直接写临时文件
        from pathlib import Path as _P
        import tempfile as _tf
        tmp = _tf.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8",
            prefix="bili_ytdlp_cookies_", dir=str(DATA_DIR),
        )
        try:
            tmp.write("# Netscape HTTP Cookie File\n")
            tmp.write("# Auto-generated for yt-dlp\n\n")
            domains = [".bilibili.com", ".api.bilibili.com", "bilibili.com"]
            for name, value in cookies.items():
                for dom in domains:
                    tmp.write(f"{dom}\tTRUE\t/\tFALSE\t0\t{name}\t{value}\n")
            tmp.flush()
            return tmp.name
        finally:
            tmp.close()
    except Exception as e:
        logger.debug(f"[miku_bilibili] 生成 yt-dlp Cookie 文件失败: {e}")
        return None


async def _ytdlp_download_bili(
    bvid: str,
    bot: Optional[Bot] = None,
    event: Optional[MessageEvent] = None,
    output_dir: Optional[str] = None,
) -> Optional[str]:
    """
    使用 yt-dlp 下载 B站视频（备用引擎）
    成功返回文件路径，失败返回 None
    """
    try:
        from utils.ytdlp_helper import (
            ytdlp_available, ytdlp_download, YtdlpDownloadOptions,
        )
    except ImportError as e:
        logger.warning(f"[miku_bilibili] yt-dlp 模块不可用: {e}")
        return None

    ok, err = ytdlp_available()
    if not ok:
        logger.warning(f"[miku_bilibili] yt-dlp 未安装，跳过备用下载: {err}")
        return None

    try:
        info = await fetch_video_info(bvid)
    except Exception as e:
        logger.warning(f"[miku_bilibili] yt-dlp 回退：获取视频信息失败: {e}")
        info = None

    # 判断画质
    quality_map = {16: 360, 32: 480, 64: 720, 80: 1080, 112: 1080, 116: 2160}
    qn = get_download_quality()
    max_height = quality_map.get(qn, 480 if not is_logged_in() else 720)

    bili_url = f"https://www.bilibili.com/video/{bvid}"
    logger.info(f"[miku_bilibili] 使用 yt-dlp 下载: {bili_url}, 最高画质={max_height}p")

    # 准备 Cookie
    cookie_file = _ensure_ytdlp_cookie_file()
    cookies_dict = get_cookies_dict() or None

    max_dur = get_max_download_duration()
    opts = YtdlpDownloadOptions(
        max_height=max_height,
        prefer_h264=True,
        max_duration_sec=max_dur * 60 if max_dur > 0 else 0,
        max_filesize=95 * 1024 * 1024,
        cookies_file=cookie_file,
        cookies_dict=cookies_dict,
        output_dir=output_dir or str(DOWNLOAD_DIR),
        outtmpl=f"{bvid}_%(title).50s.%(ext)s",
        merge=True,
        output_format="mp4",
        timeout=300,
        retries=3,
    )

    try:
        if bot and event:
            try:
                await bot.send(event, "🔄 尝试备用下载引擎（yt-dlp）...")
            except Exception:
                pass
        result = await ytdlp_download(bili_url, opts)
        if result:
            logger.info(f"[miku_bilibili] yt-dlp 备用下载成功: {result}")
        else:
            logger.warning(f"[miku_bilibili] yt-dlp 备用下载失败: {bvid}")
        return result
    except Exception as e:
        logger.error(f"[miku_bilibili] yt-dlp 备用下载异常: {e}", exc_info=True)
        return None
    finally:
        # 清理临时 cookie 文件
        if cookie_file:
            try:
                cf = Path(cookie_file)
                if cf.exists() and "ytdlp_cookies_" in cf.name:
                    cf.unlink()
            except Exception:
                pass


async def _ytdlp_auto_download(bvid: str, page: int = 1) -> Optional[str]:
    """
    yt-dlp 自动下载（静默版），下载到 VIDEO_CACHE_DIR
    """
    try:
        from utils.ytdlp_helper import (
            ytdlp_available, ytdlp_download, YtdlpDownloadOptions,
        )
    except ImportError:
        return None

    ok, _ = ytdlp_available()
    if not ok:
        return None

    # 缓存路径
    cache_filename = f"{bvid}_P{page}.mp4"
    cache_path = VIDEO_CACHE_DIR / cache_filename
    if cache_path.exists() and cache_path.stat().st_size > 0:
        return str(cache_path)

    quality_map = {16: 360, 32: 480, 64: 720, 80: 1080}
    qn = get_download_quality()
    max_height = quality_map.get(qn, 480 if not is_logged_in() else 720)

    cookie_file = _ensure_ytdlp_cookie_file()
    cookies_dict = get_cookies_dict() or None

    opts = YtdlpDownloadOptions(
        max_height=max_height,
        prefer_h264=True,
        max_duration_sec=0,
        max_filesize=95 * 1024 * 1024,
        cookies_file=cookie_file,
        cookies_dict=cookies_dict,
        output_dir=str(VIDEO_CACHE_DIR),
        outtmpl=f"{bvid}_P{page}.%(ext)s",
        merge=True,
        output_format="mp4",
        timeout=300,
        retries=2,
    )

    bili_url = f"https://www.bilibili.com/video/{bvid}"
    try:
        result = await ytdlp_download(bili_url, opts)
        if result and Path(result).exists():
            # 如果文件名不是预期的，重命名为缓存文件名
            rp = Path(result)
            expected = VIDEO_CACHE_DIR / cache_filename
            if rp.resolve() != expected.resolve():
                try:
                    if expected.exists():
                        expected.unlink()
                    rp.rename(expected)
                    result = str(expected)
                except Exception as e:
                    logger.debug(f"[miku_bilibili] yt-dlp 缓存重命名失败: {e}")
            logger.info(f"[miku_bilibili] yt-dlp 自动下载成功: {result}")
            return result
        return None
    except Exception as e:
        logger.warning(f"[miku_bilibili] yt-dlp 自动下载失败: {e}")
        return None
    finally:
        if cookie_file:
            try:
                cf = Path(cookie_file)
                if cf.exists() and "ytdlp_cookies_" in cf.name:
                    cf.unlink()
            except Exception:
                pass
