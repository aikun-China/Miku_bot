"""
yt-dlp 统一视频下载模块
========================
支持 YouTube、Bilibili、抖音、微博、快手、Twitter/X、TikTok、Instagram 等上千个平台

用法：
    from utils.ytdlp_helper import (
        ytdlp_available, ytdlp_download, ytdlp_get_info,
        YtdlpDownloadOptions,
    )

    # 简单下载
    path = await ytdlp_download("https://www.bilibili.com/video/BV1xx...")

    # 带选项下载
    opts = YtdlpDownloadOptions(
        max_height=720,
        max_duration_sec=600,
        cookies_file="data/miku_bilibili/bili_cookies_netscape.txt",
        output_dir="data/downloads",
    )
    path = await ytdlp_download(url, opts)
"""

import asyncio
import json
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from nonebot.log import logger


# ============================================================
# 可用性检测
# ============================================================
_ytdlp_available: Optional[bool] = None
_ytdlp_import_error: Optional[str] = None


def ytdlp_available() -> Tuple[bool, str]:
    """检测 yt-dlp 是否可用，返回 (可用, 错误信息)"""
    global _ytdlp_available, _ytdlp_import_error
    if _ytdlp_available is not None:
        return _ytdlp_available, _ytdlp_import_error or ""
    try:
        import yt_dlp  # noqa: F401
        _ytdlp_available = True
        _ytdlp_import_error = ""
        logger.info("[ytdlp_helper] yt-dlp 已可用")
    except ImportError as e:
        _ytdlp_available = False
        _ytdlp_import_error = str(e)
        logger.warning(f"[ytdlp_helper] yt-dlp 未安装: {e}")
    return _ytdlp_available, _ytdlp_import_error or ""


# ============================================================
# 下载选项
# ============================================================
@dataclass
class YtdlpDownloadOptions:
    """yt-dlp 下载选项"""

    # 画质限制（视频最大高度，如 480/720/1080，0=不限制）
    max_height: int = 720
    # 是否优先使用 H.264 编码（兼容性更好）
    prefer_h264: bool = True
    # 最大时长（秒），超过则取消下载，0=不限制
    max_duration_sec: int = 600
    # 最大文件大小（字节），超过则取消下载，0=不限制
    max_filesize: int = 95 * 1024 * 1024  # 默认 95MB（QQ 视频限制约 100MB）
    # Cookie 文件路径（Netscape 格式）
    cookies_file: Optional[str] = None
    # Cookie 字典（会自动转换为 Netscape 临时文件）
    cookies_dict: Optional[Dict[str, str]] = None
    # 输出目录
    output_dir: Optional[str] = None
    # 文件名模板（yt-dlp 格式，如 "%(title)s.%(ext)s"）
    outtmpl: str = "%(id)s_%(title).50s.%(ext)s"
    # 是否合并音视频（需要 ffmpeg）
    merge: bool = True
    # 输出格式：mp4 / mkv / best
    output_format: str = "mp4"
    # 只下载音频
    audio_only: bool = False
    # 音频格式：mp3 / m4a / best
    audio_format: str = "mp3"
    # 额外的 yt-dlp 参数（覆盖内置参数）
    extra_params: Dict[str, Any] = field(default_factory=dict)
    # 进度回调函数 (downloaded_bytes, total_bytes)
    progress_cb: Optional[Callable[[int, Optional[int]], None]] = None
    # 请求超时（秒）
    timeout: int = 300
    # 代理 URL（如 http://127.0.0.1:7890）
    proxy: Optional[str] = None
    # 重试次数
    retries: int = 3
    # 自定义 HTTP 请求头（如 Referer、User-Agent 等）
    headers: Dict[str, str] = field(default_factory=dict)


# ============================================================
# Cookie 字典转 Netscape 格式
# ============================================================
def _cookies_dict_to_netscape(cookies: Dict[str, str], domain: str = ".bilibili.com") -> Path:
    """将 Cookie 字典写入临时 Netscape 格式文件，返回文件路径"""
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False, encoding="utf-8", prefix="ytdlp_cookies_"
    )
    try:
        tmp.write("# Netscape HTTP Cookie File\n")
        tmp.write("# This is a generated file!  Do not edit.\n\n")
        for name, value in cookies.items():
            # domain  includeSubdomains  path  secure  expiration  name  value
            tmp.write(f"{domain}\tTRUE\t/\tFALSE\t0\t{name}\t{value}\n")
        tmp.flush()
        return Path(tmp.name)
    finally:
        tmp.close()


