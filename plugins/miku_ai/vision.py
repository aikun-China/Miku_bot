"""
Miku AI 图片识别模块
"""

import base64
import re
import json
import time
import asyncio
from pathlib import Path
from typing import Optional, Dict, List
from nonebot.log import logger
import httpx

from .config import get_config, PROJECT_ROOT
from .emoji_library import EmojiLibrary
from .exception import ImageRecognitionException


def _extract_content(choice) -> str:
    """从 API 响应的 choice 中提取文本内容"""
    message = choice.get("message", {}) if isinstance(choice, dict) else {}
    if not isinstance(message, dict):
        return ""
    content = message.get("content", "")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict):
                parts.append(str(part.get("text", "")))
            elif isinstance(part, str):
                parts.append(part)
        return "".join(parts).strip()
    return str(content).strip() if content else ""


_vision_semaphore = None


def get_vision_semaphore() -> asyncio.Semaphore:
    global _vision_semaphore
    if _vision_semaphore is None:
        concurrency = int(get_config("vision_concurrency", 2) or 2)
        if concurrency < 1:
            concurrency = 1
        _vision_semaphore = asyncio.Semaphore(concurrency)
    return _vision_semaphore


def _detect_mime_type(content: bytes) -> str:
    if not content or len(content) < 4:
        return "image/jpeg"
    if content[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if content[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if content[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "image/webp"
    if content[:2] == b"BM":
        return "image/bmp"
    return "image/jpeg"


def _image_bytes_to_data_uri(content: bytes, mime_type: str = "") -> str:
    if not mime_type or mime_type == "image/jpeg":
        detected = _detect_mime_type(content)
        if detected != "image/jpeg" or not mime_type:
            mime_type = detected
    b64 = base64.b64encode(content).decode("ascii")
    return f"data:{mime_type};base64,{b64}"


async def _download_image_bytes(image_url: str) -> Optional[bytes]:
    if not image_url:
        return None
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": "https://qpic.qq.com/",
        }
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True, headers=headers) as client:
            resp = await client.get(image_url)
            if resp.status_code == 200 and resp.content:
                return resp.content
            logger.debug(f"[miku_ai] 下载图片失败 HTTP {resp.status_code}: {image_url[:80]}")
    except Exception as e:
        logger.debug(f"[miku_ai] 下载图片异常: {e}")
    return None


async def fetch_image_info(image_url: str, local_file: str = "") -> Dict:
    result = {"url": image_url, "file_size": 0, "width": 0, "height": 0,
              "mime_type": "", "image_hash": "", "is_gif": False}
    
    content = None
    if image_url:
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Referer": "https://qpic.qq.com/",
            }
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True, headers=headers) as client:
                resp = await client.get(image_url)
                if resp.status_code == 200:
                    content = resp.content
                    result["mime_type"] = resp.headers.get("content-type", "")
                elif resp.status_code == 403:
                    logger.debug(f"[miku_ai] 图片下载403（防盗链），尝试本地缓存: {local_file}")
        except Exception as e:
            logger.debug(f"[miku_ai] URL下载图片异常: {e}")
    
    if content is None and local_file:
        local_path = None
        p = Path(local_file)
        if p.is_absolute() and p.exists():
            local_path = p
        else:
            candidate = PROJECT_ROOT / "data" / "images" / local_file
            if candidate.exists():
                local_path = candidate
            elif "/" in local_file or "\\" in local_file:
                candidate2 = PROJECT_ROOT / local_file
                if candidate2.exists():
                    local_path = candidate2
        
        if local_path and local_path.exists():
            try:
                content = local_path.read_bytes()
                result["mime_type"] = _guess_mime_type(local_path.suffix)
            except Exception as e:
                logger.debug(f"[miku_ai] 读取本地图片失败: {e}")
    
    if not content:
        logger.info(f"[miku_ai] 图片下载失败（无内容），url={image_url[:80] if image_url else ''}")
        return result
    
    result["file_size"] = len(content)
    
    try:
        from PIL import Image
        import imagehash
        import io as _io
        img = Image.open(_io.BytesIO(content))
        result["width"], result["height"] = img.size
        result["is_gif"] = img.format == "GIF"
        if result["is_gif"]:
            img.seek(0)
        rgb_img = img.convert("RGB")
        h = imagehash.dhash(rgb_img, hash_size=8)
        result["image_hash"] = str(h)
        rgb_img.close()
        img.close()
    except Exception as img_err:
        logger.debug(f"[miku_ai] 图片处理失败: {img_err}")
    
    logger.info(f"[miku_ai] 图片元数据: size={result['file_size']}B, {result['width']}x{result['height']}, hash={result['image_hash'][:16] if result['image_hash'] else '无'}")
    return result


def _guess_mime_type(ext: str) -> str:
    ext = ext.lower().lstrip(".")
    mapping = {
        "jpg": "image/jpeg", "jpeg": "image/jpeg",
        "png": "image/png", "gif": "image/gif",
        "bmp": "image/bmp", "webp": "image/webp",
        "tiff": "image/tiff", "tif": "image/tiff",
    }
    return mapping.get(ext, "image/octet-stream")


