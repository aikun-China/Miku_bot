"""
MikuBot 底层截图引擎（已优化版）

所有插件的图片生成统一走这里。
相比原版的改动：
  1. wait_until:  networkidle -> domcontentloaded  （节省 1~2 秒）
  2. 去掉多余的 wait_for_selector("body") 等待
  3. 加入基于内容哈希的短缓存（默认 60 秒）
  4. 单 page 复用（避免每次 new_page 又 close）
  5. scale factor 可通过环境变量 MIKU_DPR 控制（默认 1.5，清晰又快）

提供两种截图模式：
  1. screenshot_url(url)     — 截网页
  2. screenshot_html(html)       — 截 HTML 字符串（用于插件自定义界面）

用法示例：
    from utils.screenshot import screenshot_html
    img_path = await screenshot_html("<h1>Hello</h1>", width=800, height=400)
    await msg.finish(MessageSegment.image(img_path))
"""

from playwright.async_api import async_playwright
from pathlib import Path
import hashlib
import asyncio
import os
import time

# 浏览器策略：优先尝试系统 Edge（channel="msedge"），失败回退到 Playwright 自带 chromium
PREFERRED_BROWSER_CHANNEL = os.environ.get("MIKU_BROWSER_CHANNEL", "msedge")

# 图片输出目录
SCREENSHOT_DIR = Path(__file__).resolve().parent.parent / "data" / "images"
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

# 渲染倍率：1.5 比 2.0 快 2~3 倍，同时仍然清晰
DPR = float(os.environ.get("MIKU_DPR", "1.5"))

# 短缓存（秒）：相同 HTML 内容在 CACHE_TTL 秒内直接复用图片，避免重复渲染
CACHE_TTL = int(os.environ.get("MIKU_SCREEN_CACHE", "60"))


def to_image_uri(file_path):
    # 将本地文件路径转换为 OneBot 兼容的 file:/// URI
    # Windows: D:\path\to\x.png  ->  file:///D:/path/to/x.png
    p = Path(file_path).resolve()
    return p.as_uri()


# ─── 内部：单例浏览器 + 单例 Page 复用 ───
_browser = None
_playwright = None
_context = None
_page = None
_lock = asyncio.Lock()
_page_lock = asyncio.Lock()  # 截图时锁住 page 避免并发在同一个 page 上跑多个操作


async def _ensure_browser():
    global _browser, _context, _page, _playwright
    async with _lock:
        need_rebuild = (
            _browser is None
            or _context is None
            or _page is None
            or not _browser.is_connected()
        )
        if need_rebuild:
            # 先把旧的清理掉（如果存在）
            try:
                if _page is not None:
                    await _page.close()
            except Exception:
                pass
            try:
                if _context is not None:
                    await _context.close()
            except Exception:
                pass
            try:
                if _browser is not None:
                    await _browser.close()
            except Exception:
                pass
            try:
                if _playwright is not None:
                    await _playwright.stop()
            except Exception:
                pass
            _page = None
            _context = None
            _browser = None

            _playwright = await async_playwright().start()

            if PREFERRED_BROWSER_CHANNEL == "msedge":
                try:
                    _browser = await _playwright.chromium.launch(
                        headless=True,
                        channel="msedge",
                    )
                except Exception as e:
                    print(f"[WARN] 系统 Edge 启动失败: {e}，回退到 Playwright 自带 chromium")
                    _browser = await _playwright.chromium.launch(headless=True)
            else:
                _browser = await _playwright.chromium.launch(headless=True)

            _context = await _browser.new_context(
                viewport={"width": 1280, "height": 800},
                device_scale_factor=DPR,
            )
            _page = await _context.new_page()
    return _page


async def _close_browser():
    """关闭浏览器（Bot 退出时调用）"""
    global _browser, _context, _page, _playwright
    async with _lock:
        try:
            if _page:
                await _page.close()
        except Exception:
            pass
        _page = None
        try:
            if _context:
                await _context.close()
        except Exception:
            pass
        _context = None
        try:
            if _browser:
                await _browser.close()
        except Exception:
            pass
        _browser = None
        try:
            if _playwright:
                await _playwright.stop()
        except Exception:
            pass
        _playwright = None


def _make_path(content: str, suffix: str = ".png") -> Path:
    """根据内容哈希生成唯一文件名，避免重复"""
    h = hashlib.md5(content.encode("utf-8")).hexdigest()[:12]
    return SCREENSHOT_DIR / f"{h}{suffix}"


def _hit_cache(path: Path) -> bool:
    """判断缓存是否有效（文件存在且修改时间在 CACHE_TTL 内）"""
    try:
        if not path.exists():
            return False
        age = time.time() - path.stat().st_mtime
        return 0 <= age <= CACHE_TTL
    except Exception:
        return False


# ═══════════════════════════════════════════
# 公开 API
# ═══════════════════════════════════════════

async def screenshot_url(
    url: str,
    width: int = 1280,
    height: int = 800,
    full_page: bool = True,
    wait_for: str = None,
    timeout: int = 15000,
) -> Path:
    """
    截取网页 URL 返回图片路径

    :param url:          网页地址
    :param width/height: 视口大小
    :param full_page:    是否截取完整页面（False=只截取视口）
    :param wait_for:   CSS 选择器，等待元素出现后再截图（如 "body"）
    :param timeout:     加载超时（毫秒）
    :return:             Path 图片路径
    """
    path = _make_path(url)
    if _hit_cache(path):
        return path

    page = await _ensure_browser()
    async with _page_lock:
        await page.set_viewport_size({"width": width, "height": height})
        await page.goto(url, wait_until="domcontentloaded", timeout=timeout)
        if wait_for:
            await page.wait_for_selector(wait_for, timeout=timeout)
        await page.screenshot(path=str(path), full_page=full_page, type="png")
    return path


async def screenshot_html(
    html: str,
    width: int = 800,
    height: int = 600,
    wait_for: str = None,
    timeout: int = 8000,
) -> Path:
    """把 HTML 字符串渲染成图片。width 决定图片宽度，高度自适应内容。"""
    path = _make_path(html)
    # 短缓存命中：直接返回现成图片，节省 100%
    if _hit_cache(path):
        return path

    page = await _ensure_browser()
    async with _page_lock:
        await page.set_viewport_size({"width": width, "height": height})
        # domcontentloaded 足够，不需要等 networkidle（会额外等 1~2 秒
        await page.set_content(html, timeout=timeout, wait_until="domcontentloaded")
        if wait_for:
            await page.wait_for_selector(wait_for, timeout=timeout)
        await page.screenshot(path=str(path), full_page=True, type="png")
    return path