# ============================================================
# 构造 yt-dlp 参数
# ============================================================
def _build_ydl_opts(opts: YtdlpDownloadOptions) -> Tuple[Dict[str, Any], List[Path]]:
    """构造 YoutubeDL 参数，返回 (opts_dict, 需要清理的临时文件列表)"""
    import yt_dlp

    temp_files: List[Path] = []
    ydl_opts: Dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "ignoreerrors": False,
        "retries": opts.retries,
        "fragment_retries": opts.retries,
        "skip_unavailable_fragments": True,
        "concurrent_fragment_downloads": 4,
        "noprogress": opts.progress_cb is None,
    }

    # 超时
    if opts.timeout:
        ydl_opts["socket_timeout"] = opts.timeout

    # 代理
    if opts.proxy:
        ydl_opts["proxy"] = opts.proxy

    # 自定义 HTTP 请求头（Referer、User-Agent 等）
    if opts.headers:
        ydl_opts["http_headers"] = opts.headers
        logger.debug(f"[ytdlp_helper] 使用自定义请求头: {list(opts.headers.keys())}")

    # 输出目录 / 文件名
    outtmpl_parts = []
    if opts.output_dir:
        outtmpl_parts.append(str(Path(opts.output_dir).resolve()))
    outtmpl_parts.append(opts.outtmpl)
    ydl_opts["outtmpl"] = str(Path(*outtmpl_parts)) if len(outtmpl_parts) > 1 else outtmpl_parts[0]

    # 格式选择
    if opts.audio_only:
        ydl_opts["format"] = "bestaudio/best"
        ydl_opts["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": opts.audio_format,
            "preferredquality": "192",
        }]
    else:
        # 视频格式
        if opts.max_height and opts.max_height > 0:
            height = opts.max_height
            if opts.prefer_h264:
                # 优先 H.264，兼容性好
                fmt = (
                    f"bestvideo[height<={height}][vcodec^=avc1]"
                    f"+bestaudio[acodec^=mp4a]/"
                    f"bestvideo[height<={height}]+bestaudio/"
                    f"best[height<={height}]/best"
                )
            else:
                fmt = (
                    f"bestvideo[height<={height}]+bestaudio/"
                    f"best[height<={height}]/best"
                )
            ydl_opts["format"] = fmt
        else:
            ydl_opts["format"] = "bestvideo+bestaudio/best"

        # 合并设置
        if opts.merge:
            ydl_opts["merge_output_format"] = opts.output_format

    # 文件大小限制
    if opts.max_filesize and opts.max_filesize > 0:
        ydl_opts["max_filesize"] = opts.max_filesize

    # Cookies
    cookies_file = opts.cookies_file
    if opts.cookies_dict:
        # 写临时 cookie 文件
        tmp_path = _cookies_dict_to_netscape(opts.cookies_dict)
        temp_files.append(tmp_path)
        cookies_file = str(tmp_path)
        logger.debug(f"[ytdlp_helper] 已生成临时 Cookie 文件: {tmp_path}")

    if cookies_file and Path(cookies_file).exists():
        ydl_opts["cookiefile"] = cookies_file
        logger.debug(f"[ytdlp_helper] 使用 Cookie 文件: {cookies_file}")

    # 进度钩子
    if opts.progress_cb:
        def _hook(d):
            if d.get("status") == "downloading":
                downloaded = d.get("downloaded_bytes", 0)
                total = d.get("total_bytes") or d.get("total_bytes_estimate")
                try:
                    opts.progress_cb(int(downloaded), int(total) if total else None)
                except Exception:
                    pass
        ydl_opts["progress_hooks"] = [_hook]

    # 额外参数覆盖
    if opts.extra_params:
        ydl_opts.update(opts.extra_params)

    return ydl_opts, temp_files


# ============================================================
# 提取信息
# ============================================================
async def ytdlp_get_info(url: str, download: bool = False, cookies: Optional[Dict[str, str]] = None, cookies_domain: str = "") -> Optional[Dict[str, Any]]:
    """
    获取视频信息（不下载）

    :param url: 视频 URL
    :param download: 是否获取下载格式的完整信息
    :param cookies: Cookie 字典
    :param cookies_domain: Cookie 所属域名（用于生成 Netscape 文件）
    :return: 信息字典，失败返回 None
    """
    ok, err = ytdlp_available()
    if not ok:
        logger.warning(f"[ytdlp_helper] yt-dlp 不可用: {err}")
        return None

    temp_files: List[Path] = []

    def _sync():
        import yt_dlp
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": not download,
            "ignoreerrors": True,
            "http_headers": {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            },
        }
        if cookies:
            domain = cookies_domain or ""
            # 尝试从 URL 推断域名
            if not domain:
                try:
                    from urllib.parse import urlparse
                    parsed = urlparse(url)
                    domain = parsed.netloc
                except Exception:
                    domain = ""
            if domain:
                cookie_file = _cookies_dict_to_netscape(cookies, domain=domain)
                temp_files.append(cookie_file)
                ydl_opts["cookiefile"] = str(cookie_file)
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                # 清理大字段，避免内存问题
                for k in ["formats", "thumbnails", "automatic_captions", "subtitles", "requested_formats"]:
                    info.pop(k, None)
                return info
        finally:
            for tf in temp_files:
                try:
                    if tf.exists():
                        tf.unlink()
                except Exception:
                    pass

    try:
        return await asyncio.wait_for(asyncio.to_thread(_sync), timeout=60)
    except Exception as e:
        logger.warning(f"[ytdlp_helper] 获取信息失败: {url[:80]} - {e}")
        return None


