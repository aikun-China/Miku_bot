"""
底图辅助工具
=============

提供两个便捷功能：

1. find_bg_image(templates_dir, purpose)
   在 templates 目录中自动查找匹配 purpose 的图片文件。
   支持的文件名（不区分大小写，按优先级匹配第一个命中）：

     purpose="checkin"  ->  checkin_bg.png / 签到.png / 签到.jpg / checkin.png / checkin.jpg
     purpose="weather"  ->  weather_bg.png / 天气.png / 天气.jpg / weather.png / weather.jpg
     purpose="任意"     ->  在 templates 目录里按 keyword 匹配图片

2. load_bg_as_data_uri(file_path)
   把一张本地图片读成 base64 data URI，直接填进 HTML 模板的 url(...) 里。

3. setup_images(templates_dir, src_dir)
   （给 setup_images.py 用）扫描 src_dir 里所有图片，按文件名关键词自动
   改名/复制到 templates_dir，作为各功能的底图。
"""

from __future__ import annotations

import base64
import mimetypes
import shutil
from pathlib import Path
from typing import Dict, List, Optional


# —— 每个业务（purpose）对应的文件名关键词 ——
#   前面的优先级更高；扫描时大小写不敏感，且会匹配“文件名中是否包含关键词”
KEYWORD_MAP: Dict[str, List[str]] = {
    "checkin": [
        "checkin_bg", "check_in_bg", "signin_bg",
        "签到", "签到底图", "签到卡片", "qiandao", "checkin",
    ],
    "weather": [
        "weather_bg", "weather_card", "weather",
        "天气", "天气预报", "天气卡片", "tianqi",
    ],
    "menu": [
        "menu_bg", "menu_card", "menu",
        "菜单", "菜单背景", "功能菜单", "初音", "miku",
    ],
    "ai": [
        "ai_bg", "ai_card", "ai", "chat_bg",
        "AI背景", "聊天背景", "miku_bg", "初音背景",
    ],
    "profile": [
        "profile_bg", "profile_card", "profile",
        "个人资料", "资料", "我的信息", "用户信息", "avatar", "头像",
    ],
    "shop": [
        "shop_bg", "shop_card", "shop",
        "商店", "商城", "商店背景", "商品卡",
    ],
}
# 支持的扩展名
SUPPORTED_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}

# 各业务最终“标准名”（由 setup_images.py 写进去的文件名）
STANDARD_NAMES = {
    "checkin": "checkin_bg.png",
    "weather": "weather_bg.png",
    "menu": "menu_bg.png",
    "ai": "ai_bg.png",
    "profile": "profile_bg.png",
    "shop": "shop_bg.png",
}


# ======================================================================
# 工具小函数
# ======================================================================

def _is_image(p: Path) -> bool:
    return p.is_file() and p.suffix.lower() in SUPPORTED_EXTS


def _name_match(purpose: str, filename: str) -> bool:
    """检查文件名（不含扩展名）是否命中该 purpose 的任一关键词。"""
    keywords = KEYWORD_MAP.get(purpose, [])
    if not keywords:
        return False
    low = filename.lower()                      # 例：我的签到.jpg -> 我的签到.jpg
    stem = Path(filename).stem.lower()           # 例：我的签到
    for kw in keywords:
        kw_low = kw.lower()
        if kw_low in low or kw_low in stem:
            return True
    return False


# ======================================================================
# 对外 API
# ======================================================================

def find_bg_image(purpose: str, templates_dir: Optional[Path] = None) -> Optional[Path]:
    """
    在 templates_dir 中查找匹配该 purpose 的底图文件。

    - 先找“标准名”（checkin_bg.png / weather_bg.png），确保前面脚本改名后立即生效
    - 否则扫描整个目录，按关键词（中文/英文）匹配，返回第一个命中的
    - 找不到返回 None（调用方此时应回退到纯 CSS 渐变）
    """
    if templates_dir is None:
        templates_dir = Path(__file__).resolve().parent / "templates"

    templates_dir = Path(templates_dir)
    if not templates_dir.is_dir():
        return None

    # 1) 标准名优先（精确匹配，扩展名不限）
    standard_stem = Path(STANDARD_NAMES.get(purpose, f"{purpose}_bg.png")).stem
    for p in sorted(templates_dir.iterdir()):
        if _is_image(p) and p.stem == standard_stem:
            return p

    # 2) 关键词模糊匹配
    candidates: List[Path] = []
    for p in sorted(templates_dir.iterdir()):
        if _is_image(p) and _name_match(purpose, p.name):
            candidates.append(p)
    if candidates:
        return candidates[0]

    return None