def should_skip_recognize(meta: Dict, user_id: str = "", user_timestamps: Dict[str, list] = None) -> bool:
    if not meta:
        return True
    skip_size_kb = int(get_config("emoji_skip_size_kb", 20) or 20)
    skip_dim = int(get_config("emoji_skip_dimension", 200) or 200)
    gif_skip_size_kb = int(get_config("emoji_gif_skip_size_kb", 50) or 50)
    spam_count = int(get_config("emoji_spam_max_count", 3) or 3)
    spam_seconds = int(get_config("emoji_spam_window_seconds", 60) or 60)
    
    file_size_kb = (meta.get("file_size", 0) or 0) / 1024
    width = meta.get("width", 0) or 0
    height = meta.get("height", 0) or 0
    is_gif = meta.get("is_gif", False)
    
    if file_size_kb < skip_size_kb:
        return True
    if width > 0 and height > 0 and width < skip_dim and height < skip_dim:
        return True
    if is_gif and file_size_kb < gif_skip_size_kb:
        return True
    
    if user_id and spam_count > 0 and user_timestamps is not None:
        now = time.time()
        key = str(user_id)
        timestamps = user_timestamps.get(key, [])
        timestamps = [t for t in timestamps if now - t < spam_seconds]
        timestamps.append(now)
        user_timestamps[key] = timestamps
        if len(timestamps) > spam_count:
            return True
    return False


def _parse_vision_json(content: str) -> Optional[Dict]:
    if not content:
        return None
    try:
        result = json.loads(content)
        if isinstance(result, dict):
            return result
    except Exception:
        pass
    m = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", content)
    if m:
        try:
            result = json.loads(m.group(1))
            if isinstance(result, dict):
                return result
        except Exception:
            pass
    m = re.search(r"\{[\s\S]*?\}", content)
    if m:
        try:
            result = json.loads(m.group(0))
            if isinstance(result, dict):
                return result
        except Exception:
            pass
    return None


async def recognize_emoji(image_url: str) -> Optional[Dict]:
    api_key = get_config("cloud_api_key", "") or ""
    base_url = (get_config("cloud_base_url", "") or "").rstrip("/")
    model = get_config("cloud_model", "") or ""
    is_local = "localhost" in base_url or "127.0.0.1" in base_url
    
    logger.info(f"[miku_ai] 调用识图API: model={model}, url={image_url[:80] if image_url else ''}")
    
    if not model:
        logger.warning("[miku_ai] 识图模型未配置")
        return None
    if not api_key and not is_local:
        logger.warning("[miku_ai] 识图 API Key 未配置且非本地模式")
        return None
    
    img_content = await _download_image_bytes(image_url)
    if img_content:
        image_data_uri = _image_bytes_to_data_uri(img_content)
        logger.info(f"[miku_ai] 图片下载成功，转 base64: {len(img_content)} bytes")
    else:
        image_data_uri = image_url
        logger.warning("[miku_ai] 图片下载失败，尝试直接传 URL")
    
    prompt = get_config("vision_prompt_template", "") or (
        "分析这张图片，按JSON返回：{\"is_emoji\":true/false,"
        "\"ocr_text\":\"图上文字\","
        "\"character\":\"角色名\","
        "\"emotion_tag\":\"嘲讽/震惊/可爱/无语/悲伤/兴奋/其他\","
        "\"description\":\"一句话描述\","
        "\"tags\":[\"标签1\",\"标签2\"]}"
    )
    
    url = base_url + "/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    
    try:
        body = {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": image_data_uri}},
                    ],
                }
            ],
            "temperature": 0.3,
            "max_tokens": 300,
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(url, headers=headers, json=body)
            if r.status_code != 200:
                logger.warning(f"[miku_ai] 识图模型失败 HTTP {r.status_code}: {r.text[:200]}")
                return None
            data = r.json()
            choices = data.get("choices", [])
            if not choices:
                return None
            content = _extract_content(choices[0])
            parsed = _parse_vision_json(content)
            if parsed:
                logger.info(f"[miku_ai] 表情包识别成功: {parsed.get('description','')[:60]}")
                return parsed
            else:
                logger.debug(f"[miku_ai] 识图返回解析失败: {content[:200]}")
    except Exception as e:
        logger.warning(f"[miku_ai] 识图模型异常: {e}")
    return None


def format_emoji_text(info: Dict) -> str:
    if not info:
        return ""
    is_emoji = info.get("is_emoji", False)
    ocr = info.get("ocr_text", "").strip()
    char = info.get("character", "").strip()
    emotion = info.get("emotion_tag", "").strip()
    desc = info.get("description", "").strip()
    
    if is_emoji:
        prefix = "发了一张表情包"
    else:
        prefix = "发了一张图片"
    
    detail_parts = []
    if char:
        detail_parts.append(f"角色:{char}")
    if emotion:
        detail_parts.append(f"情绪:{emotion}")
    if ocr:
        detail_parts.append(f"文字:{ocr}")
    if desc and not (char or emotion or ocr):
        detail_parts.append(f"内容:{desc}")
    
    if detail_parts:
        return f"[{prefix}|{'|'.join(detail_parts)}]"
    return f"[{prefix}]"