# ============================================================
# 下载视频
# ============================================================
async def ytdlp_download(url: str, opts: Optional[YtdlpDownloadOptions] = None) -> Optional[str]:
    """
    使用 yt-dlp 下载视频，返回本地文件路径

    :param url: 视频 URL
    :param opts: 下载选项（默认使用 YtdlpDownloadOptions 默认值）
    :return: 下载成功返回文件绝对路径，失败返回 None
    """
    ok, err = ytdlp_available()
    if not ok:
        logger.warning(f"[ytdlp_helper] yt-dlp 不可用: {err}")
        return None

    if opts is None:
        opts = YtdlpDownloadOptions()

    # 确保输出目录存在
    if opts.output_dir:
        Path(opts.output_dir).mkdir(parents=True, exist_ok=True)

    ydl_opts, temp_files = _build_ydl_opts(opts)
    timeout = opts.timeout or 300

    def _sync_download() -> Optional[str]:
        import yt_dlp
        info = None
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                if info is None:
                    logger.warning(f"[ytdlp_helper] extract_info 返回 None: {url[:80]}")
                    return None
                # 播放列表：取第一个
                if "entries" in info:
                    entries = info.get("entries") or []
                    if entries:
                        info = entries[0]
                    else:
                        logger.warning(f"[ytdlp_helper] 播放列表为空: {url[:80]}")
                        return None
                # 获取已下载的文件路径
                filepath = info.get("filepath") or info.get("_filename")
                if not filepath and opts.audio_only:
                    requested = info.get("requested_downloads") or []
                    if requested:
                        filepath = requested[0].get("filepath")
                # 校验文件，找不到则在输出目录搜索最新的媒体文件
                p = Path(filepath) if filepath else None
                if not p or not p.exists():
                    search_dir = Path(opts.output_dir).resolve() if opts.output_dir else Path.cwd()
                    exts = {opts.audio_format} if opts.audio_only else {"mp4", "mkv", "webm", "flv", "avi", "mov"}
                    candidates = [
                        f for f in search_dir.iterdir()
                        if f.is_file() and f.suffix.lower().lstrip(".") in exts
                    ]
                    if candidates:
                        p = max(candidates, key=lambda f: f.stat().st_mtime)
                        filepath = str(p.resolve())
                        logger.debug(f"[ytdlp_helper] 通过目录搜索找到文件: {p.name}")
                    else:
                        logger.warning(f"[ytdlp_helper] 找不到下载文件: {filepath}")
                        return None
                if p.stat().st_size == 0:
                    logger.warning(f"[ytdlp_helper] 下载文件为空: {filepath}")
                    try:
                        p.unlink()
                    except Exception:
                        pass
                    return None
                # 时长校验
                duration = info.get("duration") or 0
                if opts.max_duration_sec and opts.max_duration_sec > 0:
                    if duration and int(duration) > opts.max_duration_sec:
                        logger.warning(
                            f"[ytdlp_helper] 视频时长 {duration}s 超过限制 {opts.max_duration_sec}s, 删除文件"
                        )
                        try:
                            p.unlink()
                        except Exception:
                            pass
                        return None
                logger.info(
                    f"[ytdlp_helper] 下载成功: {p.name}, "
                    f"大小: {p.stat().st_size / 1024 / 1024:.1f}MB, "
                    f"时长: {duration}s"
                )
                return str(p.resolve())
        except yt_dlp.utils.MaxDownloadsReached:
            logger.warning(f"[ytdlp_helper] 文件大小超过限制: {url[:80]}")
            return None
        except Exception as e:
            logger.error(f"[ytdlp_helper] 下载失败: {url[:80]} - {e}", exc_info=True)
            return None
        finally:
            # 清理临时 Cookie 文件
            for tf in temp_files:
                try:
                    if tf.exists():
                        tf.unlink()
                except Exception:
                    pass

    try:
        result = await asyncio.wait_for(asyncio.to_thread(_sync_download), timeout=timeout)
        return result
    except asyncio.TimeoutError:
        logger.error(f"[ytdlp_helper] 下载超时 ({timeout}s): {url[:80]}")
        return None
    except Exception as e:
        logger.error(f"[ytdlp_helper] 下载异常: {e}", exc_info=True)
        return None