def load_bg_as_data_uri(image_path: Path) -> str:
    """
    读取本地图片并返回 data URI（可直接放进 CSS url() / HTML <img>）
    失败返回空字符串（调用方用 CSS fallback 即可）。
    """
    path = Path(image_path)
    if not path.is_file():
        return ""
    try:
        mime, _ = mimetypes.guess_type(path.name)
        if not mime:
            mime = "image/png"
        raw = path.read_bytes()
        if not raw:
            return ""
        b64 = base64.b64encode(raw).decode("ascii")
        return f"data:{mime};base64,{b64}"
    except Exception:
        return ""


def find_and_load_bg(purpose: str, templates_dir: Optional[Path] = None) -> str:
    """
    便捷入口：定位底图 -> 读为 data URI。找不到返回空字符串。
    """
    p = find_bg_image(purpose, templates_dir)
    if p is None:
        return ""
    return load_bg_as_data_uri(p)


def get_image_size(image_path: Path) -> Optional[tuple]:
    """
    获取图片尺寸（宽度, 高度）。失败返回 None。
    优先根据文件头判断格式，避免后缀名与实际格式不符（如 .png 实为 JPEG）。
    """
    path = Path(image_path)
    if not path.is_file():
        return None
    try:
        import struct
        with open(path, 'rb') as f:
            header = f.read(24)
        # PNG
        if len(header) >= 24 and header[:8] == b'\x89PNG\r\n\x1a\n':
            width = struct.unpack('>I', header[16:20])[0]
            height = struct.unpack('>I', header[20:24])[0]
            return (width, height)
        # JPEG
        if header[:2] == b'\xff\xd8':
            with open(path, 'rb') as f:
                f.seek(2)
                while True:
                    marker = f.read(2)
                    if not marker or len(marker) < 2:
                        break
                    if marker[0] != 0xff:
                        break
                    if marker[1] in (0xd8, 0xd9, 0x01):
                        continue
                    if marker[1] in range(0xd0, 0xd9):
                        continue
                    len_bytes = f.read(2)
                    if len(len_bytes) < 2:
                        break
                    length = struct.unpack('>H', len_bytes)[0]
                    if marker[1] in (0xc0, 0xc1, 0xc2, 0xc3, 0xc5, 0xc6, 0xc7, 0xc9, 0xca, 0xcb, 0xcd, 0xce, 0xcf):
                        raw = f.read(6)
                        if len(raw) < 6:
                            break
                        height = struct.unpack('>H', raw[1:3])[0]
                        width = struct.unpack('>H', raw[3:5])[0]
                        return (width, height)
                    f.seek(length - 2, 1)
        return None
    except Exception:
        return None


# ======================================================================
# 安装用（给 setup_images.py 调用）
# ======================================================================

def detect_purpose_from_filename(filename: str) -> Optional[str]:
    """反向：根据文件名判断它最可能是哪个业务的底图（用于自动改名）。"""
    # 按 purpose 扫描，命中就返回
    for purpose in ["checkin", "weather", "menu", "ai", "profile", "shop"]:
        if _name_match(purpose, filename):
            return purpose
    # 没命中任何业务 -> None
    return None


def install_image(src_path: Path, templates_dir: Path, purpose: Optional[str] = None) -> Optional[Path]:
    """
    把 src_path 这张图片复制到 templates_dir/标准名，覆盖旧文件。
    不指定 purpose 时自动识别。返回目标文件路径；失败返回 None。
    """
    src = Path(src_path)
    if not src.is_file() or src.suffix.lower() not in SUPPORTED_EXTS:
        return None

    if purpose is None:
        purpose = detect_purpose_from_filename(src.name)
    if purpose is None:
        return None

    target = templates_dir / STANDARD_NAMES[purpose]
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, target)
    return target