# ============================================================
# 获取 yt-dlp 支持的提取器列表（检测平台）
# ============================================================
async def ytdlp_list_extractors() -> List[str]:
    """获取 yt-dlp 支持的所有提取器名称列表"""
    ok, _ = ytdlp_available()
    if not ok:
        return []

    def _sync():
        import yt_dlp
        names = []
        for ie in yt_dlp.list_extractors():
            try:
                names.append(ie.IE_NAME)
            except Exception:
                pass
        return names

    try:
        return await asyncio.to_thread(_sync)
    except Exception:
        return []


# ============================================================
# 将 B站 cookies.json 转为 Netscape 格式
# ============================================================
def convert_bili_cookies_to_netscape(cookie_json_path: str, output_path: str) -> bool:
    """
    将 B站插件的 bili_cookies.json 转换为 yt-dlp 可用的 Netscape Cookie 文件

    :param cookie_json_path: data/miku_bilibili/bili_cookies.json 路径
    :param output_path: 输出 Netscape 格式文件路径
    :return: 是否成功
    """
    try:
        src = Path(cookie_json_path)
        if not src.exists():
            logger.warning(f"[ytdlp_helper] B站 Cookie 文件不存在: {cookie_json_path}")
            return False
        data = json.loads(src.read_text(encoding="utf-8"))

        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            f.write("# Netscape HTTP Cookie File\n")
            f.write("# Converted from miku_bilibili bili_cookies.json\n\n")
            for item in data:
                if isinstance(item, dict):
                    name = item.get("name") or item.get("Name") or ""
                    value = item.get("value") or item.get("Value") or ""
                    domain = item.get("domain") or item.get("Domain") or ".bilibili.com"
                    path = item.get("path") or item.get("Path") or "/"
                    secure = "TRUE" if str(item.get("secure", item.get("Secure", False))).lower() in ("true", "1", "yes") else "FALSE"
                    expires = str(item.get("expires") or item.get("Expires") or item.get("expiry") or 0)
                    if name and value:
                        f.write(f"{domain}\tTRUE\t{path}\t{secure}\t{expires}\t{name}\t{value}\n")
                elif isinstance(item, (list, tuple)) and len(item) >= 2:
                    # [name, value] 格式
                    name, value = str(item[0]), str(item[1])
                    f.write(f".bilibili.com\tTRUE\t/\tFALSE\t0\t{name}\t{value}\n")
        logger.info(f"[ytdlp_helper] B站 Cookie 已转换: {cookie_json_path} -> {output_path}")
        return True
    except Exception as e:
        logger.error(f"[ytdlp_helper] 转换 B站 Cookie 失败: {e}", exc_info=True)
        return False


# ============================================================
# 检测 ffmpeg 可用性
# ============================================================
_has_ffmpeg: Optional[bool] = None


async def ytdlp_has_ffmpeg() -> bool:
    """检测系统是否安装了 ffmpeg（yt-dlp 合并音视频需要）"""
    global _has_ffmpeg
    if _has_ffmpeg is not None:
        return _has_ffmpeg
    # 先检查 yt-dlp 自带 ffmpeg
    ok, _ = ytdlp_available()
    if ok:
        try:
            import yt_dlp
            binary = getattr(yt_dlp, "get_variant", lambda: None)()
            if binary and hasattr(yt_dlp, "YoutubeDL"):
                # yt-dlp 二进制包自带 ffmpeg
                _has_ffmpeg = True
                return _has_ffmpeg
        except Exception:
            pass
    # 检查系统 PATH
    _has_ffmpeg = shutil.which("ffmpeg") is not None
    if _has_ffmpeg:
        logger.info("[ytdlp_helper] 检测到系统 ffmpeg")
    else:
        logger.warning("[ytdlp_helper] 未检测到 ffmpeg，音视频合并将不可用")
    return _has_ffmpeg


# ============================================================
# 模块初始化日志
# ============================================================
ok, err = ytdlp_available()
if ok:
    logger.info("[ytdlp_helper] 模块初始化完成，yt-dlp 已加载")
else:
    logger.info(f"[ytdlp_helper] 模块初始化完成，yt-dlp 未安装（{err[:60]}），功能暂不可用")
